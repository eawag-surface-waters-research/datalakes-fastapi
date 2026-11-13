"""The cleanup job, which deletes the files of a deleted dataset."""

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api import app
from app.database import get_session
from app.enums import JobStatus, JobType
from app.models import Dataset, Job
from app.worker import enqueue


@pytest.fixture
def filesystem(tmp_path, monkeypatch) -> Path:
  monkeypatch.setenv("FILESYSTEM", str(tmp_path))
  return tmp_path


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


def write_archive(filesystem: Path, dataset_id: int) -> Path:
  archive = filesystem / "raw" / str(dataset_id)
  (archive / "L2" / "2024").mkdir(parents=True)
  (archive / "L2" / "2024" / "a.nc").write_text("a")
  (archive / "L2" / "b.nc").write_text("b")
  (archive / "c.nc").write_text("c")
  return archive


async def run_cleanup(engine: AsyncEngine, make_worker, **params) -> Job:
  async with AsyncSession(engine, expire_on_commit=False) as session:
    job = await enqueue(session, JobType.cleanup, params=params)
  await make_worker().run_once()
  async with AsyncSession(engine) as session:
    job = await session.get(Job, job.id)
  assert job is not None
  return job


async def test_deleting_a_dataset_deletes_its_files(worker_engine, worker_client, make_worker, filesystem):
  dataset_id = (await worker_client.post("/datasets", json={"title": "Thermistor chain"})).json()["id"]
  other_id = (await worker_client.post("/datasets", json={"title": "Other"})).json()["id"]
  archive = write_archive(filesystem, dataset_id)
  other_archive = write_archive(filesystem, other_id)

  assert (await worker_client.delete(f"/datasets/{dataset_id}")).status_code == 204
  assert archive.exists()  # the API doesn't touch the files, the worker does

  await make_worker().run_once()
  async with AsyncSession(worker_engine) as session:
    job = (await session.exec(select(Job).where(Job.type == JobType.cleanup))).one()
  assert job.status == JobStatus.succeeded
  assert job.result == {"files_deleted": 3}
  assert not archive.exists()
  assert (filesystem / "raw").is_dir()
  assert sorted(path.name for path in other_archive.rglob("*.nc")) == ["a.nc", "b.nc", "c.nc"]  # other datasets keep their files


async def test_cleanup_without_files_succeeds(worker_engine, make_worker, filesystem):
  job = await run_cleanup(worker_engine, make_worker, dataset_id=123)
  assert job.status == JobStatus.succeeded
  assert job.result == {"files_deleted": 0}


async def test_cleanup_of_an_existing_dataset_fails_and_deletes_nothing(worker_engine, make_worker, filesystem):
  async with AsyncSession(worker_engine, expire_on_commit=False) as session:
    dataset = Dataset(title="Still here")
    session.add(dataset)
    await session.commit()
  assert dataset.id is not None
  archive = write_archive(filesystem, dataset.id)

  job = await run_cleanup(worker_engine, make_worker, dataset_id=dataset.id)
  assert job.status == JobStatus.failed
  assert job.error == f"Dataset {dataset.id} exists, so its files weren't deleted"
  assert (archive / "c.nc").exists()
