import subprocess
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
import xarray as xr
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.enums import FileStatus, JobStatus, JobType, VariableRole
from app.models import Dataset, Job, Partition, Variable
from app.tests.test_fetch import Bucket, add_dataset, fetch, index, result


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
  monkeypatch.setenv("FILESYSTEM", str(tmp_path / "filesystem"))
  return tmp_path / "filesystem"


UNIX_UNITS = "seconds since 1970-01-01 00:00:00"
START = 1_700_000_000  # 2023-11-14 22:13:20 UTC
START_2024 = 1_710_000_000  # 2024-03-09 16:00:00 UTC


def netcdf(tmp_path: Path, start: int = START, temp_units: str = "degC", temp_name: str = "temp", steps: int = 2) -> bytes:
  """A small time series file with a value every 10 minutes, as bytes to upload."""
  ds = xr.Dataset(
    {temp_name: ("time", np.linspace(10.5, 11.0, steps), {"units": temp_units})},
    coords={"time": ("time", start + 600 * np.arange(steps, dtype="float64"), {"units": UNIX_UNITS})},
  )
  path = tmp_path / f"{start}-{temp_units}-{temp_name}-{steps}.nc"
  ds.to_netcdf(path=path, engine="netcdf4", format="NETCDF4")
  return path.read_bytes()


async def add_variables(engine: AsyncEngine, dataset_id: int) -> None:
  async with AsyncSession(engine) as session:
    session.add(Variable(dataset_id=dataset_id, name="time", source_name="time", role=VariableRole.time, dimensions=["time"]))
    session.add(Variable(dataset_id=dataset_id, name="temperature", source_name="temp", role=VariableRole.data, dimensions=["time"], unit="degC"))
    await session.commit()


async def mark_built(engine: AsyncEngine, dataset_id: int) -> None:
  async with AsyncSession(engine) as session:
    dataset = await session.get(Dataset, dataset_id)
    assert dataset is not None
    dataset.built_at = datetime.now(UTC)
    session.add(dataset)
    await session.commit()


async def get_dataset(engine: AsyncEngine, dataset_id: int) -> Dataset:
  async with AsyncSession(engine) as session:
    dataset = await session.get(Dataset, dataset_id)
    assert dataset is not None
    return dataset


async def built_at(engine: AsyncEngine, dataset_id: int) -> datetime | None:
  return (await get_dataset(engine, dataset_id)).built_at


async def sync(engine: AsyncEngine, make_worker, dataset_id: int) -> Job:
  return await fetch(engine, make_worker, dataset_id, JobType.sync)


async def partitions(engine: AsyncEngine, dataset_id: int) -> dict[int, Partition]:
  async with AsyncSession(engine) as session:
    return {partition.year: partition for partition in (await session.exec(select(Partition).where(Partition.dataset_id == dataset_id))).all()}


def read_partition(filesystem: Path, dataset_id: int, partition: Partition) -> xr.Dataset:
  return xr.open_zarr(filesystem / "zarr" / str(dataset_id) / partition.path, consolidated=False).load()


def stores(filesystem: Path, dataset_id: int) -> list[str]:
  """Every store on disk, as `year/job_id`, including the replaced ones."""
  root = filesystem / "zarr" / str(dataset_id)
  return sorted(str(path.relative_to(root)) for path in root.glob("*/*")) if root.exists() else []


def archive_paths(filesystem: Path, dataset_id: int) -> list[str]:
  return [str(path.relative_to(filesystem / "raw" / str(dataset_id))) for path in (filesystem / "raw" / str(dataset_id)).iterdir()]


def sync_result(**counts) -> dict:
  return result() | {"files_checked": 0, "files_ignored": 0, "files_failed": [], "years_written": [], "years_removed": [], "version": 1} | counts


async def test_sync_writes_the_data(worker_engine, make_worker, bucket, filesystem, tmp_path):
  a, b = netcdf(tmp_path), netcdf(tmp_path, start=START + 86_400)
  bucket.put("a.nc", a)
  bucket.put("b.nc", b)
  dataset_id = await add_dataset(worker_engine, bucket)
  await add_variables(worker_engine, dataset_id)

  job = await sync(worker_engine, make_worker, dataset_id)
  assert job.status == JobStatus.succeeded, job.error
  assert job.result == sync_result(files_new=2, bytes_copied=len(a) + len(b), files_checked=2, years_written=[2023], version=2)
  assert job.log.startswith("Listing the source\n")
  assert "Checking 2 archived files against 2 variables" in job.log
  assert job.log.endswith("Writing 1 partitions: 2023\n2023: 4 time steps from 2 files\nSwitched to the new data, the dataset's version is now 2")
  assert set(archive_paths(filesystem, dataset_id)) == {"a.nc", "b.nc"}

  files = await index(worker_engine, dataset_id)
  assert {path: (file.status, file.error) for path, file in files.items()} == {"a.nc": (FileStatus.ok, None), "b.nc": (FileStatus.ok, None)}
  assert (files["a.nc"].time_start, files["a.nc"].time_end) == (
    datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC),
    datetime(2023, 11, 14, 22, 23, 20, tzinfo=UTC),
  )
  assert files["b.nc"].time_start == datetime(2023, 11, 15, 22, 13, 20, tzinfo=UTC)

  partition = (await partitions(worker_engine, dataset_id))[2023]
  assert (partition.path, partition.rows) == (f"2023/{job.id}", 4)
  assert (partition.time_start, partition.time_end) == (
    datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC),
    datetime(2023, 11, 15, 22, 23, 20, tzinfo=UTC),
  )
  assert read_partition(filesystem, dataset_id, partition)["temperature"].values.tolist() == [10.5, 11.0, 10.5, 11.0]
  dataset = await get_dataset(worker_engine, dataset_id)
  assert dataset.built_at is not None
  assert (dataset.time_start, dataset.time_end, dataset.version) == (partition.time_start, partition.time_end, 2)


async def test_sync_warns_about_unused_variables(worker_engine, make_worker, bucket, filesystem, tmp_path):
  bucket.put("a.nc", netcdf(tmp_path, temp_name="extra"))
  dataset_id = await add_dataset(worker_engine, bucket)
  async with AsyncSession(worker_engine) as session:
    session.add(Variable(dataset_id=dataset_id, name="time", source_name="time", role=VariableRole.time, dimensions=["time"]))
    await session.commit()

  job = await sync(worker_engine, make_worker, dataset_id)
  assert job.status == JobStatus.succeeded, job.error
  assert "Warning: a.nc: variables not used by the dataset: extra" in job.log


async def test_new_dataset_fails_on_one_bad_file(worker_engine, make_worker, bucket, filesystem, tmp_path):
  bucket.put("a.nc", netcdf(tmp_path))
  bucket.put("b.nc", netcdf(tmp_path, start=START + 86_400, temp_units="K"))
  bucket.put("c.nc", netcdf(tmp_path, start=START + 2 * 86_400, temp_units="K"))
  bucket.put("notes.txt", "not data")
  dataset_id = await add_dataset(worker_engine, bucket)
  await add_variables(worker_engine, dataset_id)

  job = await sync(worker_engine, make_worker, dataset_id)
  assert job.status == JobStatus.failed
  assert job.error == (
    "Nothing was built: 3 of 4 files failed the checks\n"
    "- 2 of 4 files: variable 'temp' (source of 'temperature') has units 'K', expected 'degC' (e.g. b.nc)\n"
    "- 1 of 4 files: not a NetCDF file (.nc) (notes.txt)"
  )
  assert job.result is not None
  assert job.result["files_checked"] == 4
  assert job.result["files_ignored"] == 0
  assert job.result["files_failed"] == [
    {"path": "b.nc", "error": "variable 'temp' (source of 'temperature') has units 'K', expected 'degC'"},
    {"path": "c.nc", "error": "variable 'temp' (source of 'temperature') has units 'K', expected 'degC'"},
    {"path": "notes.txt", "error": "not a NetCDF file (.nc)"},
  ]
  assert "Writing the data" not in job.log

  files = await index(worker_engine, dataset_id)
  assert {path: file.status for path, file in files.items()} == {
    "a.nc": FileStatus.new,
    "b.nc": FileStatus.failed,
    "c.nc": FileStatus.failed,
    "notes.txt": FileStatus.failed,
  }
  assert files["b.nc"].error == "variable 'temp' (source of 'temperature') has units 'K', expected 'degC'"
  assert files["a.nc"].error is None
  assert await built_at(worker_engine, dataset_id) is None
  assert await partitions(worker_engine, dataset_id) == {}
  assert stores(filesystem, dataset_id) == []


async def test_new_dataset_checks_every_file_again(worker_engine, make_worker, bucket, filesystem, tmp_path):
  bucket.put("a.nc", netcdf(tmp_path, temp_units="K"))
  dataset_id = await add_dataset(worker_engine, bucket)
  await add_variables(worker_engine, dataset_id)
  assert (await sync(worker_engine, make_worker, dataset_id)).status == JobStatus.failed

  # fixing the variable: the unchanged file is checked again, as the dataset hasn't been built yet, and its error goes away
  async with AsyncSession(worker_engine) as session:
    variable = (await session.exec(select(Variable).where(Variable.dataset_id == dataset_id, Variable.name == "temperature"))).one()
    variable.unit = "K"
    session.add(variable)
    await session.commit()

  job = await sync(worker_engine, make_worker, dataset_id)
  assert job.status == JobStatus.succeeded, job.error
  assert job.result == sync_result(files_unchanged=1, files_checked=1, years_written=[2023], version=2)
  file = (await index(worker_engine, dataset_id))["a.nc"]
  assert (file.status, file.error) == (FileStatus.ok, None)
  assert set(await partitions(worker_engine, dataset_id)) == {2023}


async def test_built_dataset_skips_bad_files_and_ignores_other_formats(worker_engine, make_worker, bucket, filesystem, tmp_path):
  dataset_id = await add_dataset(worker_engine, bucket)
  await add_variables(worker_engine, dataset_id)
  await mark_built(worker_engine, dataset_id)
  bucket.put("a.nc", netcdf(tmp_path))
  bucket.put("b.nc", netcdf(tmp_path, start=START + 86_400, temp_name="temperature"))
  bucket.put("notes.txt", "not data")

  job = await sync(worker_engine, make_worker, dataset_id)
  assert job.status == JobStatus.failed
  assert job.error == "1 of 3 new files failed the checks and were skipped\n- 1 of 3 files: no variable 'temp' (source of 'temperature') (b.nc)"
  assert job.result is not None
  assert (job.result["files_checked"], job.result["files_ignored"]) == (3, 1)
  assert job.result["files_failed"] == [{"path": "b.nc", "error": "no variable 'temp' (source of 'temperature')"}]
  assert (job.result["years_written"], job.result["version"]) == ([2023], 2)  # a.nc was written all the same
  assert "Ignored 1 files that aren't NetCDF, e.g. notes.txt" in job.log

  files = await index(worker_engine, dataset_id)
  assert {path: file.status for path, file in files.items()} == {"a.nc": FileStatus.ok, "b.nc": FileStatus.failed, "notes.txt": FileStatus.ignored}
  assert files["notes.txt"].error == "not a NetCDF file (.nc)"
  # the file that passed was written all the same
  assert read_partition(filesystem, dataset_id, (await partitions(worker_engine, dataset_id))[2023])["temperature"].size == 2


async def test_built_dataset_succeeds_with_only_ignored_files(worker_engine, make_worker, bucket, filesystem, tmp_path):
  dataset_id = await add_dataset(worker_engine, bucket)
  await add_variables(worker_engine, dataset_id)
  await mark_built(worker_engine, dataset_id)
  bucket.put("a.nc", netcdf(tmp_path))
  bucket.put("notes.txt", "not data")

  job = await sync(worker_engine, make_worker, dataset_id)
  assert job.status == JobStatus.succeeded, job.error
  assert job.result is not None
  assert (job.result["files_checked"], job.result["files_ignored"], job.result["files_failed"]) == (2, 1, [])


async def test_built_dataset_checks_only_new_files(worker_engine, make_worker, bucket, filesystem, tmp_path):
  dataset_id = await add_dataset(worker_engine, bucket)
  await add_variables(worker_engine, dataset_id)
  await mark_built(worker_engine, dataset_id)
  bucket.put("bad.nc", netcdf(tmp_path, temp_units="K"))
  assert (await sync(worker_engine, make_worker, dataset_id)).status == JobStatus.failed

  # the failed file isn't checked again until it changes in the source
  job = await sync(worker_engine, make_worker, dataset_id)
  assert job.status == JobStatus.succeeded, job.error
  assert job.result is not None and job.result["files_checked"] == 0

  fixed = netcdf(tmp_path, start=START + 86_400, steps=3)
  assert len(fixed) != len(netcdf(tmp_path, temp_units="K"))
  bucket.put("bad.nc", fixed)
  job = await sync(worker_engine, make_worker, dataset_id)
  assert job.status == JobStatus.succeeded, job.error
  assert job.result is not None and job.result["files_checked"] == 1
  assert (await index(worker_engine, dataset_id))["bad.nc"].status == FileStatus.ok
  assert (await partitions(worker_engine, dataset_id))[2023].rows == 3


async def test_sync_without_variables_fails(worker_engine, make_worker, bucket, filesystem, tmp_path):
  bucket.put("a.nc", netcdf(tmp_path))
  dataset_id = await add_dataset(worker_engine, bucket)
  job = await sync(worker_engine, make_worker, dataset_id)
  assert job.status == JobStatus.failed
  assert job.error == f"Dataset {dataset_id} has no variables. Fetch, inspect and create the variables, then rebuild it."
  assert set(await index(worker_engine, dataset_id)) == {"a.nc"}  # the fetch ran
  assert await built_at(worker_engine, dataset_id) is None


async def test_second_sync_processes_only_the_changes(worker_engine, make_worker, bucket, filesystem, tmp_path):
  a, b, c = netcdf(tmp_path), netcdf(tmp_path, start=START + 86_400), netcdf(tmp_path, start=START + 2 * 86_400)
  bucket.put("a.nc", a)
  bucket.put("b.nc", b)
  dataset_id = await add_dataset(worker_engine, bucket)
  await add_variables(worker_engine, dataset_id)
  first = await sync(worker_engine, make_worker, dataset_id)

  bucket.put("c.nc", c)
  bucket.remove("b.nc")
  job = await sync(worker_engine, make_worker, dataset_id)
  assert job.status == JobStatus.succeeded, job.error
  assert job.result == sync_result(
    files_new=1, files_removed=1, files_unchanged=1, bytes_copied=len(c), files_checked=1, years_written=[2023], version=3
  )
  assert sorted(archive_paths(filesystem, dataset_id)) == ["a.nc", "c.nc"]

  # 2023 is written again from a.nc and c.nc. The replaced store stays on disk
  partition = (await partitions(worker_engine, dataset_id))[2023]
  assert (partition.path, partition.time_end) == (f"2023/{job.id}", datetime(2023, 11, 16, 22, 23, 20, tzinfo=UTC))
  assert read_partition(filesystem, dataset_id, partition)["time"].size == 4
  assert stores(filesystem, dataset_id) == sorted([f"2023/{first.id}", f"2023/{job.id}"])


async def test_sync_writes_only_the_years_that_changed(worker_engine, make_worker, bucket, filesystem, tmp_path):
  bucket.put("a.nc", netcdf(tmp_path))
  dataset_id = await add_dataset(worker_engine, bucket)
  await add_variables(worker_engine, dataset_id)
  first = await sync(worker_engine, make_worker, dataset_id)

  bucket.put("b.nc", netcdf(tmp_path, start=START_2024))
  job = await sync(worker_engine, make_worker, dataset_id)
  assert job.status == JobStatus.succeeded, job.error
  assert "Writing 1 partitions: 2024" in job.log
  assert {year: partition.path for year, partition in (await partitions(worker_engine, dataset_id)).items()} == {
    2023: f"2023/{first.id}",
    2024: f"2024/{job.id}",
  }


async def test_removing_the_last_file_of_a_year_removes_its_partition(worker_engine, make_worker, bucket, filesystem, tmp_path):
  bucket.put("a.nc", netcdf(tmp_path))
  bucket.put("b.nc", netcdf(tmp_path, start=START_2024))
  dataset_id = await add_dataset(worker_engine, bucket)
  await add_variables(worker_engine, dataset_id)
  first = await sync(worker_engine, make_worker, dataset_id)

  bucket.remove("a.nc")
  job = await sync(worker_engine, make_worker, dataset_id)
  assert job.status == JobStatus.succeeded, job.error
  assert "2023: no data any more, the partition is removed" in job.log
  assert job.result is not None
  assert (job.result["years_written"], job.result["years_removed"], job.result["version"]) == ([], [2023], 3)
  assert set(await partitions(worker_engine, dataset_id)) == {2024}
  dataset = await get_dataset(worker_engine, dataset_id)
  assert dataset.time_start == datetime(2024, 3, 9, 16, tzinfo=UTC)  # the extent shrinks
  assert stores(filesystem, dataset_id) == [f"2023/{first.id}", f"2024/{first.id}"]  # nothing is deleted from disk yet


async def test_changed_file_moving_to_another_year(worker_engine, make_worker, bucket, filesystem, tmp_path):
  bucket.put("a.nc", netcdf(tmp_path))
  dataset_id = await add_dataset(worker_engine, bucket)
  await add_variables(worker_engine, dataset_id)
  await sync(worker_engine, make_worker, dataset_id)

  bucket.put("a.nc", netcdf(tmp_path, start=START_2024, steps=3))  # a different size, so it's changed
  job = await sync(worker_engine, make_worker, dataset_id)
  assert job.status == JobStatus.succeeded, job.error
  assert "Writing 2 partitions: 2023, 2024" in job.log  # the old year from the index, the new one from the check
  assert job.result is not None
  assert (job.result["years_written"], job.result["years_removed"]) == ([2024], [2023])
  assert {year: partition.rows for year, partition in (await partitions(worker_engine, dataset_id)).items()} == {2024: 3}
  dataset = await get_dataset(worker_engine, dataset_id)
  assert (dataset.time_start, dataset.time_end) == (datetime(2024, 3, 9, 16, tzinfo=UTC), datetime(2024, 3, 9, 16, 20, tzinfo=UTC))


async def test_sync_without_changes_keeps_the_version(worker_engine, make_worker, bucket, filesystem, tmp_path):
  bucket.put("a.nc", netcdf(tmp_path))
  dataset_id = await add_dataset(worker_engine, bucket)
  await add_variables(worker_engine, dataset_id)
  await sync(worker_engine, make_worker, dataset_id)
  first = await get_dataset(worker_engine, dataset_id)

  job = await sync(worker_engine, make_worker, dataset_id)
  assert job.status == JobStatus.succeeded, job.error
  assert job.result == sync_result(files_unchanged=1, version=2)
  assert "Switched" not in job.log
  dataset = await get_dataset(worker_engine, dataset_id)
  assert (dataset.version, dataset.built_at) == (2, first.built_at)


async def test_partition_that_cant_be_written_fails_and_switches_nothing(worker_engine, make_worker, bucket, filesystem, tmp_path):
  # temp is on a depth dimension, as the variable says, so the file passes the checks, but a timeSeries needs every variable on time
  ds = xr.Dataset(
    {"temp": ("depth", np.array([10.5, 11.0]), {"units": "degC"})},
    coords={"time": ("time", np.array([START], dtype="float64"), {"units": UNIX_UNITS})},
  )
  ds.to_netcdf(tmp_path / "a.nc")
  bucket.put("a.nc", (tmp_path / "a.nc").read_bytes())
  dataset_id = await add_dataset(worker_engine, bucket)
  async with AsyncSession(worker_engine) as session:
    session.add(Variable(dataset_id=dataset_id, name="time", source_name="time", role=VariableRole.time, dimensions=["time"]))
    session.add(Variable(dataset_id=dataset_id, name="temperature", source_name="temp", role=VariableRole.data, dimensions=["depth"], unit="degC"))
    await session.commit()

  job = await sync(worker_engine, make_worker, dataset_id)
  assert job.status == JobStatus.failed
  assert job.error == "Nothing was written: a.nc: variable 'temp' has dimensions (depth), a timeSeries needs (time)"
  assert job.result is not None and job.result["files_checked"] == 1
  assert await partitions(worker_engine, dataset_id) == {}
  assert stores(filesystem, dataset_id) == []  # the store it started is deleted
  assert (await index(worker_engine, dataset_id))["a.nc"].status == FileStatus.new
  dataset = await get_dataset(worker_engine, dataset_id)
  assert (dataset.built_at, dataset.time_start, dataset.version) == (None, None, 1)


async def test_sync_fails_when_its_fetch_fails(worker_engine, make_worker, bucket, filesystem, tmp_path):
  bucket.put("a.nc", netcdf(tmp_path))
  dataset_id = await add_dataset(worker_engine, bucket)
  await add_variables(worker_engine, dataset_id)
  await sync(worker_engine, make_worker, dataset_id)

  bucket.remove("a.nc")
  job = await sync(worker_engine, make_worker, dataset_id)
  assert job.status == JobStatus.failed
  assert job.error is not None and job.error.startswith("The source has no files, but the archive has 1 files.")
  assert "Checking" not in job.log
  assert sorted(archive_paths(filesystem, dataset_id)) == ["a.nc"]


async def test_sync_without_source_fails(worker_engine, make_worker, filesystem):
  dataset_id = await add_dataset(worker_engine, None)
  job = await sync(worker_engine, make_worker, dataset_id)
  assert job.status == JobStatus.failed
  assert job.error == f"Dataset {dataset_id} has no source"
