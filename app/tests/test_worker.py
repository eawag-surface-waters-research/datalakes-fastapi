import asyncio
import random
import uuid
from collections.abc import AsyncGenerator

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel, col
from sqlmodel.ext.asyncio.session import AsyncSession

import app.models
from app.api import app
from app.database import DATABASE_URL, engine, get_session
from app.enums import JobStatus, JobType
from app.models import Job
from app.worker import JobContext, Worker, enqueue
from app.worker.handlers import Handler, NoParams
from app.worker.queue import MAX_RESTARTS, claim, recover_orphans


@pytest.fixture
async def worker_engine() -> AsyncGenerator[AsyncEngine]:
  """
  The worker uses several connections at once, so these tests can't run inside one rolled-back transaction like the API tests.
  Each test gets its own committed schema instead, dropped at the end.
  """
  schema = f"test_worker_{uuid.uuid4().hex[:12]}"
  async with engine.begin() as conn:
    await conn.execute(sa.text(f"CREATE SCHEMA {schema}"))
  test_engine = create_async_engine(DATABASE_URL, poolclass=NullPool, connect_args={"server_settings": {"search_path": schema}})

  try:
    async with test_engine.begin() as conn:
      await conn.run_sync(SQLModel.metadata.create_all)
    yield test_engine
  finally:
    await test_engine.dispose()
    async with engine.begin() as conn:
      await conn.execute(sa.text(f"DROP SCHEMA {schema} CASCADE"))


@pytest.fixture(autouse=True)
def lock_class(monkeypatch) -> int:
  """Advisory locks are shared by the whole database, so each test uses its own, to never clash with a worker running on the dev database."""
  value = random.randint(1000, 2**31 - 1)
  monkeypatch.setattr("app.worker.queue.JOB_LOCK_CLASS", value)
  return value


@pytest.fixture
async def make_worker(worker_engine):
  workers: list[Worker] = []

  def make(**options) -> Worker:
    worker = Worker(worker_engine, poll_interval=0.05, **options)
    workers.append(worker)
    return worker

  yield make
  for worker in workers:
    worker.stop()


@pytest.fixture
async def worker_client(worker_engine) -> AsyncGenerator[AsyncClient]:
  """An API client on the same committed schema as the worker."""

  async def session():
    async with AsyncSession(worker_engine) as session:
      yield session

  app.dependency_overrides[get_session] = session
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
    yield client
  app.dependency_overrides.clear()


async def queue(engine: AsyncEngine, type: JobType = JobType.test, **params) -> int:
  async with AsyncSession(engine, expire_on_commit=False) as session:
    job = await enqueue(session, type, params=params)
  assert job.id is not None
  return job.id


async def get_job(engine: AsyncEngine, job_id: int) -> Job:
  async with AsyncSession(engine) as session:
    job = await session.get(Job, job_id)
  assert job is not None
  return job


async def wait_for(engine: AsyncEngine, job_id: int, status: JobStatus, timeout: float = 5) -> Job:
  async with asyncio.timeout(timeout):
    while (job := await get_job(engine, job_id)).status != status:
      await asyncio.sleep(0.02)
  return job


async def test_job_goes_from_queued_to_running_to_succeeded(worker_engine, make_worker):
  job_id = await queue(worker_engine, seconds=0.3)
  assert (await get_job(worker_engine, job_id)).status == JobStatus.queued

  run = asyncio.create_task(make_worker().run_once())
  running = await wait_for(worker_engine, job_id, JobStatus.running)
  assert running.started_at is not None
  assert running.finished_at is None
  assert await run == job_id

  job = await get_job(worker_engine, job_id)
  assert job.status == JobStatus.succeeded
  assert job.result == {"waited_seconds": 0.3}
  assert job.progress == 1
  assert job.error is None
  assert job.log == "Waiting 0.3 s\nDone"
  assert job.finished_at is not None and job.started_at is not None and job.finished_at >= job.started_at


async def test_job_failing_with_job_error(worker_engine, make_worker):
  job_id = await queue(worker_engine, fail=True)
  await make_worker().run_once()
  job = await get_job(worker_engine, job_id)
  assert job.status == JobStatus.failed
  assert job.error == "The test job failed, as asked by its `fail` parameter"
  assert job.log.endswith("Failed: The test job failed, as asked by its `fail` parameter")
  assert job.result is None
  assert job.finished_at is not None


class BrokenHandler(Handler[NoParams]):
  type = JobType.test
  params_model = NoParams

  async def run(self, context: JobContext, params: NoParams) -> None:
    raise ValueError("boom")


async def test_job_failing_unexpectedly(worker_engine, make_worker):
  job_id = await queue(worker_engine)
  await make_worker(handlers={JobType.test: BrokenHandler()}).run_once()
  job = await get_job(worker_engine, job_id)
  assert job.status == JobStatus.failed
  assert job.error == "Unexpected error: ValueError: boom"


async def test_fetch_job_without_dataset_fails(worker_engine, make_worker):
  job_id = await queue(worker_engine, JobType.fetch)
  await make_worker().run_once()
  job = await get_job(worker_engine, job_id)
  assert job.status == JobStatus.failed
  assert job.error == "A fetch job needs a dataset"


async def test_job_without_handler_fails(worker_engine, make_worker):
  job_id = await queue(worker_engine, JobType.export)
  await make_worker().run_once()
  job = await get_job(worker_engine, job_id)
  assert job.status == JobStatus.failed
  assert job.error == "The worker can't run jobs of type export yet"


class StepsParams(BaseModel):
  steps: int


class StepsResult(BaseModel):
  steps_done: int


async def test_handler_gets_its_params_and_reports_progress(worker_engine, make_worker):
  seen: list[float | None] = []

  class StepsHandler(Handler[StepsParams]):
    type = JobType.test
    params_model = StepsParams

    async def run(self, context: JobContext, params: StepsParams) -> StepsResult:
      for i in range(params.steps):
        await context.log(f"step {i}")
        await context.progress(i / 2)
        seen.append((await get_job(worker_engine, context.job_id)).progress)
      await context.progress(7)  # clamped
      seen.append((await get_job(worker_engine, context.job_id)).progress)
      return StepsResult(steps_done=params.steps)

  job_id = await queue(worker_engine, steps=3)
  await make_worker(handlers={JobType.test: StepsHandler()}).run_once()
  job = await get_job(worker_engine, job_id)
  assert seen == [0, 0.5, 1, 1]
  assert job.log == "step 0\nstep 1\nstep 2"
  assert job.status == JobStatus.succeeded
  assert job.result == {"steps_done": 3}


async def test_handler_without_result(worker_engine, make_worker):
  class QuietHandler(Handler[NoParams]):
    type = JobType.test
    params_model = NoParams

    async def run(self, context: JobContext, params: NoParams) -> None:
      return None

  job_id = await queue(worker_engine)
  await make_worker(handlers={JobType.test: QuietHandler()}).run_once()
  job = await get_job(worker_engine, job_id)
  assert job.status == JobStatus.succeeded
  assert job.result is None


async def test_oldest_job_runs_first(worker_engine, make_worker):
  first = await queue(worker_engine)
  second = await queue(worker_engine)
  worker = make_worker()
  assert await worker.run_once() == first
  assert await worker.run_once() == second


async def test_two_workers_never_take_the_same_job(worker_engine, make_worker):
  job_id = await queue(worker_engine, seconds=30)
  worker_a, worker_b = make_worker(), make_worker()
  run_a = asyncio.create_task(worker_a.run_once())
  await wait_for(worker_engine, job_id, JobStatus.running)
  assert await worker_b.run_once() is None
  worker_a.stop()
  await run_a


async def test_cancel_running_job(worker_engine, make_worker, worker_client):
  job_id = await queue(worker_engine, seconds=30)
  run = asyncio.create_task(make_worker().run_once())
  await wait_for(worker_engine, job_id, JobStatus.running)

  response = await worker_client.post(f"/jobs/{job_id}/cancel")
  assert response.status_code == 200
  assert response.json()["status"] == "running"

  async with asyncio.timeout(5):  # long before the job's 30 s
    await run
  job = await get_job(worker_engine, job_id)
  assert job.status == JobStatus.cancelled
  assert job.finished_at is not None
  assert job.log.endswith("Cancelled while running")


async def test_cancelled_queued_job_never_runs(worker_engine, make_worker, worker_client):
  job_id = await queue(worker_engine)
  response = await worker_client.post(f"/jobs/{job_id}/cancel")
  assert response.json()["status"] == "cancelled"
  assert await make_worker().run_once() is None
  assert (await get_job(worker_engine, job_id)).started_at is None


async def test_stopping_the_worker_queues_its_jobs_again(worker_engine, make_worker):
  job_id = await queue(worker_engine, seconds=0.5)
  worker = make_worker()
  run = asyncio.create_task(worker.run())
  await wait_for(worker_engine, job_id, JobStatus.running)
  worker.stop()
  async with asyncio.timeout(5):
    await run

  job = await get_job(worker_engine, job_id)
  assert job.status == JobStatus.queued
  assert job.started_at is None
  assert job.progress is None
  assert job.restarts == 0  # a clean stop isn't counted against the job
  assert "The worker stopped. Queued again" in job.log

  assert await make_worker().run_once() == job_id
  assert (await get_job(worker_engine, job_id)).status == JobStatus.succeeded


async def dead_worker_job(engine: AsyncEngine, **values) -> int:
  """A job left `running` by a worker that died: claimed, then its connection closed without recording an end."""
  job_id = await queue(engine)
  claimed = await claim(engine)
  assert claimed is not None and claimed[0].id == job_id
  if values:
    async with engine.begin() as conn:
      await conn.execute(sa.update(Job).where(col(Job.id) == job_id).values(**values))
  await claimed[1].close()
  return job_id


async def test_job_of_dead_worker_is_queued_again(worker_engine):
  job_id = await dead_worker_job(worker_engine)
  assert await recover_orphans(worker_engine) == [job_id]
  job = await get_job(worker_engine, job_id)
  assert job.status == JobStatus.queued
  assert job.restarts == 1
  assert job.started_at is None
  assert "The worker stopped unexpectedly" in job.log


async def test_restarted_worker_finishes_the_job_of_the_dead_one(worker_engine, make_worker):
  job_id = await dead_worker_job(worker_engine)
  worker = make_worker()
  run = asyncio.create_task(worker.run())
  job = await wait_for(worker_engine, job_id, JobStatus.succeeded)
  assert job.restarts == 1
  worker.stop()
  await run


async def test_job_of_live_worker_is_not_recovered(worker_engine):
  job_id = await queue(worker_engine)
  claimed = await claim(worker_engine)
  assert claimed is not None
  try:
    assert await recover_orphans(worker_engine) == []
    assert (await get_job(worker_engine, job_id)).status == JobStatus.running
  finally:
    await claimed[1].close()


async def test_job_that_keeps_killing_the_worker_fails(worker_engine):
  job_id = await dead_worker_job(worker_engine, restarts=MAX_RESTARTS)
  assert await recover_orphans(worker_engine) == [job_id]
  job = await get_job(worker_engine, job_id)
  assert job.status == JobStatus.failed
  assert job.finished_at is not None
