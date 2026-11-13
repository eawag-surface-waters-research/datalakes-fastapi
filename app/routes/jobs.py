from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query
from sqlmodel import col, select

from app.database import SessionDep
from app.enums import JobStatus, JobType
from app.errors import ApiError, ErrorResponse, not_found
from app.models import Dataset, Job
from app.schemas import JobRead
from app.worker.queue import append_log

router = APIRouter(tags=["Jobs"])

StatusFilter = Annotated[JobStatus | None, Query(description="Only jobs with this status.", examples=["failed"])]
TypeFilter = Annotated[JobType | None, Query(description="Only jobs of this type.", examples=["sync"])]

NOT_FOUND_RESPONSE_EXAMPLE = {404: {"model": ErrorResponse, "description": "The job doesn't exist."}}

QUEUED_JOB_EXAMPLE = {
  "id": 5812,
  "dataset_id": 123,
  "type": "sync",
  "status": "queued",
  "progress": None,
  "error": None,
  "log": "",
  "result": None,
  "created_at": "2024-10-30T10:20:00Z",
  "started_at": None,
  "finished_at": None,
}
FAILED_JOB_EXAMPLE = {
  "id": 5790,
  "dataset_id": 123,
  "type": "sync",
  "status": "failed",
  "progress": 1,
  "error": "1 of 3 new files failed: L2_ThermistorChain_202410.nc: variable 'temp' not found",
  "log": "Copied 3 files (52.1 MB)\nProcessing L2_ThermistorChain_202410.nc failed: variable 'temp' not found",
  "result": {
    "files_new": 3,
    "files_changed": 0,
    "files_removed": 0,
    "files_unchanged": 64,
    "bytes_copied": 54632118,
    "files_processed": 2,
    "files_failed": [{"path": "2024/L2_ThermistorChain_202410.nc", "error": "variable 'temp' not found"}],
  },
  "created_at": "2024-10-30T00:00:02Z",
  "started_at": "2024-10-30T00:00:03Z",
  "finished_at": "2024-10-30T00:05:31Z",
}


async def find_jobs(session: SessionDep, *, dataset_id: int | None = None, status: JobStatus | None = None, type: JobType | None = None) -> list[Job]:
  query = select(Job)
  if dataset_id is not None:
    query = query.where(Job.dataset_id == dataset_id)
  if status:
    query = query.where(Job.status == status)
  if type:
    query = query.where(Job.type == type)
  return list((await session.exec(query.order_by(col(Job.created_at).desc(), col(Job.id).desc()))).all())


@router.get(
  "/jobs",
  response_model=list[JobRead],
  summary="List all jobs",
  responses={200: {"content": {"application/json": {"example": [FAILED_JOB_EXAMPLE]}}}},
)
async def list_jobs(session: SessionDep, status: StatusFilter = None, type: TypeFilter = None):
  """
  Lists the jobs of all datasets, newest first.

  Job types:
  - `fetch`: copy new and changed files from the source and drop removed ones. Nothing is processed.
  - `sync`: a fetch, then process the new and changed files.
  - `rebuild`: rebuild a dataset's Zarr stores from its archived files.
  - `export`: prepare a large download.

  **Access:** owner.
  """
  return await find_jobs(session, status=status, type=type)


@router.get(
  "/datasets/{dataset_id}/jobs",
  response_model=list[JobRead],
  summary="List a dataset's jobs",
  responses={
    200: {"content": {"application/json": {"example": [QUEUED_JOB_EXAMPLE, FAILED_JOB_EXAMPLE]}}},
    404: {"model": ErrorResponse, "description": "The dataset doesn't exist."},
  },
)
async def list_dataset_jobs(dataset_id: int, session: SessionDep, status: StatusFilter = None, type: TypeFilter = None):
  """
  Lists the jobs of one dataset, newest first.

  **Access:** public.
  """
  if await session.get(Dataset, dataset_id) is None:
    raise not_found("Dataset", dataset_id)
  return await find_jobs(session, dataset_id=dataset_id, status=status, type=type)


@router.post(
  "/jobs/clear-queue",
  response_model=list[JobRead],
  summary="Clear the job queue",
  responses={
    200: {
      "description": "The jobs that were cancelled. Empty if no job was queued.",
      "content": {
        "application/json": {
          "example": [
            {**QUEUED_JOB_EXAMPLE, "status": "cancelled", "log": "Was cancelled when the queue was cleared", "finished_at": "2024-10-30T10:21:05Z"}
          ]
        }
      },
    }
  },
)
async def clear_queue(session: SessionDep):
  """
  Cancels every queued job, so no worker picks them up. Running jobs are left alone: cancel them one by one with `POST /jobs/{job_id}/cancel`.
  `cleanup` jobs stay queued: they delete the files of deleted datasets, which would otherwise stay on disk.

  **Access:** owner.
  """
  # `skip_locked` leaves out the jobs a worker is claiming right now
  queued_query = (
    select(Job)
    .where(Job.status == JobStatus.queued, Job.type != JobType.cleanup)
    .order_by(col(Job.created_at), col(Job.id))
    .with_for_update(skip_locked=True)
  )
  jobs = list((await session.exec(queued_query)).all())

  finished_at = datetime.now(UTC)
  for job in jobs:
    job.status = JobStatus.cancelled
    job.finished_at = finished_at
    job.log = append_log(job.log, "Was cancelled when the queue was cleared")
    session.add(job)

  cancelled = [JobRead.model_validate(job) for job in jobs]
  await session.commit()

  return cancelled


@router.get(
  "/jobs/{job_id}",
  response_model=JobRead,
  summary="Get a job",
  responses={
    200: {
      "content": {
        "application/json": {
          "examples": {
            "queued": {"summary": "A queued sync", "value": QUEUED_JOB_EXAMPLE},
            "failed": {"summary": "A failed sync", "value": FAILED_JOB_EXAMPLE},
          }
        }
      }
    },
    **NOT_FOUND_RESPONSE_EXAMPLE,
  },
)
async def get_job(job_id: int, session: SessionDep):
  """
  Returns a job's status, progress, error, the last lines of its log, and its result. Poll this after starting a fetch, sync or rebuild.

  A job moves from `queued` to `running`, then ends as `succeeded`, `failed` or `cancelled`. If the worker stops while running a job
  (e.g. a restart), the job is queued again and runs from the start. After the worker stopped three times on the same job, the job fails.

  **Access:** public.
  """
  job = await session.get(Job, job_id)
  if job is None:
    raise not_found("Job", job_id)
  return job


@router.post(
  "/jobs/{job_id}/cancel",
  response_model=JobRead,
  summary="Cancel a job",
  responses={
    200: {
      "description": "The job, cancelled or stopping.",
      "content": {
        "application/json": {
          "example": {**QUEUED_JOB_EXAMPLE, "status": "cancelled", "log": "Was cancelled before it started", "finished_at": "2024-10-30T10:21:05Z"}
        }
      },
    },
    **NOT_FOUND_RESPONSE_EXAMPLE,
    409: {
      "model": ErrorResponse,
      "description": "The job has already ended.",
      "content": {"application/json": {"example": {"detail": "Job 5790 already ended (failed)", "code": "job_finished"}}},
    },
  },
)
async def cancel_job(job_id: int, session: SessionDep):
  """
  Cancels a queued or running job.

  - A **queued** job is cancelled at once.
  - A **running** job stops within a few seconds, so its status stays `running` for a moment. Poll the job to see it end as `cancelled`.

  Cancelling a job that's already being cancelled is fine: it returns the job as it is.

  **Access:** user (jobs of own datasets) or owner.
  """
  # `with_for_update` locks the row, so the worker can't claim the job while it's being cancelled
  job = await session.get(Job, job_id, with_for_update=True)
  if job is None:
    raise not_found("Job", job_id)
  if job.status == JobStatus.queued:
    job.status = JobStatus.cancelled
    job.finished_at = datetime.now(UTC)
    job.log = append_log(job.log, "Was cancelled before it started")
  elif job.status == JobStatus.running:
    if job.cancel_requested_at is None:
      job.cancel_requested_at = datetime.now(UTC)
  else:
    raise ApiError(409, "job_finished", f"Job {job_id} already ended ({job.status})")
  session.add(job)
  await session.commit()
  await session.refresh(job)
  return job
