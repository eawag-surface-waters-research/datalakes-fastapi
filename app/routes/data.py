import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import numpy as np
from fastapi import APIRouter, Header, Query, Response
from sqlmodel import col, select

from app.database import SessionDep
from app.enums import DatasetKind, FeatureType, VariableRole
from app.errors import ApiError, ErrorResponse, not_found
from app.models import Dataset, Parameter, Partition, Variable
from app.openapi import PUBLIC_TAG
from app.params import parse_time_range
from app.reading import in_read_pool, read_time_series
from app.schemas.data import DataResponse
from app.storage import zarr_dir

router = APIRouter(tags=["Data"])

SUPPORTED_FEATURE_TYPES = [FeatureType.time_series]

TIME_SERIES_EXAMPLE = {
  "dataset_id": 124,
  "version": 2203,
  "feature_type": "timeSeries",
  "query": {
    "variables": ["air_temperature", "wind_speed"],
    "start": "2024-10-30T09:00:00Z",
    "end": "2024-10-30T09:20:00Z",
    "depth_min": None,
    "depth_max": None,
    "mask": False,
  },
  "coordinates": {
    "time": {"unit": "UTC", "dimensions": ["time"], "values": ["2024-10-30T09:00:00.000Z", "2024-10-30T09:10:00.000Z", "2024-10-30T09:20:00.000Z"]},
  },
  "variables": {
    "air_temperature": {"parameter": "Air Temperature", "unit": "degC", "dimensions": ["time"], "flag_for": None, "values": [10.8, 11.0, 11.1]},
    "wind_speed": {"parameter": "Wind Speed", "unit": "m/s", "dimensions": ["time"], "flag_for": None, "values": [2.9, None, 3.4]},
  },
}

Variables = Annotated[
  str | None,
  Query(
    description="Variables to return, by name, separated by commas. Leave it out for all variables. Coordinates such as time are always returned.",
    examples=["air_temperature,wind_speed"],
  ),
]
DataStart = Annotated[
  str | None,
  Query(
    description="Start of the time range, inclusive. A date (`2024-03-01`, meaning midnight UTC) or a full ISO 8601 timestamp. "
    "Leave it out to start at the dataset's first value.",
    examples=["2024-03-01", "2024-03-01T00:00:00Z"],
  ),
]
DataEnd = Annotated[
  str | None,
  Query(
    description="End of the time range, inclusive. A date (`2024-03-31`, meaning the whole day in UTC) or a full ISO 8601 timestamp. "
    "Leave it out to end at the dataset's last value.",
    examples=["2024-03-31", "2024-03-31T23:59:59Z"],
  ),
]
IfNoneMatch = Annotated[
  str | None,
  Header(
    description="The `ETag` of an earlier response. If the dataset hasn't changed since, the answer is 304 without a body.", examples=['"123-v14"']
  ),
]


@router.get(
  "/datasets/{dataset_id}/data",
  response_model=DataResponse,
  summary="Read data",
  tags=[PUBLIC_TAG],
  responses={
    200: {
      "description": "The data. The dataset's version is also in the `ETag` header.",
      "content": {"application/json": {"example": TIME_SERIES_EXAMPLE}},
    },
    304: {"description": "The dataset hasn't changed since the `ETag` sent in `If-None-Match`. No body."},
    400: {
      "model": ErrorResponse,
      "description": "The request makes no sense for this dataset, e.g. `start` is after `end` or a variable it doesn't have.",
    },
    404: {"model": ErrorResponse, "description": "The dataset doesn't exist, has no data yet, or is a link dataset."},
    501: {"model": ErrorResponse, "description": "The dataset's feature type can't be read yet: only `timeSeries` for now."},
  },
)
async def get_data(
  dataset_id: int,
  session: SessionDep,
  variables: Variables = None,
  start: DataStart = None,
  end: DataEnd = None,
  if_none_match: IfNoneMatch = None,
):
  """
  Returns a dataset's values for a time range and a set of variables. The dataset's `version` is in the body and in the `ETag` header.

  - `timeSeries`: variables have the dimension `time`.
  - `timeSeriesProfile` and `profile` datasets can't be read yet (501).

  A time range without data returns empty arrays. Missing values are `null`.

  Values are returned as stored. Flag variables are returned when they're requested, like any variable.

  - **Drafts:** their data can be read like any other.
  - **Link datasets** have no data here (404 with code `link_dataset`). Their data is at their `external_url`.

  **Access:** public.
  """
  dataset = await session.get(Dataset, dataset_id)
  if dataset is None:
    raise not_found("Dataset", dataset_id)
  if dataset.kind == DatasetKind.link:
    raise ApiError(404, "link_dataset", f"Dataset {dataset_id} is a link dataset. Its data is at {dataset.external_url}")
  if dataset.time_start is None or dataset.time_end is None:
    raise ApiError(404, "no_data", f"Dataset {dataset_id} has no data yet. Sync it first.")
  if dataset.feature_type not in SUPPORTED_FEATURE_TYPES:
    raise ApiError(501, "not_implemented", f"Data of {dataset.feature_type} datasets can't be read yet, only of timeSeries datasets")

  start_time, end_time = parse_time_range(start, end)
  dataset_variables = list((await session.exec(select(Variable).where(Variable.dataset_id == dataset_id).order_by(col(Variable.id)))).all())
  time_variable = next(variable for variable in dataset_variables if variable.role == VariableRole.time)
  selected = select_variables(dataset_id, [variable for variable in dataset_variables if variable is not time_variable], variables)

  etag = f'"{dataset_id}-v{dataset.version}"'
  if etag_matches(if_none_match, etag):
    return Response(status_code=304, headers={"ETag": etag})

  query = select(Partition).where(Partition.dataset_id == dataset_id)
  if start_time:
    query = query.where(col(Partition.time_end) >= start_time)
  if end_time:
    query = query.where(col(Partition.time_start) <= end_time)
  stores = [zarr_dir(dataset_id) / partition.path for partition in (await session.exec(query.order_by(col(Partition.time_start)))).all()]

  # the time range that was answered, for `query`
  answered_start, answered_end = start_time or dataset.time_start, end_time or dataset.time_end
  parameters = await parameter_names(session, selected)
  body = {
    "dataset_id": dataset_id,
    "version": dataset.version,
    "feature_type": dataset.feature_type,
    "query": {
      "variables": [variable.name for variable in selected],
      "start": iso_time(answered_start),
      "end": iso_time(answered_end),
      "depth_min": None,
      "depth_max": None,
      "mask": False,
    },
    "coordinates": {time_variable.name: {"unit": "UTC", "dimensions": [time_variable.name], "values": []}},
    "variables": {
      variable.name: {"parameter": parameter, "unit": variable.unit, "dimensions": [time_variable.name], "flag_for": variable.flag_for, "values": []}
      for variable, parameter in zip(selected, parameters, strict=True)
    },
  }

  # reading and writing the JSON both take long for large responses, so both run in the read pool
  content = await in_read_pool(read_json, body, stores, time_variable.name, start_time, end_time)
  return Response(content=content, media_type="application/json", headers={"ETag": etag})


def select_variables(dataset_id: int, available: list[Variable], requested: str | None) -> list[Variable]:
  """The requested variables in the order requested, without duplicates. All of them if none are requested."""
  if requested is None:
    return available

  names = list(dict.fromkeys(name.strip() for name in requested.split(",") if name.strip()))
  if not names:
    raise ApiError(422, "validation_error", "variables: Give the names of the variables, separated by commas, or leave it out for all variables")

  by_name = {variable.name: variable for variable in available}
  unknown = [name for name in names if name not in by_name]
  if unknown:
    quoted = ", ".join(f"'{name}'" for name in unknown)
    raise ApiError(
      400,
      "unknown_variable",
      f"Dataset {dataset_id} has no variable{'s' if len(unknown) > 1 else ''} {quoted}. Variables: {', '.join(by_name) or 'none'}",
    )
  return [by_name[name] for name in names]


def etag_matches(if_none_match: str | None, etag: str) -> bool:
  """
  Whether `If-None-Match` holds the ETag: `*`, or a list of ETags that has it, weak (`W/"…"`) or not.
  An ETag without its quotes matches too.
  """
  if not if_none_match:
    return False

  wanted = etag.strip('"')
  return if_none_match.strip() == "*" or any(tag.strip().removeprefix("W/").strip('"') == wanted for tag in if_none_match.split(","))


async def parameter_names(session: SessionDep, variables: Sequence[Variable]) -> list[str | None]:
  """The name of each variable's parameter, `None` for a variable without one."""
  ids = {variable.parameter_id for variable in variables} - {None}
  names = dict((await session.exec(select(Parameter.id, Parameter.name).where(col(Parameter.id).in_(ids)))).all()) if ids else {}
  return [names.get(variable.parameter_id) for variable in variables]


def read_json(body: dict[str, Any], stores: list[Path], time_name: str, start: datetime | None, end: datetime | None) -> bytes:
  """Reads the values of the variables in `body` from the stores into it, and returns the whole body as JSON."""
  data = read_time_series(stores, time_name, list(body["variables"]), start, end)
  body["coordinates"][time_name]["values"] = iso_times(data.times)
  for name, variable in body["variables"].items():
    variable["values"] = json_values(data.values[name])
  return json.dumps(body, separators=(",", ":"), allow_nan=False).encode()


def iso_time(value: datetime) -> str:
  return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def iso_times(times: np.ndarray) -> list[str]:
  """Times as ISO 8601 strings in UTC, always in milliseconds, e.g. `2024-03-01T00:10:00.000Z`. Finer parts of a time are cut off."""
  return [time + "Z" for time in np.datetime_as_string(times, unit="ms").tolist()]


def json_values(values: np.ndarray) -> list[Any]:
  """Values as (nested) lists, with `None` for values that are missing (NaN) or infinite."""
  if values.dtype.kind == "f":
    missing = ~np.isfinite(values)
    if missing.any():
      return np.where(missing, None, values.astype(object)).tolist()
  return values.tolist()
