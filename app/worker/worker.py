"""
A worker that runs queued jobs.
"""

import asyncio
import logging
import signal
from contextlib import suppress

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import select

from app.database import DATABASE_URL, get_safe_db_url
from app.enums import JobStatus, JobType
from app.models import Job
from app.worker.context import JobContext, JobError
from app.worker.handlers import HANDLERS_BY_TYPE, Handler
from app.worker.queue import claim, recover_orphans, update_job

logger = logging.getLogger(__name__)


class Worker:
  """
  Runs queued jobs one at a time until `stop()` is called.
  """

  def __init__(
    self,
    engine: AsyncEngine,
    *,
    handlers: dict[JobType, Handler] | None = None,
    poll_interval: float = 2.0,
    recover_interval: float = 60.0,
  ):
    self.engine: AsyncEngine = engine
    self.handlers: dict[JobType, Handler] = HANDLERS_BY_TYPE if handlers is None else handlers
    self.poll_interval: float = poll_interval
    self.recover_interval: float = recover_interval
    self._stopping = asyncio.Event()
    self._work_task: asyncio.Task | None = None  # the running handler

  def stop(self) -> None:
    """Stops taking jobs and stops the running one. It's queued again to run from the start."""
    self._stopping.set()
    if self._work_task is not None:
      self._work_task.cancel()

  async def run(self) -> None:
    """Runs jobs until `stop()` is called. Database errors are logged and retried, so the worker survives a database restart."""
    loop = asyncio.get_running_loop()
    next_recovery = loop.time()

    while not self._stopping.is_set():
      try:
        # recover orphaned jobs every `recover_interval` seconds, in case a worker died while running a job
        if loop.time() >= next_recovery:
          await recover_orphans(self.engine)
          next_recovery = loop.time() + self.recover_interval

        if await self.run_once() is not None:
          continue  # a job ran: look for the next one right away
      except Exception:
        logger.exception("Couldn't take jobs from the queue. Trying again in %g s", self.poll_interval)

      # no job was queued: wait until the poll interval is over, or the worker is stopping
      with suppress(TimeoutError):
        await asyncio.wait_for(self._stopping.wait(), timeout=self.poll_interval)

  async def run_once(self) -> int | None:
    """Runs the oldest queued job to its end. Returns its ID, or `None` if no job was queued."""
    claimed = await claim(self.engine)
    if claimed is None:
      return None

    (job, conn) = claimed
    assert job.id is not None
    logger.info("Job %s: claimed and marked running", job.id)

    await self._execute(job, conn)
    return job.id

  async def _execute(self, job: Job, conn: AsyncConnection) -> None:
    """Runs a claimed job and records how it ended. `conn` holds the job's lock and is closed at the end, which releases it."""
    assert job.id is not None
    job_id = job.id
    context: JobContext | None = None
    work_task: asyncio.Task | None = None
    try:
      handler = self.handlers.get(job.type)
      if handler is None:
        error = f"The worker can't run jobs of type {job.type} yet"
        await update_job(conn, job_id, log=error, status=JobStatus.failed, error=error, finished_at=sa.func.now())
        return

      logger.info("Job %s: running %s job", job_id, job.type)

      context = JobContext(self.engine, job)
      work_task = self._work_task = asyncio.create_task(handler(context, job.params))

      if self._stopping.is_set():
        work_task.cancel()  # `stop()` was called while the job was being claimed

      cancelled_by_user = await self._watch(job_id, conn, work_task)  # wait until the job's handler has ended

      if cancelled_by_user:
        logger.info("Job %s: cancelled", job_id)
        await update_job(conn, job_id, log="Cancelled while running", status=JobStatus.cancelled, finished_at=sa.func.now())
      elif work_task.cancelled():
        logger.info("Job %s: the worker is stopping. Queuing the job again", job_id)
        await update_job(
          conn, job_id, log="The worker stopped. Queued again, to run from the start", status=JobStatus.queued, started_at=None, progress=None
        )
      elif (exception := work_task.exception()) is not None:
        # the job's handler raised an exception. Mark the job failed, with the error message
        if isinstance(exception, JobError):
          error = str(exception)
          logger.info("Job %s: failed: %s", job_id, error)
        else:
          error = f"Unexpected error: {type(exception).__name__}: {exception}"
          logger.error("Job %s: unexpected error", job_id, exc_info=exception)
        await update_job(conn, job_id, log=f"Failed: {error}", status=JobStatus.failed, error=error, finished_at=sa.func.now())
      else:
        logger.info("Job %s: succeeded", job_id)
        await update_job(conn, job_id, status=JobStatus.succeeded, progress=1.0, result=work_task.result(), finished_at=sa.func.now())
    except Exception:
      # e.g. the connection that holds the job's lock failed. The job stays `running`, and once its lock is released below, `recover_orphans` queues it again
      logger.exception("Job %s: couldn't run it or record how it ended", job_id)
    finally:
      if work_task is not None and not work_task.done():
        work_task.cancel()  # never leave a handler running without its lock: another worker may take the job
        await asyncio.gather(work_task, return_exceptions=True)
      self._work_task = None
      if context is not None:
        await context.close()
      await conn.close()  # also releases the job's lock

  async def _watch(self, job_id: int, conn: AsyncConnection, work: asyncio.Task) -> bool:
    """
    Waits until the job's handler has ended, and stops it if a user asks to cancel the job. Returns whether a user cancelled it.
    Raises if the connection that holds the job's lock fails.
    """
    while not work.done():
      await asyncio.wait({work}, timeout=self.poll_interval)
      if work.done():
        break

      cancel_requested_at = (await conn.execute(select(Job.cancel_requested_at).where(Job.id == job_id))).scalar_one()
      await conn.commit()

      # a user asked to cancel the job => stop the handler and mark the job cancelled
      if cancel_requested_at is not None:
        work.cancel()
        await asyncio.gather(work, return_exceptions=True)
        return True
    return False


def worker_engine() -> AsyncEngine:
  # No pool: closing a job's connection must end its database session, which releases the job's lock
  return create_async_engine(DATABASE_URL, poolclass=NullPool)


async def run_worker(poll_interval: float) -> None:
  engine = worker_engine()
  worker = Worker(engine, poll_interval=poll_interval)

  loop = asyncio.get_running_loop()
  loop.add_signal_handler(signal.SIGTERM, worker.stop)
  loop.add_signal_handler(signal.SIGINT, worker.stop)

  logger.info("Worker started, database %s", get_safe_db_url(DATABASE_URL))

  try:
    await worker.run()
  finally:
    await engine.dispose()
    logger.info("Worker stopped")
