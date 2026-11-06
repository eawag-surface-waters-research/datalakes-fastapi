from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func
from sqlmodel import col, select

from app.database import SessionDep
from app.enums import DatasetSort, DatasetStatus, FeatureType
from app.errors import ApiError, ErrorResponse, not_found
from app.models import Collection, CollectionDataset, Dataset, License, Organisation, Person, Site, Variable
from app.params import Bbox, End, Start, parse_bbox, parse_time_range
from app.schemas import DatasetCreate, DatasetRead

router = APIRouter(prefix="/datasets", tags=["Datasets"])

# Fields that point to a row of another table, with the name used in error messages
REFERENCES: dict[str, tuple[str, type]] = {
  "site_id": ("Site", Site),
  "person_id": ("Person", Person),
  "organisation_id": ("Organisation", Organisation),
  "license_id": ("License", License),
}


async def check_references(session: SessionDep, data: DatasetCreate) -> None:
  return  # TODO: Remove when other resources can be created
  for field, (name, model) in REFERENCES.items():
    reference_id = getattr(data, field)
    if reference_id is not None and await session.get(model, reference_id) is None:
      raise ApiError(422, "unknown_reference", f"{name} {reference_id} not found")


@router.get(
  "",
  response_model=list[DatasetRead],
  summary="List datasets",
  responses={400: {"model": ErrorResponse, "description": "The request makes no sense, e.g. `start` is after `end`."}},
)
async def list_datasets(
  session: SessionDep,
  q: Annotated[
    str | None, Query(min_length=1, description="Text to search for in the title and description, case-insensitive.", examples=["thermistor"])
  ] = None,
  site_id: Annotated[int | None, Query(description="Only datasets at this site.", examples=[1])] = None,
  parameter: Annotated[
    int | None, Query(description="Only datasets with a variable measuring this parameter (an ID from the `parameters` vocabulary).", examples=[5])
  ] = None,
  collection: Annotated[str | None, Query(description="Only datasets in this collection, by ID or slug.", examples=["lexplore"])] = None,
  feature_type: Annotated[FeatureType | None, Query(description="Only datasets of this feature type.")] = None,
  start: Start = None,
  end: End = None,
  bbox: Bbox = None,
  status: Annotated[DatasetStatus, Query(description="List published datasets (default) or drafts.")] = DatasetStatus.published,
  sort: Annotated[DatasetSort, Query(description="How to order the datasets.")] = DatasetSort.title,
):
  """
  Lists the published datasets, or the drafts with `status=draft`. All filters combine with AND.

  - **Drafts** are left out unless `status=draft` is requested.
  - **Link datasets** are listed like other datasets. They have no variables and no extents, so the `parameter` and `start`/`end` filters leave them out.
  - `start` and `end` keep the datasets whose time range overlaps the given one. Datasets without data are left out.
  - `bbox` keeps the datasets whose `latitude` and `longitude` are inside the box. Datasets without a location are left out.

  `sort` is one of:
  - `title`: alphabetically.
  - `recent`: latest data first (by `time_end`). Datasets without data come last.
  - `downloads`: most downloaded first.
  """
  start_time, end_time = parse_time_range(start, end)
  query = select(Dataset).where(Dataset.status == status)
  if q:
    query = query.where(col(Dataset.title).icontains(q, autoescape=True) | col(Dataset.description).icontains(q, autoescape=True))
  if site_id is not None:
    query = query.where(Dataset.site_id == site_id)
  if feature_type:
    query = query.where(Dataset.feature_type == feature_type)
  if parameter is not None:
    query = query.where(col(Dataset.id).in_(select(Variable.dataset_id).where(Variable.parameter_id == parameter)))
  if collection:
    # Slugs always contain a letter, so a number is an ID
    in_collection = CollectionDataset.collection_id == int(collection) if collection.isdecimal() else col(Collection.slug) == collection
    query = query.where(col(Dataset.id).in_(select(CollectionDataset.dataset_id).join(Collection).where(in_collection)))
  if start_time:
    query = query.where(col(Dataset.time_end) >= start_time)
  if end_time:
    query = query.where(col(Dataset.time_start) <= end_time)
  if bbox:
    min_lon, min_lat, max_lon, max_lat = parse_bbox(bbox)
    query = query.where(col(Dataset.longitude).between(min_lon, max_lon), col(Dataset.latitude).between(min_lat, max_lat))

  if sort == DatasetSort.recent:
    query = query.order_by(col(Dataset.time_end).desc().nulls_last())
  elif sort == DatasetSort.downloads:
    query = query.order_by(col(Dataset.downloads).desc())

  query = query.order_by(func.lower(Dataset.title), col(Dataset.id))  # to make the sort stable

  return (await session.exec(query)).all()


@router.post(
  "",
  response_model=DatasetRead,
  status_code=201,
  summary="Create a dataset",
  responses={
    422: {"model": ErrorResponse, "description": "The dataset is invalid, e.g. a link dataset without `external_url`, or a site that doesn't exist."}
  },
)
async def create_dataset(data: DatasetCreate, session: SessionDep):
  """
  Creates a dataset. New datasets are drafts unless `status` says otherwise, so they can be set up before they're listed.

  - A **data dataset** (`kind: data`, the default) gets its variables and source afterwards, through their own endpoints.
  - A **link dataset** (`kind: link`) only describes data shown elsewhere. It needs `external_url`, and can't have variables or a source.

  The ID is always assigned automatically. `kind` can't be changed later.
  """
  await check_references(session, data)
  dataset = Dataset.model_validate(data)
  session.add(dataset)
  await session.commit()
  await session.refresh(dataset)
  return dataset


@router.get(
  "/{dataset_id}",
  response_model=DatasetRead,
  summary="Get a dataset",
  responses={404: {"model": ErrorResponse, "description": "The resource doesn't exist."}},
)
async def get_dataset(dataset_id: int, session: SessionDep):
  """
  Returns a dataset's metadata.

  - **Drafts** are returned too: they're only left out of lists.
  - **Link datasets** have no variables and no source. Their data is at `external_url`.
  """
  dataset = await session.get(Dataset, dataset_id)
  if dataset is None:
    raise not_found("Dataset", dataset_id)
  return dataset
