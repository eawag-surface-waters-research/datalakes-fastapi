from typing import Annotated

from fastapi import APIRouter, Query
from sqlmodel import col, select

from app.database import SessionDep
from app.enums import FileStatus
from app.errors import ErrorResponse, not_found
from app.models import Dataset, File
from app.params import End, Start, parse_time_range
from app.schemas import FileRead

router = APIRouter(tags=["Files"])

FILES_EXAMPLE = [
  {
    "id": 20411,
    "dataset_id": 123,
    "path": "2024/L2_ThermistorChain_202409.nc",
    "size": 18011342,
    "modified_at": "2024-10-01T00:12:41Z",
    "time_start": "2024-09-01T00:00:00Z",
    "time_end": "2024-09-30T23:50:00Z",
    "depth_layout": 2,
    "status": "ok",
    "error": None,
  },
  {
    "id": 20412,
    "dataset_id": 123,
    "path": "2024/L2_ThermistorChain_202410.nc",
    "size": 17388120,
    "modified_at": "2024-10-30T00:00:01Z",
    "time_start": None,
    "time_end": None,
    "depth_layout": None,
    "status": "failed",
    "error": "variable 'temp' not found",
  },
]


@router.get(
  "/datasets/{dataset_id}/files",
  response_model=list[FileRead],
  summary="List a dataset's file index",
  responses={
    200: {"content": {"application/json": {"example": FILES_EXAMPLE}}},
    400: {"model": ErrorResponse, "description": "The request makes no sense, e.g. `start` is after `end`."},
    404: {"model": ErrorResponse, "description": "The dataset doesn't exist."},
  },
)
async def list_files(
  dataset_id: int,
  session: SessionDep,
  start: Start = None,
  end: End = None,
  status: Annotated[FileStatus | None, Query(description="Only files with this status.", examples=["failed"])] = None,
):
  """
  Lists every file fetched from the dataset's source, sorted by path, with its size, the time range it covers, and whether it was processed.
  It's also how to pick a file to inspect.

  With `start` and `end`, returns the files whose time range overlaps that period. Files not processed yet have no time range,
  so this filter leaves them out.

  - **Drafts:** their files are listed like any other.
  - **Link datasets** have no files, so the list is empty.

  **Access:** public.
  """
  start_time, end_time = parse_time_range(start, end)
  if await session.get(Dataset, dataset_id) is None:
    raise not_found("Dataset", dataset_id)

  query = select(File).where(File.dataset_id == dataset_id)
  if status:
    query = query.where(File.status == status)
  if start_time:
    query = query.where(col(File.time_end) >= start_time)
  if end_time:
    query = query.where(col(File.time_start) <= end_time)

  return list((await session.exec(query.order_by(col(File.path)))).all())
