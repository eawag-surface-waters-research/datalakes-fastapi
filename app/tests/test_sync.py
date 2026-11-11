from datetime import UTC, datetime

import pytest

from app.enums import DatasetKind, JobStatus, JobType, SourceBackend
from app.models import Dataset, Job, Source


async def add_dataset(session, kind=DatasetKind.data, with_source=True) -> Dataset:
  dataset = Dataset(title="Thermistor chain", kind=kind, external_url="https://example.org" if kind == DatasetKind.link else None)
  session.add(dataset)
  await session.commit()
  await session.refresh(dataset)
  if with_source:
    session.add(Source(dataset_id=dataset.id, backend=SourceBackend.s3, options={"bucket": "eawag-data"}, last_tested_at=datetime.now(UTC)))
    await session.commit()
  return dataset


async def add_job(session, dataset_id, type=JobType.fetch, status=JobStatus.queued) -> Job:
  job = Job(type=type, status=status, dataset_id=dataset_id)
  session.add(job)
  await session.commit()
  await session.refresh(job)
  return job


async def test_fetch_queues_a_job(client, session):
  dataset = await add_dataset(session)
  response = await client.post(f"/datasets/{dataset.id}/fetch")
  assert response.status_code == 202
  body = response.json()
  assert body["dataset_id"] == dataset.id
  assert body["type"] == "fetch"
  assert body["status"] == "queued"
  assert "location" not in response.headers

  job = await session.get(Job, body["id"])
  assert job is not None
  assert job.params == {}


async def test_fetch_job_can_be_polled(client, session):
  dataset = await add_dataset(session)
  job_id = (await client.post(f"/datasets/{dataset.id}/fetch")).json()["id"]
  response = await client.get(f"/jobs/{job_id}")
  assert response.status_code == 200
  assert response.json()["type"] == "fetch"


async def test_fetch_unknown_dataset(client):
  response = await client.post("/datasets/999/fetch")
  assert response.status_code == 404
  assert response.json() == {"detail": "Dataset 999 not found", "code": "not_found"}


async def test_fetch_dataset_without_source(client, session):
  dataset = await add_dataset(session, with_source=False)
  response = await client.post(f"/datasets/{dataset.id}/fetch")
  assert response.status_code == 422
  assert response.json() == {"detail": f"Dataset {dataset.id} has no source", "code": "no_source"}


async def test_fetch_link_dataset(client, session):
  dataset = await add_dataset(session, kind=DatasetKind.link, with_source=False)
  response = await client.post(f"/datasets/{dataset.id}/fetch")
  assert response.status_code == 422
  assert response.json() == {"detail": f"Dataset {dataset.id} is a link dataset and can't be fetched", "code": "link_dataset"}


@pytest.mark.parametrize("type", [JobType.fetch, JobType.sync, JobType.rebuild])
@pytest.mark.parametrize("status", [JobStatus.queued, JobStatus.running])
async def test_fetch_while_a_job_is_in_progress(client, session, type, status):
  dataset = await add_dataset(session)
  active = await add_job(session, dataset.id, type=type, status=status)
  response = await client.post(f"/datasets/{dataset.id}/fetch")
  assert response.status_code == 409
  assert response.json() == {"detail": f"Dataset {dataset.id} already has a job {status} (job {active.id})", "code": "job_in_progress"}


async def test_second_fetch_is_rejected(client, session):
  dataset = await add_dataset(session)
  assert (await client.post(f"/datasets/{dataset.id}/fetch")).status_code == 202
  assert (await client.post(f"/datasets/{dataset.id}/fetch")).status_code == 409
