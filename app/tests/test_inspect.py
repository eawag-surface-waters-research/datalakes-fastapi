from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from app.enums import DatasetKind, DatasetStatus, JobStatus, JobType, SourceBackend, VariableRole
from app.models import Dataset, File, Job, Parameter, Source, Variable

T0 = datetime(2024, 9, 1, tzinfo=UTC)
FILE = "2024/L2_ThermistorChain_202406.nc"


@pytest.fixture
def filesystem(tmp_path, monkeypatch) -> Path:
  monkeypatch.setenv("FILESYSTEM", str(tmp_path))
  return tmp_path


async def add_dataset(session, kind=DatasetKind.data, with_source=True, **values) -> Dataset:
  dataset = Dataset(title="Thermistor chain", kind=kind, external_url="https://example.org" if kind == DatasetKind.link else None, **values)
  session.add(dataset)
  await session.commit()
  await session.refresh(dataset)
  if with_source:
    session.add(Source(dataset_id=dataset.id, backend=SourceBackend.s3, options={"bucket": "eawag-data"}, last_tested_at=datetime.now(UTC)))
    await session.commit()
  return dataset


def thermistor_chain() -> xr.Dataset:
  """A small thermistor chain file: temperature over time and depth, with a -9999 placeholder and the location in the global attributes."""
  temp = np.array([[10.5, 9.0], [-9999, 9.1], [11.0, 9.2]])
  return xr.Dataset(
    {"temp": (("time", "depth"), temp, {"units": "degC", "long_name": "Water temperature"})},
    coords={
      "time": ("time", [1_717_200_000.0, 1_717_200_600.0, 1_717_201_200.0], {"units": "seconds since 1970-01-01 00:00:00"}),
      "depth": ("depth", [0.5, 1.0], {"units": "m"}),
    },
    attrs={"title": "Thermistor chain", "latitude": 46.5, "longitude": 6.66},
  )


async def add_file(session, filesystem: Path, dataset_id: int, path: str, on_disk=True, content: bytes | None = None) -> None:
  """Indexes a file and puts it in the archive: the thermistor chain file, or `content` if given."""
  session.add(File(dataset_id=dataset_id, path=path, size=4, modified_at=T0))
  await session.commit()
  if on_disk:
    archived = filesystem / "raw" / str(dataset_id) / path
    archived.parent.mkdir(parents=True, exist_ok=True)
    if content is None:
      thermistor_chain().to_netcdf(archived, engine="netcdf4")
    else:
      archived.write_bytes(content)


async def add_job(session, dataset_id: int, type: JobType, status: JobStatus) -> Job:
  job = Job(type=type, status=status, dataset_id=dataset_id)
  session.add(job)
  await session.commit()
  await session.refresh(job)
  return job


async def test_inspect_file(client, session, filesystem):
  dataset = await add_dataset(session)
  await add_file(session, filesystem, dataset.id, FILE)
  session.add_all(
    [
      Parameter(id=1, name="Time", cf_name="time"),
      Parameter(id=2, name="Depth", cf_name="depth"),
      Parameter(id=5, name="Water temperature", cf_name="water_temperature"),
    ]
  )
  await session.commit()
  response = await client.post(f"/datasets/{dataset.id}/inspect", json={"file": FILE})
  assert response.status_code == 200
  body = response.json()
  assert body["file"] == "L2_ThermistorChain_202406.nc"
  assert body["format"] == "netcdf4"
  assert body["dimensions"] == {"time": 3, "depth": 2}
  assert body["feature_type"] == "timeSeriesProfile"
  assert body["time_range"]["start"] == "2024-06-01T00:00:00Z"
  assert body["location"] == {"latitude": 46.5, "longitude": 6.66}
  assert body["global_attributes"] == {"title": "Thermistor chain", "latitude": 46.5, "longitude": 6.66}
  assert [(v["name"], v["role"], v["parameter_id"]) for v in body["variables"]] == [
    ("time", "time", 1),
    ("depth", "coordinate", 2),
    ("temp", "data", 5),
  ]
  temp = body["variables"][2]
  assert temp["dimensions"] == ["time", "depth"]
  assert temp["unit"] == "degC"
  assert temp["decoding"] == {"missing_values": [-9999]}
  assert temp["shape"] == [3, 2]
  assert temp["sample"] == {"count": 6, "missing": 1, "min": 9.0, "max": 11.0}
  assert body["warnings"] == ["temp: placeholder values found in the sample, suggested decoding {'missing_values': [-9999]}."]


async def test_inspect_learns_from_published_datasets(client, session, filesystem):
  """By its long_name, temp is Water temperature, but five published datasets chose Surface temperature for it."""
  session.add_all(
    [
      Parameter(id=1, name="Time", cf_name="time"),
      Parameter(id=5, name="Water temperature", cf_name="water_temperature"),
      Parameter(id=68, name="Surface temperature", cf_name="surface_water_temperature"),
    ]
  )
  await session.commit()
  for _ in range(5):
    other = await add_dataset(session, with_source=False, status=DatasetStatus.published)
    session.add(Variable(dataset_id=other.id, name="time", source_name="time", role=VariableRole.time, dimensions=["time"]))
    session.add(Variable(dataset_id=other.id, name="temp", source_name="temp", role=VariableRole.data, dimensions=["time"], parameter_id=68))
    await session.commit()
  dataset = await add_dataset(session)
  await add_file(session, filesystem, dataset.id, FILE)

  response = await client.post(f"/datasets/{dataset.id}/inspect", json={"file": FILE})
  assert response.status_code == 200
  assert {v["name"]: v["parameter_id"] for v in response.json()["variables"]}["temp"] == 68


@pytest.mark.parametrize("body", [None, {}, {"file": None}])
async def test_inspect_first_file_by_default(client, session, filesystem, body):
  """Without a file, the first file in the order of `GET /datasets/{dataset_id}/files` is inspected."""
  dataset = await add_dataset(session)
  await add_file(session, filesystem, dataset.id, "b.nc", content=b"# not read")
  await add_file(session, filesystem, dataset.id, "a.nc")
  other = await add_dataset(session)
  await add_file(session, filesystem, other.id, "0.nc", content=b"# another dataset's file")
  response = await client.post(f"/datasets/{dataset.id}/inspect", json=body)
  assert response.status_code == 200
  assert response.json()["file"] == "a.nc"


async def test_inspect_file_that_is_not_netcdf(client, session, filesystem):
  dataset = await add_dataset(session)
  await add_file(session, filesystem, dataset.id, "README.md", content=b"# Thermistor chain")
  response = await client.post(f"/datasets/{dataset.id}/inspect", json={"file": "README.md"})
  assert response.status_code == 422
  assert response.json() == {"detail": "File 'README.md' is not a NetCDF file (.nc). Only NetCDF is supported.", "code": "unreadable_file"}


async def test_inspect_broken_netcdf_file(client, session, filesystem):
  dataset = await add_dataset(session)
  await add_file(session, filesystem, dataset.id, FILE, content=b"CDF\x01")
  response = await client.post(f"/datasets/{dataset.id}/inspect", json={"file": FILE})
  assert response.status_code == 422
  assert response.json()["code"] == "unreadable_file"
  assert response.json()["detail"].startswith("File 'L2_ThermistorChain_202406.nc' can't be read as NetCDF")


@pytest.mark.parametrize("body", [{"file": FILE}, None])
async def test_inspect_unknown_dataset(client, body):
  response = await client.post("/datasets/999/inspect", json=body)
  assert response.status_code == 404
  assert response.json() == {"detail": "Dataset 999 not found", "code": "not_found"}


async def test_inspect_link_dataset(client, session):
  dataset = await add_dataset(session, kind=DatasetKind.link, with_source=False)
  response = await client.post(f"/datasets/{dataset.id}/inspect", json={"file": FILE})
  assert response.status_code == 422
  assert response.json() == {"detail": f"Dataset {dataset.id} is a link dataset and can't be inspected", "code": "link_dataset"}


async def test_inspect_dataset_without_source(client, session):
  dataset = await add_dataset(session, with_source=False)
  response = await client.post(f"/datasets/{dataset.id}/inspect", json={"file": FILE})
  assert response.status_code == 422
  assert response.json() == {"detail": f"Dataset {dataset.id} has no source", "code": "no_source"}


@pytest.mark.parametrize("type", [JobType.fetch, JobType.sync, JobType.rebuild])
@pytest.mark.parametrize("status", [JobStatus.queued, JobStatus.running])
async def test_inspect_while_a_job_is_in_progress(client, session, filesystem, type, status):
  dataset = await add_dataset(session)
  await add_file(session, filesystem, dataset.id, FILE)
  active = await add_job(session, dataset.id, type=type, status=status)
  response = await client.post(f"/datasets/{dataset.id}/inspect", json={"file": FILE})
  assert response.status_code == 409
  assert response.json() == {"detail": f"Dataset {dataset.id} already has a job {status} (job {active.id})", "code": "job_in_progress"}


def not_in_archive(dataset_id: int, path: str) -> dict:
  return {"detail": f"File {path!r} is not in the archive of dataset {dataset_id}. Fetch the dataset first.", "code": "file_not_in_archive"}


async def test_inspect_file_not_in_index(client, session, filesystem):
  dataset = await add_dataset(session)
  response = await client.post(f"/datasets/{dataset.id}/inspect", json={"file": FILE})
  assert response.status_code == 422
  assert response.json() == not_in_archive(dataset.id, FILE)


async def test_inspect_indexed_file_missing_from_archive(client, session, filesystem):
  dataset = await add_dataset(session)
  await add_file(session, filesystem, dataset.id, FILE, on_disk=False)
  response = await client.post(f"/datasets/{dataset.id}/inspect", json={"file": FILE})
  assert response.status_code == 422
  assert response.json() == not_in_archive(dataset.id, FILE)


async def test_inspect_path_outside_archive(client, session, filesystem):
  """A file on disk outside the archive is never read, as only indexed paths are."""
  dataset = await add_dataset(session)
  other = await add_dataset(session)
  await add_file(session, filesystem, other.id, FILE)
  path = f"../{other.id}/{FILE}"
  response = await client.post(f"/datasets/{dataset.id}/inspect", json={"file": path})
  assert response.status_code == 422
  assert response.json() == not_in_archive(dataset.id, path)
