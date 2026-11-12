from datetime import UTC, datetime, timedelta

import pytest

from app.enums import JobStatus, JobType
from app.models import Dataset, Job

T0 = datetime(2026, 11, 1, tzinfo=UTC)


async def add_job(session, type=JobType.test, status=JobStatus.queued, created_at=T0, **values) -> Job:
  job = Job(type=type, status=status, created_at=created_at, **values)
  session.add(job)
  await session.commit()
  await session.refresh(job)
  return job


async def test_get_missing_job(client):
  response = await client.get("/jobs/999")
  assert response.status_code == 404
  assert response.json() == {"detail": "Job 999 not found", "code": "not_found"}


async def test_list_jobs_newest_first(client, session):
  old = await add_job(session, created_at=T0)
  new = await add_job(session, created_at=T0 + timedelta(hours=1))
  same_time = await add_job(session, created_at=T0 + timedelta(hours=1))  # the higher ID comes first
  response = await client.get("/jobs")
  assert response.status_code == 200
  assert [job["id"] for job in response.json()] == [same_time.id, new.id, old.id]


async def test_cancel_queued_job(client, session):
  job = await add_job(session, status=JobStatus.queued)
  response = await client.post(f"/jobs/{job.id}/cancel")
  assert response.status_code == 200
  body = response.json()
  assert body["status"] == "cancelled"
  assert body["finished_at"] is not None
  assert body["log"] == "Was cancelled before it started"


async def test_cancel_running_job_asks_the_worker_to_stop_it(client, session):
  job = await add_job(session, status=JobStatus.running, started_at=T0)
  response = await client.post(f"/jobs/{job.id}/cancel")
  assert response.status_code == 200
  assert response.json()["status"] == "running"  # until the worker has stopped it
  await session.refresh(job)
  requested_at = job.cancel_requested_at
  assert requested_at is not None

  # Cancelling again is fine and keeps the first request
  response = await client.post(f"/jobs/{job.id}/cancel")
  assert response.status_code == 200
  await session.refresh(job)
  assert job.cancel_requested_at == requested_at


@pytest.mark.parametrize("status", [JobStatus.succeeded, JobStatus.failed, JobStatus.cancelled])
async def test_cancel_ended_job(client, session, status):
  job = await add_job(session, status=status)
  response = await client.post(f"/jobs/{job.id}/cancel")
  assert response.status_code == 409
  assert response.json() == {"detail": f"Job {job.id} already ended ({status})", "code": "job_finished"}


async def test_cancel_missing_job(client):
  response = await client.post("/jobs/999/cancel")
  assert response.status_code == 404
  assert response.json() == {"detail": "Job 999 not found", "code": "not_found"}


async def add_dataset(session) -> Dataset:
  dataset = Dataset(title="Thermistor chain")
  session.add(dataset)
  await session.commit()
  await session.refresh(dataset)
  return dataset


async def test_list_dataset_jobs_newest_first(client, session):
  dataset = await add_dataset(session)
  other = await add_dataset(session)
  old = await add_job(session, type=JobType.fetch, dataset_id=dataset.id, created_at=T0)
  new = await add_job(session, type=JobType.sync, dataset_id=dataset.id, created_at=T0 + timedelta(hours=1))
  same_time = await add_job(session, type=JobType.fetch, dataset_id=dataset.id, created_at=T0 + timedelta(hours=1))
  await add_job(session, type=JobType.fetch, dataset_id=other.id)
  await add_job(session)  # a job without a dataset
  response = await client.get(f"/datasets/{dataset.id}/jobs")
  assert response.status_code == 200
  body = response.json()
  assert [job["id"] for job in body] == [same_time.id, new.id, old.id]
  assert all(job["dataset_id"] == dataset.id for job in body)


async def test_list_dataset_jobs_filtered(client, session):
  dataset = await add_dataset(session)
  failed_fetch = await add_job(session, type=JobType.fetch, status=JobStatus.failed, dataset_id=dataset.id)
  await add_job(session, type=JobType.sync, status=JobStatus.failed, dataset_id=dataset.id)
  await add_job(session, type=JobType.fetch, status=JobStatus.succeeded, dataset_id=dataset.id)
  response = await client.get(f"/datasets/{dataset.id}/jobs", params={"status": "failed", "type": "fetch"})
  assert response.status_code == 200
  assert [job["id"] for job in response.json()] == [failed_fetch.id]


async def test_list_jobs_of_missing_dataset(client):
  response = await client.get("/datasets/999/jobs")
  assert response.status_code == 404
  assert response.json() == {"detail": "Dataset 999 not found", "code": "not_found"}
