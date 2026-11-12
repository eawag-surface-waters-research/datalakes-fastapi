"""
The fetch job, against the local test S3 server (`docker compose -f docker-compose.test.yml up -d`).
Each test writes its files to its own folder in the `public` bucket, which anonymous users can read, with the server's admin credentials.
"""

import os
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.enums import FileStatus, JobStatus, JobType, SourceBackend
from app.models import Dataset, File, Job, Source
from app.storage import is_safe_relative_path
from app.worker import enqueue

T0 = datetime(2026, 1, 1, tzinfo=UTC)


class Bucket:
  """A folder the test bucket which the test can change."""

  def __init__(self, endpoint: str):
    self.endpoint = endpoint
    self.prefix = f"fetch-tests/{uuid.uuid4().hex[:12]}"
    self.remote = f":s3,provider='Other',access_key_id='test-admin',secret_access_key='test-admin-secret',endpoint='{endpoint}':public/{self.prefix}"

  def _rclone(self, *args: str, input: bytes | None = None) -> None:
    subprocess.run(["rclone", "--config", "", *args], input=input, check=True, capture_output=True, env={"PATH": os.environ["PATH"]})

  def put(self, path: str, content: str) -> None:
    self._rclone("rcat", f"{self.remote}/{path}", input=content.encode())

  def remove(self, path: str) -> None:
    self._rclone("deletefile", f"{self.remote}/{path}")

  def purge(self) -> None:
    self._rclone("purge", self.remote)


@pytest.fixture
def bucket(s3_endpoint):
  bucket = Bucket(s3_endpoint)
  yield bucket
  try:
    bucket.purge()
  except subprocess.CalledProcessError:
    pass  # the test removed every file already


@pytest.fixture
def filesystem(tmp_path, monkeypatch) -> Path:
  monkeypatch.setenv("FILESYSTEM", str(tmp_path))
  return tmp_path


async def add_dataset(engine: AsyncEngine, bucket: Bucket | None, include: list[str] | None = None) -> int:
  """A dataset whose source is the bucket's folder."""
  async with AsyncSession(engine, expire_on_commit=False) as session:
    dataset = Dataset(title="Thermistor chain")
    session.add(dataset)
    await session.commit()
    assert dataset.id is not None
    if bucket is not None:
      options = {"bucket": "public", "endpoint": bucket.endpoint}
      session.add(
        Source(dataset_id=dataset.id, backend=SourceBackend.s3, options=options, path=bucket.prefix, include=include or [], last_tested_at=T0)
      )
      await session.commit()
    return dataset.id


async def fetch(engine: AsyncEngine, make_worker, dataset_id: int) -> Job:
  async with AsyncSession(engine, expire_on_commit=False) as session:
    job = await enqueue(session, JobType.fetch, dataset_id=dataset_id)
  await make_worker().run_once()
  async with AsyncSession(engine) as session:
    job = await session.get(Job, job.id)
  assert job is not None
  return job


async def index(engine: AsyncEngine, dataset_id: int) -> dict[str, File]:
  async with AsyncSession(engine) as session:
    files = (await session.exec(select(File).where(File.dataset_id == dataset_id))).all()
  return {file.path: file for file in files}


def archive(filesystem: Path, dataset_id: int) -> dict[str, str]:
  """The files in the dataset's archive, with their content."""
  root = filesystem / "raw" / str(dataset_id)
  return {str(path.relative_to(root)): path.read_text() for path in sorted(root.rglob("*")) if path.is_file()}


def result(**counts: int) -> dict[str, int]:
  return {"files_new": 0, "files_changed": 0, "files_removed": 0, "files_unchanged": 0, "bytes_copied": 0} | counts


async def test_first_fetch_copies_every_file(worker_engine, make_worker, bucket, filesystem):
  bucket.put("L2/2024/a.nc", "a" * 10)
  bucket.put("L2/b.nc", "b" * 5)
  dataset_id = await add_dataset(worker_engine, bucket)

  job = await fetch(worker_engine, make_worker, dataset_id)
  assert job.status == JobStatus.succeeded, job.error
  assert job.result == result(files_new=2, bytes_copied=15)
  assert job.progress == 1
  assert archive(filesystem, dataset_id) == {"L2/2024/a.nc": "a" * 10, "L2/b.nc": "b" * 5}

  files = await index(worker_engine, dataset_id)
  assert set(files) == {"L2/2024/a.nc", "L2/b.nc"}
  assert files["L2/2024/a.nc"].size == 10
  assert files["L2/2024/a.nc"].status == FileStatus.new
  assert files["L2/2024/a.nc"].modified_at > T0

  async with AsyncSession(worker_engine) as session:
    source = (await session.exec(select(Source).where(Source.dataset_id == dataset_id))).one()
  assert source.last_tested_at > T0


async def test_second_fetch_copies_nothing(worker_engine, make_worker, bucket, filesystem):
  bucket.put("a.nc", "a")
  dataset_id = await add_dataset(worker_engine, bucket)
  await fetch(worker_engine, make_worker, dataset_id)
  copied_at = (filesystem / "raw" / str(dataset_id) / "a.nc").stat().st_ctime_ns

  job = await fetch(worker_engine, make_worker, dataset_id)
  assert job.status == JobStatus.succeeded, job.error
  assert job.result == result(files_unchanged=1)
  assert (filesystem / "raw" / str(dataset_id) / "a.nc").stat().st_ctime_ns == copied_at


async def test_added_file_is_the_only_one_copied(worker_engine, make_worker, bucket, filesystem):
  bucket.put("a.nc", "a")
  dataset_id = await add_dataset(worker_engine, bucket)
  await fetch(worker_engine, make_worker, dataset_id)

  bucket.put("b.nc", "bb")
  job = await fetch(worker_engine, make_worker, dataset_id)
  assert job.result == result(files_new=1, files_unchanged=1, bytes_copied=2)
  assert archive(filesystem, dataset_id) == {"a.nc": "a", "b.nc": "bb"}


async def test_changed_file_is_copied_again(worker_engine, make_worker, bucket, filesystem):
  bucket.put("a.nc", "old")
  bucket.put("b.nc", "b")
  dataset_id = await add_dataset(worker_engine, bucket)
  await fetch(worker_engine, make_worker, dataset_id)

  # as if a build had processed the file
  async with AsyncSession(worker_engine) as session:
    file = (await session.exec(select(File).where(File.path == "a.nc"))).one()
    file.status, file.time_start, file.time_end, file.error = FileStatus.ok, T0, T0, "an old error"
    session.add(file)
    await session.commit()

  bucket.put("a.nc", "changed")
  job = await fetch(worker_engine, make_worker, dataset_id)
  assert job.result == result(files_changed=1, files_unchanged=1, bytes_copied=7)
  assert archive(filesystem, dataset_id) == {"a.nc": "changed", "b.nc": "b"}

  file = (await index(worker_engine, dataset_id))["a.nc"]
  assert file.size == 7
  assert file.status == FileStatus.new  # to be processed again
  assert file.error is None
  assert file.time_start == T0  # kept until the build reads the new file, so it knows which data the file replaces


async def test_removed_file_is_deleted(worker_engine, make_worker, bucket, filesystem):
  bucket.put("a.nc", "a")
  bucket.put("L2/2024/b.nc", "b")
  dataset_id = await add_dataset(worker_engine, bucket)
  await fetch(worker_engine, make_worker, dataset_id)

  bucket.remove("L2/2024/b.nc")
  job = await fetch(worker_engine, make_worker, dataset_id)
  assert job.result == result(files_removed=1, files_unchanged=1)
  assert archive(filesystem, dataset_id) == {"a.nc": "a"}
  assert not (filesystem / "raw" / str(dataset_id) / "L2").exists()  # the folders it left empty are gone too
  assert set(await index(worker_engine, dataset_id)) == {"a.nc"}


async def test_file_no_longer_included_is_removed(worker_engine, make_worker, bucket, filesystem):
  bucket.put("a.nc", "a")
  bucket.put("README.md", "readme")
  dataset_id = await add_dataset(worker_engine, bucket)
  await fetch(worker_engine, make_worker, dataset_id)

  async with AsyncSession(worker_engine) as session:
    source = (await session.exec(select(Source).where(Source.dataset_id == dataset_id))).one()
    source.include = ["*.nc"]
    session.add(source)
    await session.commit()

  job = await fetch(worker_engine, make_worker, dataset_id)
  assert job.result == result(files_removed=1, files_unchanged=1)
  assert archive(filesystem, dataset_id) == {"a.nc": "a"}


async def test_emptied_source_removes_nothing(worker_engine, make_worker, bucket, filesystem):
  bucket.put("a.nc", "a")
  dataset_id = await add_dataset(worker_engine, bucket)
  await fetch(worker_engine, make_worker, dataset_id)

  bucket.remove("a.nc")
  job = await fetch(worker_engine, make_worker, dataset_id)
  assert job.status == JobStatus.failed
  assert job.error is not None and job.error.startswith("The source has no files, but the archive has 1 files.")
  assert archive(filesystem, dataset_id) == {"a.nc": "a"}
  assert set(await index(worker_engine, dataset_id)) == {"a.nc"}


async def test_empty_source_is_fine_for_an_empty_archive(worker_engine, make_worker, bucket, filesystem):
  dataset_id = await add_dataset(worker_engine, bucket)
  job = await fetch(worker_engine, make_worker, dataset_id)
  assert job.status == JobStatus.succeeded, job.error
  assert job.result == result()


async def test_unreachable_source_removes_nothing(worker_engine, make_worker, bucket, filesystem):
  bucket.put("a.nc", "a")
  dataset_id = await add_dataset(worker_engine, bucket)
  await fetch(worker_engine, make_worker, dataset_id)

  async with AsyncSession(worker_engine) as session:
    source = (await session.exec(select(Source).where(Source.dataset_id == dataset_id))).one()
    source.options = {"bucket": "public", "endpoint": "http://127.0.0.1:1"}  # nothing listens on port 1
    session.add(source)
    await session.commit()

  job = await fetch(worker_engine, make_worker, dataset_id)
  assert job.status == JobStatus.failed
  assert job.error is not None and job.error.startswith("Couldn't list the source, nothing was changed: Couldn't connect to the source: ")
  assert archive(filesystem, dataset_id) == {"a.nc": "a"}
  assert set(await index(worker_engine, dataset_id)) == {"a.nc"}


async def test_fetch_without_source_fails(worker_engine, make_worker, filesystem):
  dataset_id = await add_dataset(worker_engine, None)
  job = await fetch(worker_engine, make_worker, dataset_id)
  assert job.status == JobStatus.failed
  assert job.error == f"Dataset {dataset_id} has no source"


@pytest.mark.parametrize(
  ("path", "safe"),
  [
    ("a.nc", True),
    ("L2/2024/a.nc", True),
    ("..a.nc", True),
    ("../a.nc", False),
    ("L2/../../a.nc", False),
    ("/etc/passwd", False),
    ("L2//a.nc", False),
    ("L2/./a.nc", False),
    ("L2\\a.nc", False),
    ("", False),
  ],
)
def test_paths_that_lead_outside_the_archive_are_unsafe(path, safe):
  assert is_safe_relative_path(path) is safe
