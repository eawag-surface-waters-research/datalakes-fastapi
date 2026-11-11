from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Body, Response
from fastapi.openapi.models import Example
from sqlmodel import select

from app.backends import SOURCE_TYPES, SourceInput
from app.database import SessionDep
from app.enums import DatasetKind
from app.errors import ApiError, ErrorResponse, not_found
from app.models import Dataset, Source
from app.rclone import probe_source
from app.routes.sync import CONFLICT_RESPONSE, ensure_no_active_job
from app.schemas import SourceRead, SourceTestResult, SourceType

router = APIRouter(tags=["Sources"])

SOURCE_INPUT_EXAMPLES: dict[str, Example] = {
  "s3": {
    "summary": "An S3 bucket and prefix",
    "value": {
      "backend": "s3",
      "options": {"bucket": "eawag-lexplore", "region": "eu-central-1"},
      "path": "thermistorchain/Level2",
      "include": ["*.nc"],
    },
  },
  "switchdrive": {
    "summary": "A SWITCHdrive public share",
    "value": {
      "backend": "switchdrive",
      "options": {"share_url": "https://drive.switch.ch/index.php/s/aB3dE5fG7hJ9kL1"},
      "path": "Level2",
      "include": ["*.nc"],
    },
  },
  "credentials": {
    "summary": "A config with credentials (rejected with 422)",
    "value": {
      "backend": "s3",
      "options": {"bucket": "eawag-lexplore", "region": "eu-central-1", "access_key_id": "AKIAIOSFODNN7EXAMPLE"},
      "path": "thermistorchain/Level2",
    },
  },
}


@router.get("/source-types", response_model=list[SourceType], summary="List the supported source types")
async def list_source_types():
  """
  Lists the backends a source can use, with the options each one accepts. Only these options are accepted, and anything else is rejected with 422.

  Sources are always read anonymously. No backend has an option for access keys, passwords or tokens, and none will be added.
  """
  return SOURCE_TYPES


@router.post(
  "/sources/test",
  response_model=SourceTestResult,
  summary="Test a source without saving it",
  responses={
    200: {
      "description": "The result of the test.",
      "content": {
        "application/json": {
          "examples": {
            "ok": {
              "summary": "The source can be read",
              "value": {
                "ok": True,
                "error": None,
                "files": [
                  {"path": "2024/L2_ThermistorChain_202405.nc", "size": 18342011, "modified_at": "2024-06-01T00:12:40Z"},
                  {"path": "2024/L2_ThermistorChain_202406.nc", "size": 17920455, "modified_at": "2024-07-01T00:12:38Z"},
                ],
                "truncated": False,
              },
            },
            "not_public": {
              "summary": "The bucket doesn't allow anonymous reads",
              "value": {
                "ok": False,
                "error": {
                  "detail": "Access denied to bucket eawag-lexplore. Sources are read anonymously, so the bucket must allow public reads.",
                  "code": "source_not_public",
                },
                "files": [],
                "truncated": False,
              },
            },
            "missing": {
              "summary": "The bucket doesn't exist",
              "value": {
                "ok": False,
                "error": {"detail": "Bucket eawag-lexplor does not exist", "code": "source_not_found"},
                "files": [],
                "truncated": False,
              },
            },
          }
        }
      },
    },
    422: {
      "model": ErrorResponse,
      "description": "The source config is invalid, e.g. it has an option its backend doesn't accept, such as an access key.",
      "content": {
        "application/json": {
          "examples": {
            "credentials": {
              "summary": "A source config with an access key",
              "value": {"detail": "Unknown option 'access_key_id' for backend s3. Allowed: bucket, region, endpoint", "code": "unknown_option"},
            },
            "unknown_option": {
              "summary": "An option the backend doesn't accept",
              "value": {"detail": "Unknown option 'acl' for backend s3. Allowed: bucket, region, endpoint", "code": "unknown_option"},
            },
          }
        }
      },
    },
  },
)
async def test_source(
  source: Annotated[
    SourceInput,
    Body(
      openapi_examples=SOURCE_INPUT_EXAMPLES,
    ),
  ],
):
  """
  Tries to list a source anonymously and returns the first files it finds.

  The response is 200 whether or not the source could be read: check `ok`. An invalid config is rejected with 422 before anything is tried:

  - an option that isn't listed for its backend in `GET /source-types` (`unknown_option`). Only listed options are accepted, and no backend has
    an option for credentials, so access keys, passwords and tokens are rejected this way.
  - an S3 `endpoint` that isn't a public HTTPS address, or that contains a user name or password (`validation_error`),
  - an `include` pattern rclone can't read, e.g. `[abc` (`validation_error`).

  The listing includes subfolders and stops after 100 files (`truncated` is then `true`), or after 10 s (`source_timeout`).
  rclone lists a whole folder before returning any of it, so a folder with very many files directly in it (about 100,000) can time out.
  rclone applies the `include` patterns, so only the files a sync would fetch are listed. Folders aren't listed: a file's `path` shows its folders.
  A prefix without matching files is not an error: `ok` is `true` and `files` is empty.
  """
  return await probe_source(source)


SOURCE_READ_EXAMPLE = {
  "dataset_id": 123,
  "backend": "s3",
  "options": {"bucket": "eawag-lexplore", "region": "eu-central-1", "endpoint": None},
  "path": "thermistorchain/Level2",
  "include": ["*.nc"],
  "last_tested_at": "2024-10-30T10:04:12Z",
  "last_synced_at": "2024-10-30T10:04:12Z",
  "created_at": "2019-03-20T09:15:00Z",
  "updated_at": "2023-02-01T13:40:00Z",
}


NO_SOURCE_RESPONSE = {
  404: {
    "model": ErrorResponse,
    "description": "The dataset doesn't exist, or has no source.",
    "content": {
      "application/json": {
        "examples": {
          "no_dataset": {"summary": "The dataset doesn't exist", "value": {"detail": "Dataset 999 not found", "code": "not_found"}},
          "no_source": {"summary": "The dataset has no source", "value": {"detail": "Dataset 512 has no source", "code": "no_source"}},
        }
      }
    },
  }
}


async def get_stored_source(session: SessionDep, dataset_id: int, *, lock_dataset: bool = False) -> tuple[Dataset, Source]:
  dataset = await session.get(Dataset, dataset_id, with_for_update=lock_dataset)
  if dataset is None:
    raise not_found("Dataset", dataset_id)
  source = (await session.exec(select(Source).where(Source.dataset_id == dataset_id))).first()
  if source is None:
    raise ApiError(404, "no_source", f"Dataset {dataset_id} has no source")
  return dataset, source


@router.get(
  "/datasets/{dataset_id}/source",
  response_model=SourceRead,
  summary="Get a dataset's source",
  responses={200: {"content": {"application/json": {"example": SOURCE_READ_EXAMPLE}}}, **NO_SOURCE_RESPONSE},
)
async def get_source(dataset_id: int, session: SessionDep):
  """
  Returns where the dataset's files come from.

  - **Drafts** have sources like any other dataset.
  - **Link datasets** never have a source (404).

  **Access:** public.
  """
  _, source = await get_stored_source(session, dataset_id)
  return source


@router.put(
  "/datasets/{dataset_id}/source",
  response_model=SourceRead,
  summary="Set or replace a dataset's source",
  responses={
    200: {"description": "The source was replaced.", "content": {"application/json": {"example": SOURCE_READ_EXAMPLE}}},
    201: {
      "model": SourceRead,
      "description": "The dataset had no source and now has one.",
      "content": {"application/json": {"example": {**SOURCE_READ_EXAMPLE, "last_synced_at": None}}},
    },
    404: {"model": ErrorResponse, "description": "The dataset doesn't exist."},
    **CONFLICT_RESPONSE,
    422: {
      "model": ErrorResponse,
      "description": "The config is invalid (e.g. it contains credentials), the source can't be read anonymously, or the dataset is a link dataset.",
      "content": {
        "application/json": {
          "examples": {
            "credentials": {
              "summary": "A source config with an access key",
              "value": {"detail": "Unknown option 'access_key_id' for backend s3. Allowed: bucket, region, endpoint", "code": "unknown_option"},
            },
            "not_public": {
              "summary": "The source can't be read anonymously",
              "value": {
                "detail": "Access denied to bucket eawag-lexplore. Sources are read anonymously, so the bucket must allow public reads.",
                "code": "source_not_public",
              },
            },
            "link_dataset": {
              "summary": "The dataset is a link dataset",
              "value": {"detail": "Dataset 2 is a link dataset and can't have a source", "code": "link_dataset"},
            },
          }
        }
      },
    },
  },
)
async def set_source(
  dataset_id: int,
  source: Annotated[SourceInput, Body(openapi_examples=SOURCE_INPUT_EXAMPLES)],
  session: SessionDep,
  response: Response,
):
  """
  Sets the dataset's source, replacing the current one. A dataset has one source. Nothing is downloaded yet, a fetch must be queued to do that

  The source is tested first like in `POST /sources/test`. It's only saved if it can be read anonymously. If not, the response is 422 with the test's error.

  Replacing a source keeps the files already fetched. The next fetch or sync adds the files of the new source.
  The dataset's `version` is increased.

  The source can't be changed while a fetch, sync or rebuild of the dataset is queued or running (409).

  - **Drafts:** a source can be set as usual.
  - **Link datasets** can't have a source (422).

  **Access:** user (own datasets) or owner.
  """
  dataset = await session.get(Dataset, dataset_id)
  if dataset is None:
    raise not_found("Dataset", dataset_id)
  if dataset.kind == DatasetKind.link:
    raise ApiError(422, "link_dataset", f"Dataset {dataset_id} is a link dataset and can't have a source")

  await ensure_no_active_job(session, dataset_id)  # check the first time before the test, which can take a few seconds

  result = await probe_source(source)
  if result.error is not None:
    raise ApiError(422, result.error.code, result.error.detail)

  await session.refresh(dataset, with_for_update=True)  # lock the dataset row so a job can't be queued between the check below and the commit
  await ensure_no_active_job(session, dataset_id)  # check again after the test, as one could have been queued while the test was running

  stored = (await session.exec(select(Source).where(Source.dataset_id == dataset_id))).first()
  values = {
    "backend": source.backend,
    "options": source.options.model_dump(mode="json"),
    "path": source.path,
    "include": source.include,
    "last_tested_at": datetime.now(UTC),
  }
  if stored is None:
    # the dataset had no source yet, so create one
    stored = Source(dataset_id=dataset_id, **values)
    response.status_code = 201
  else:
    # the dataset already had a source, so update it
    stored.sqlmodel_update(values)

  dataset.version += 1
  session.add_all([stored, dataset])

  await session.commit()
  await session.refresh(stored)
  return stored


@router.delete(
  "/datasets/{dataset_id}/source",
  status_code=204,
  summary="Remove a dataset's source",
  responses={204: {"description": "The source was removed."}, **NO_SOURCE_RESPONSE, **CONFLICT_RESPONSE},
)
async def delete_source(dataset_id: int, session: SessionDep):
  """
  Removes the source. The files already fetched and the data built from them are kept, and the dataset just stops syncing.
  Increases the dataset's `version`.

  The source can't be removed while a fetch, sync or rebuild of the dataset is queued or running (409).

  **Access:** user (own datasets) or owner.
  """
  # Locking the dataset, so a job can't be queued between the check and the commit
  dataset, source = await get_stored_source(session, dataset_id, lock_dataset=True)

  await ensure_no_active_job(session, dataset_id)
  await session.delete(source)

  dataset.version += 1
  session.add(dataset)
  await session.commit()
