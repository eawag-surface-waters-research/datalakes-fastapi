from typing import Annotated

from fastapi import APIRouter, Body, Query
from sqlalchemy import func
from sqlmodel import col, select

from app.database import SessionDep
from app.enums import DatasetKind, DatasetSort, DatasetStatus, FeatureType
from app.errors import ApiError, ErrorResponse, not_found
from app.models import Collection, CollectionDataset, Dataset, License, Organisation, Person, Site, Variable
from app.params import Bbox, End, Expand, Start, parse_bbox, parse_time_range
from app.references import Reference, check_references, expand_references
from app.routes.sync import CONFLICT_RESPONSE, ensure_no_active_job
from app.schemas import DatasetCreate, DatasetRead, DatasetUpdate, LicenseRead, OrganisationRead, PersonRead, SiteRead

router = APIRouter(prefix="/datasets", tags=["Datasets"])

REFERENCES: dict[str, Reference] = {
  "site_id": Reference("Site", Site, SiteRead),
  "person_id": Reference("Person", Person, PersonRead),
  "organisation_id": Reference("Organisation", Organisation, OrganisationRead),
  "license_id": Reference("License", License, LicenseRead),
}


@router.get(
  "",
  response_model=list[DatasetRead],
  response_model_exclude_unset=True,  # leaves out the referenced objects unless they're expanded
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
  expand: Expand = True,
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

  The site, contact person, organisation and license of each dataset are returned in full next to their IDs, unless `expand=false`.
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

  datasets = [DatasetRead.model_validate(dataset) for dataset in (await session.exec(query)).all()]
  if expand:
    await expand_references(session, datasets, REFERENCES)
  return datasets


@router.post(
  "",
  response_model=DatasetRead,
  response_model_exclude_unset=True,  # leaves out the referenced objects, which are never expanded here
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

  The response has the IDs of the site, contact person, organisation and license only. `GET /datasets/{dataset_id}` returns them in full.
  """
  await check_references(session, data, REFERENCES)
  dataset = Dataset.model_validate(data)
  session.add(dataset)
  await session.commit()
  await session.refresh(dataset)
  return dataset


@router.get(
  "/{dataset_id}",
  response_model=DatasetRead,
  response_model_exclude_unset=True,  # leaves out the referenced objects unless they're expanded
  summary="Get a dataset",
  responses={404: {"model": ErrorResponse, "description": "The resource doesn't exist."}},
)
async def get_dataset(dataset_id: int, session: SessionDep, expand: Expand = True):
  """
  Returns a dataset's metadata.

  - **Drafts** are returned too: they're only left out of lists.
  - **Link datasets** have no variables and no source. Their data is at `external_url`.

  The site, contact person, organisation and license are returned in full next to their IDs, unless `expand=false`.
  """
  dataset = await session.get(Dataset, dataset_id)
  if dataset is None:
    raise not_found("Dataset", dataset_id)
  read = DatasetRead.model_validate(dataset)
  if expand:
    await expand_references(session, [read], REFERENCES)
  return read


@router.patch(
  "/{dataset_id}",
  response_model=DatasetRead,
  response_model_exclude_unset=True,  # leaves out the referenced objects, which are never expanded here
  summary="Update a dataset",
  responses={
    404: {"model": ErrorResponse, "description": "The dataset doesn't exist."},
    422: {
      "model": ErrorResponse,
      "description": "The change is invalid, e.g. it changes `kind`, removes a link dataset's `external_url`, or points to a site that doesn't exist.",
    },
  },
)
async def update_dataset(
  dataset_id: int,
  data: Annotated[
    DatasetUpdate,
    Body(
      openapi_examples={
        "publish": {"summary": "Publish a draft", "value": {"status": "published"}},
        "live": {"summary": "Mark as live, updated every 20 minutes", "value": {"update_interval": 1200}},
      }
    ),
  ],
  session: SessionDep,
):
  """
  Only the fields sent are changed, and the others stay as they are. `null` clears a field. Every change increases `version`;
  a request that changes nothing leaves it as it is.

  - `kind` can't be changed.
  - `title` and `status` can't be `null`.

  The response has the IDs of the site, contact person, organisation and license only.

  **Access:** user (own datasets) or owner.
  """
  # Locked, so two concurrent updates can't both increase `version` from the same value
  dataset = await session.get(Dataset, dataset_id, with_for_update=True)
  if dataset is None:
    raise not_found("Dataset", dataset_id)
  changes = data.model_dump(exclude_unset=True)
  if "external_url" in changes:
    if dataset.kind == DatasetKind.link and not changes["external_url"]:
      raise ApiError(422, "validation_error", "external_url: A link dataset needs an external_url")
    if dataset.kind == DatasetKind.data and changes["external_url"]:
      raise ApiError(422, "validation_error", "external_url: Only a link dataset can have an external_url")

  await check_references(session, data, REFERENCES)

  changed = {field: value for field, value in changes.items() if getattr(dataset, field) != value}
  if changed:
    dataset.sqlmodel_update(changed)
    dataset.version += 1
    session.add(dataset)
    await session.commit()
    await session.refresh(dataset)

  return dataset


@router.delete(
  "/{dataset_id}",
  status_code=204,
  summary="Delete a dataset",
  responses={
    204: {"description": "The dataset was deleted."},
    404: {"model": ErrorResponse, "description": "The dataset doesn't exist."},
    **CONFLICT_RESPONSE,
  },
)
async def delete_dataset(dataset_id: int, session: SessionDep):
  """
  Deletes the dataset and everything that belongs to it: variables, issues, source, file index, jobs and collection memberships.

  A dataset can't be deleted while a fetch, sync or rebuild of it is queued or running (409): cancel the job first.

  **Access:** user (own datasets) or owner.
  """
  # Locked, so a job can't be queued between the check and the delete
  dataset = await session.get(Dataset, dataset_id, with_for_update=True)
  if dataset is None:
    raise not_found("Dataset", dataset_id)

  await ensure_no_active_job(session, dataset_id)

  await session.delete(dataset)
  await session.commit()
