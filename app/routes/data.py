import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import numpy as np
from fastapi import APIRouter, Header, Query, Response
from sqlmodel import col, select

from app.database import SessionDep
from app.enums import DataFormat, DatasetKind, FeatureType, VariableRole
from app.errors import ApiError, ErrorResponse, not_found
from app.models import Dataset, Parameter, Partition, Variable
from app.openapi import PUBLIC_TAG
from app.params import parse_time_range
from app.partitions import depth_variable
from app.reading import Data, in_read_pool, read_data
from app.schemas.data import DataResponse
from app.storage import zarr_dir

router = APIRouter(tags=["Data"])

SUPPORTED_FEATURE_TYPES = [FeatureType.time_series, FeatureType.time_series_profile]

TIME_SERIES_PROFILE_EXAMPLE = {
  "dataset_id": 123,
  "version": 14,
  "feature_type": "timeSeriesProfile",
  "query": {
    "variables": ["temperature"],
    "start": "2024-03-01T00:00:00Z",
    "end": "2024-03-01T00:10:00Z",
    "depth_min": 0,
    "depth_max": 1.5,
    "mask": False,
  },
  "coordinates": {
    "time": {"unit": "UTC", "dimensions": ["time"], "values": ["2024-03-01T00:00:00.000Z", "2024-03-01T00:10:00.000Z"]},
    "depth": {"unit": "m", "dimensions": ["depth"], "values": [0.5, 1.0, 1.5]},
  },
  "variables": {
    "temperature": {
      "parameter": "Water temperature",
      "unit": "degC",
      "dimensions": ["time", "depth"],
      "flag_for": None,
      "values": [[6.1, 6.0, 5.9], [6.1, 6.0, None]],
    },
  },
}

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
DepthMin = Annotated[
  float | None,
  Query(ge=0, description="Shallowest depth to return, in metres, inclusive. Only for datasets with depth.", examples=[0]),
]
DepthMax = Annotated[
  float | None,
  Query(ge=0, description="Deepest depth to return, in metres, inclusive. Only for datasets with depth.", examples=[10]),
]
Format = Annotated[
  DataFormat,
  Query(description="`json` or `binary`"),
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
      "content": {
        "application/json": {
          "examples": {
            "timeSeries": {"summary": "A weather station (timeSeries)", "value": TIME_SERIES_EXAMPLE},
            "timeSeriesProfile": {"summary": "A thermistor chain (timeSeriesProfile), 0–1.5 m", "value": TIME_SERIES_PROFILE_EXAMPLE},
          }
        }
      },
    },
    304: {"description": "The dataset hasn't changed since the `ETag` sent in `If-None-Match`. No body."},
    400: {
      "model": ErrorResponse,
      "description": "The request makes no sense for this dataset, e.g. `start` is after `end`, a variable it doesn't have, "
      "or a depth range for a dataset without depth.",
    },
    404: {"model": ErrorResponse, "description": "The dataset doesn't exist, has no data yet, or is a link dataset."},
    501: {"model": ErrorResponse, "description": "`profile` datasets can't be read yet."},
  },
)
async def get_data(
  dataset_id: int,
  session: SessionDep,
  variables: Variables = None,
  start: DataStart = None,
  end: DataEnd = None,
  depth_min: DepthMin = None,
  depth_max: DepthMax = None,
  format: Format = DataFormat.json,
  if_none_match: IfNoneMatch = None,
):
  """
  Returns a dataset's values for a time range, a depth range and a set of variables. The dataset's `version` is in the body and in the
  `ETag` header.

  - `timeSeries`: variables have the dimension `time`.
  - `timeSeriesProfile`: variables have the dimensions `time` and `depth`, or only `time` (e.g. a surface temperature). If the depths
    changed during the period (a thermistor chain with more sensors, say), `depth` holds all the depths that occur, and a value is `null`
    where that depth had no sensor at that time.
  - `profile` datasets can't be read yet (501).

  A time range without data returns empty arrays. Missing values are `null`.

  **Binary format** (`format=binary`, media type `application/vnd.datalakes.data`): the same document, but each `values` is replaced
  by where its values are, `{"dtype": "float32", "shape": [51240, 48], "offset": 409920}`. The body is:

  1. the length of the header in bytes, as a little-endian 32-bit unsigned integer;
  2. the header, the document as UTF-8 JSON, padded with spaces so that what follows starts at a multiple of 8 bytes;
  3. the values of each coordinate and variable, little-endian, at its `offset` from the end of the header, each at a multiple of 8.

  Times are `float64` milliseconds since 1970-01-01 UTC, depths `float64`, and all variables `float32`, with
  NaN for missing values.

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
    raise ApiError(501, "not_implemented", f"Data of {dataset.feature_type} datasets can't be read yet")

  start_time, end_time = parse_time_range(start, end)
  dataset_variables = list((await session.exec(select(Variable).where(Variable.dataset_id == dataset_id).order_by(col(Variable.id)))).all())
  time_variable = next(variable for variable in dataset_variables if variable.role == VariableRole.time)
  depth = depth_variable(dataset_variables, dataset.feature_type)
  check_depth_range(dataset_id, depth, depth_min, depth_max)
  coordinates = [variable for variable in (time_variable, depth) if variable is not None]
  selected = select_variables(dataset_id, [variable for variable in dataset_variables if variable not in coordinates], variables)

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
      "depth_min": depth_min,
      "depth_max": depth_max,
      "mask": False,
    },
    "coordinates": {time_variable.name: {"unit": "UTC", "dimensions": [time_variable.name], "values": []}},
    "variables": {
      variable.name: {
        "parameter": parameter,
        "unit": variable.unit,
        "dimensions": [time_variable.name, depth.name] if has_depth(variable, depth) else [time_variable.name],
        "flag_for": variable.flag_for,
        "values": [],
      }
      for variable, parameter in zip(selected, parameters, strict=True)
    },
  }
  if depth is not None:
    body["coordinates"][depth.name] = {"unit": depth.unit, "dimensions": [depth.name], "values": []}

  # reading and writing the data both take long for large responses, so both run in the read pool
  names = {variable.name: has_depth(variable, depth) for variable in selected}
  content = await in_read_pool(read_body, format, body, stores, time_variable.name, names, start_time, end_time, depth, depth_min, depth_max)
  return Response(content=content, media_type=MEDIA_TYPES[format], headers={"ETag": etag})


def has_depth(variable: Variable, depth: Variable | None) -> bool:
  return depth is not None and depth.name in variable.dimensions


def check_depth_range(dataset_id: int, depth: Variable | None, depth_min: float | None, depth_max: float | None) -> None:
  if depth is None and (depth_min is not None or depth_max is not None):
    raise ApiError(400, "no_depth", f"Dataset {dataset_id} has no depth coordinate, so depth_min and depth_max can't be used")
  if depth_min is not None and depth_max is not None and depth_min > depth_max:
    raise ApiError(400, "invalid_depth_range", f"depth_min ({depth_min:g}) is deeper than depth_max ({depth_max:g})")


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


MEDIA_TYPES = {DataFormat.json: "application/json", DataFormat.binary: "application/vnd.datalakes.data"}


def read_body(
  format: DataFormat,
  body: dict[str, Any],
  stores: list[Path],
  time_name: str,
  names: dict[str, bool],
  start: datetime | None,
  end: datetime | None,
  depth: Variable | None,
  depth_min: float | None,
  depth_max: float | None,
) -> bytes:
  """Reads the values of the variables in `body` from the stores, and returns the whole body in the format."""
  depth_name = depth.name if depth else None
  data = read_data(stores, time_name, names, start, end, depth_name, depth_min, depth_max)
  if format == DataFormat.binary:
    return binary_body(body, data, time_name, depth_name)
  return json_body(body, data, time_name, depth_name)


def json_body(body: dict[str, Any], data: Data, time_name: str, depth_name: str | None) -> bytes:
  body["coordinates"][time_name]["values"] = iso_times(data.times)
  if depth_name is not None and data.depths is not None:
    body["coordinates"][depth_name]["values"] = json_values(data.depths)
  for name, variable in body["variables"].items():
    variable["values"] = json_values(data.values[name])
  return json.dumps(body, separators=(",", ":"), allow_nan=False).encode()


def binary_body(body: dict[str, Any], data: Data, time_name: str, depth_name: str | None) -> bytes:
  """The header (the body, with where each array is instead of its values), then the arrays."""
  time = body["coordinates"][time_name]
  time["unit"] = "milliseconds since 1970-01-01T00:00:00Z"

  # time is milliseconds since 1970-01-01 UTC as float64
  # "<f8" ensures little-endian float64
  arrays = [(time, data.times.astype("datetime64[ms]").astype("int64").astype("<f8"))]

  # depth is float64, little-endian
  if depth_name is not None and data.depths is not None:
    arrays.append((body["coordinates"][depth_name], data.depths.astype("<f8")))

  # each variable is float32, little-endian, with NaN for missing values
  for name, variable in body["variables"].items():
    values = data.values[name].astype("<f4")
    values[~np.isfinite(values)] = np.nan
    arrays.append((variable, values))

  # concatenate the arrays (time, depth, each variable) into one bytes object
  chunks, offset = [], 0
  for entry, array in arrays:
    entry["values"] = {"dtype": array.dtype.name, "shape": list(array.shape), "offset": offset}
    chunk = array.tobytes()
    chunks.append(chunk + bytes(-len(chunk) % 8))  # the next array starts at a multiple of 8
    offset += len(chunks[-1])

  # the header is the body, with each array replaced by where its values are
  header = json.dumps(body, separators=(",", ":"), allow_nan=False).encode()
  header += b" " * (-(4 + len(header)) % 8)  # the arrays start at a multiple of 8

  # bytes: 4 bytes for the length of the header, then the header, then the arrays
  return len(header).to_bytes(4, "little") + header + b"".join(chunks)


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
