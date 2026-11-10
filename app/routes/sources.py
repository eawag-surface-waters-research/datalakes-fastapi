from typing import Annotated

from fastapi import APIRouter, Body

from app.errors import ErrorResponse
from app.rclone import probe_source
from app.schemas import SourceInput, SourceTestResult, SourceType
from app.source_types import SOURCE_TYPES

router = APIRouter(tags=["Sources"])


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
      openapi_examples={
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
