from fastapi import APIRouter

from app.schemas import SourceType
from app.source_types import SOURCE_TYPES

router = APIRouter(tags=["Sources"])


@router.get("/source-types", response_model=list[SourceType], summary="List the supported source types")
async def list_source_types():
  """
  Lists the backends a source can use, with the options each one accepts. Only these options are accepted, and anything else is rejected with 422.

  Sources are always read anonymously. No backend has an option for access keys, passwords or tokens, and none will be added.
  """
  return SOURCE_TYPES
