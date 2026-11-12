from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.enums import DatasetKind, JobStatus, JobType, SourceBackend
from app.models import Dataset, File, Job, Source

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


async def add_file(session, filesystem: Path, dataset_id: int, path: str, on_disk=True) -> None:
  session.add(File(dataset_id=dataset_id, path=path, size=4, modified_at=T0))
  await session.commit()
  if on_disk:
    archived = filesystem / "raw" / str(dataset_id) / path
    archived.parent.mkdir(parents=True, exist_ok=True)
    archived.write_bytes(b"CDF\x01")


async def add_job(session, dataset_id: int, type: JobType, status: JobStatus) -> Job:
  job = Job(type=type, status=status, dataset_id=dataset_id)
  session.add(job)
  await session.commit()
  await session.refresh(job)
  return job


async def test_inspect_extension_is_case_insensitive(client, session, filesystem):
  dataset = await add_dataset(session)
  await add_file(session, filesystem, dataset.id, "2024/DATA.NC")
  response = await client.post(f"/datasets/{dataset.id}/inspect", json={"file": "2024/DATA.NC"})
  assert response.status_code == 501


async def test_inspect_unknown_dataset(client):
  response = await client.post("/datasets/999/inspect", json={"file": FILE})
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
