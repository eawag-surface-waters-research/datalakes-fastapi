"""
The job queue is the `jobs` table.

Claiming: a worker takes the oldest queued job. Two workers never take the same job.

Ownership: while a job runs, the worker holds a Postgres advisory lock on it.
Postgres releases the lock when that connection closes, so when a worker dies (crash, out of memory, etc.) its jobs are
left `running` but unlocked. `recover_orphans` finds them and queues them again, up to `MAX_RESTARTS` times (to prevent infinite loops).
The lock is taken before the job is marked `running`, and released after it has ended, so a job that's `running` and unlocked always belongs to a worker that has gone.
"""

import logging
from typing import Any

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlmodel import col, select, update
from sqlmodel.ext.asyncio.session import AsyncSession

from app.enums import JobStatus, JobType
from app.models import Job

logger = logging.getLogger(__name__)

JOB_LOCK_CLASS = 1  # first key of the pg advisory locks on jobs. Can be any 32-bit integer. The second key is the job ID.
MAX_RESTARTS = 2  # how often a job is queued again after its worker died, before it's failed
LOG_MAX_LINES = 100  # lines of a job's log that are kept
LOG_MAX_LINE_LENGTH = 1000  # maximum length of a line in a job's log. Longer lines are truncated.


def append_log(log: str, *lines: str) -> str:
  """The log with the lines added at the end, keeping the last `LOG_MAX_LINES` lines."""
  added = [line[:LOG_MAX_LINE_LENGTH] for text in lines for line in (text.splitlines() or [""])]
  return "\n".join([*log.splitlines(), *added][-LOG_MAX_LINES:])


async def enqueue(session: AsyncSession, type: JobType, *, dataset_id: int | None = None, params: dict[str, Any] | None = None) -> Job:
  """Queues a job"""
  job = Job(type=type, dataset_id=dataset_id, params=params or {})
  session.add(job)
  await session.commit()
  await session.refresh(job)
  return job


async def update_job(conn: AsyncConnection, job_id: int, *, log: str | None = None, **values: Any) -> None:
  """Updates a job's columns, adds a line to its log, and commits."""
  if log is not None:
    current = (await conn.execute(select(Job.log).where(col(Job.id) == job_id).with_for_update())).scalar_one()
    values["log"] = append_log(current, log)
  await conn.execute(update(Job).where(col(Job.id) == job_id).values(**values))
  await conn.commit()


async def claim(engine: AsyncEngine) -> tuple[Job, AsyncConnection] | None:
  """
  Takes the oldest queued job, locks it and marks it `running`.
  Returns the job and the connection that holds the lock, which the caller closes once the job has ended. `None` if no job is queued.
  """
  conn = await engine.connect()
  job: Job | None = None
  try:
    async with conn.begin():
      # find the oldest queued job
      next_job_query = (
        select(Job.id).where(Job.status == JobStatus.queued).order_by(col(Job.created_at), col(Job.id)).limit(1).with_for_update(skip_locked=True)
      )
      job_id = (await conn.execute(next_job_query)).scalar()

      # a session-level lock, held until `conn` closes. Fails only while `recover_orphans` checks the job. It's then taken at the next try.
      if (
        job_id is not None and (await conn.execute(select(func.pg_try_advisory_lock(JOB_LOCK_CLASS, job_id)))).scalar_one()
      ):  # `pg_try_advisory_lock` is true if the lock is free
        # mark the job `running`
        mark_running_statement = update(Job).where(col(Job.id) == job_id).values(status=JobStatus.running, started_at=func.now()).returning(Job)
        job = Job(**(await conn.execute(mark_running_statement)).one()._mapping)
  except BaseException:
    await conn.close()
    raise

  if job is None:
    # no job was queued or its lock couldn't be taken
    await conn.close()
    return None

  # the job is now `running` and locked. The caller must close `conn` when the job has ended to release the lock
  return job, conn


async def recover_orphans(engine: AsyncEngine) -> list[int]:
  """
  Finds the running jobs whose worker has gone (no lock held) and queues them again from the start.
  A job is failed instead after `MAX_RESTARTS` restarts, and cancelled if a user had asked to cancel it.
  Returns the IDs of the jobs changed.
  """
  async with engine.begin() as conn:
    # A running job whose lock can be taken has no worker anymore.
    # These are transaction-level locks, released at the commit below.
    orphans_query = (
      select(Job.id, Job.restarts, Job.cancel_requested_at, Job.log)
      .where(
        Job.status == JobStatus.running, func.pg_try_advisory_xact_lock(JOB_LOCK_CLASS, Job.id)
      )  # `pg_try_advisory_xact_lock` is true if the lock is free
      .with_for_update()  # lock the rows so another worker doesn't take them at the same time
    )
    orphans = (await conn.execute(orphans_query)).all()

    for job in orphans:
      if job.cancel_requested_at is not None:
        # a user asked to cancel the job while its worker was running it
        logger.warning("Job %s: its worker stopped while cancelling it. Marking it cancelled", job.id)
        line = "The worker stopped while cancelling the job"
        update_values = {"status": JobStatus.cancelled, "finished_at": func.now()}
      elif job.restarts >= MAX_RESTARTS:
        # maximum restarts reached
        logger.warning("Job %s: its worker stopped %s times while running it. Giving up", job.id, job.restarts + 1)
        line = (
          f"The worker stopped unexpectedly {job.restarts + 1} times while running this job, e.g. because it ran out of memory. It won't be retried."
        )
        update_values = {"status": JobStatus.failed, "error": line, "finished_at": func.now()}
      else:
        # the job is queued again to run from the start
        logger.warning("Job %s: its worker stopped while running it. Queuing it again", job.id)
        line = "The worker stopped unexpectedly while running the job. Queued again, to run from the start"
        update_values = {"status": JobStatus.queued, "restarts": job.restarts + 1, "started_at": None, "progress": None}

      await conn.execute(update(Job).where(col(Job.id) == job.id).values(log=append_log(job.log, line), **update_values))

  return [job.id for job in orphans]
