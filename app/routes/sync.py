from fastapi import APIRouter
from sqlmodel import col, select

from app.database import SessionDep
from app.enums import DatasetKind, JobStatus, JobType
from app.errors import ApiError, ErrorResponse, not_found
from app.models import Dataset, Job, Source
from app.routes.jobs import QUEUED_JOB_EXAMPLE
from app.schemas import JobRead
from app.worker.queue import enqueue

router = APIRouter(prefix="/datasets", tags=["Sync"])

EXCLUSIVE_JOB_TYPES = (
  JobType.fetch,
  JobType.sync,
  JobType.rebuild,
)  # only one of these runs per dataset at a time, as they all write to the dataset's archive or data
VERBS = {JobType.fetch: "fetched", JobType.sync: "synced", JobType.rebuild: "rebuilt"}  # for error messages

NOT_FOUND_RESPONSE = {404: {"model": ErrorResponse, "description": "The dataset doesn't exist."}}
CONFLICT_RESPONSE = {
  409: {
    "model": ErrorResponse,
    "description": "A fetch, sync or rebuild of the dataset is already queued or running. Try again when it has ended.",
    "content": {"application/json": {"example": {"detail": "Dataset 123 already has a job running (job 5812)", "code": "job_in_progress"}}},
  }
}


def job_accepted_response(type: JobType) -> dict:
  return {
    202: {
      "description": "The job was queued. Poll `GET /jobs/{job_id}` with its `id` to follow it.",
      "content": {"application/json": {"example": {**QUEUED_JOB_EXAMPLE, "type": type}}},
    }
  }


async def ensure_no_active_job(session: SessionDep, dataset_id: int) -> None:
  """Raises 409 if a fetch, sync or rebuild of the dataset is queued or running."""
  active_job_query = (
    select(Job)
    .where(Job.dataset_id == dataset_id, col(Job.type).in_(EXCLUSIVE_JOB_TYPES), col(Job.status).in_([JobStatus.queued, JobStatus.running]))
    .order_by(col(Job.id))
    .limit(1)
  )
  if (active := (await session.exec(active_job_query)).first()) is not None:
    raise ApiError(409, "job_in_progress", f"Dataset {dataset_id} already has a job {active.status} (job {active.id})")


async def queue_dataset_job(session: SessionDep, dataset_id: int, type: JobType) -> Job:
  """
  Queues a job of an exclusive type for a dataset that has a source.
  Raises 404 for an unknown dataset, 422 for a dataset without a source, and 409 if another exclusive job is queued or running.
  """
  # Locking the dataset row makes concurrent requests for the same dataset wait here, so only one of them queues a job.
  # The lock is released by the commit in `enqueue`, or by the rollback when an error is raised.
  dataset = await session.get(Dataset, dataset_id, with_for_update=True)
  if dataset is None:
    raise not_found("Dataset", dataset_id)
  if dataset.kind == DatasetKind.link:
    raise ApiError(422, "link_dataset", f"Dataset {dataset_id} is a link dataset and can't be {VERBS[type]}")
  if (await session.exec(select(Source.id).where(Source.dataset_id == dataset_id))).first() is None:
    raise ApiError(422, "no_source", f"Dataset {dataset_id} has no source")

  await ensure_no_active_job(session, dataset_id)
  return await enqueue(session, type, dataset_id=dataset_id)


@router.post(
  "/{dataset_id}/fetch",
  response_model=JobRead,
  status_code=202,
  summary="Fetch a dataset's files from its source",
  responses={
    **job_accepted_response(JobType.fetch),
    **NOT_FOUND_RESPONSE,
    **CONFLICT_RESPONSE,
    422: {
      "model": ErrorResponse,
      "description": "The dataset has no source, e.g. it's a link dataset.",
      "content": {
        "application/json": {
          "examples": {
            "no_source": {"summary": "The dataset has no source", "value": {"detail": "Dataset 512 has no source", "code": "no_source"}},
            "link_dataset": {
              "summary": "The dataset is a link dataset",
              "value": {"detail": "Dataset 2 is a link dataset and can't be fetched", "code": "link_dataset"},
            },
          }
        }
      },
    },
  },
)
async def fetch_dataset(dataset_id: int, session: SessionDep):
  """
  Queues a `fetch` job and returns it. The worker then:

  1. Lists the source and compares it with the file index. A file is **new** if the index doesn't have it, **changed** if its size,
     modified time or checksum differs, and **removed** if it's no longer in the source.
  2. Copies new and changed files into the dataset's archive, replacing the old copy of a changed file. Removed files are deleted from
     the archive and from the index. New and changed files are marked `new` in the index.

  If the listing fails, or is empty for a dataset that had files, the job fails and removes nothing.

  Only one job per dataset runs at a time. If a fetch, sync or rebuild is already queued or running, this returns 409 with the job's ID.

  - **Drafts** are fetched as usual.
  - **Link datasets** have no source (422).

  **Access:** user (own datasets) or owner.
  """
  return await queue_dataset_job(session, dataset_id, JobType.fetch)
