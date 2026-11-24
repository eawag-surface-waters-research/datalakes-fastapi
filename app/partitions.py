"""
Writes the data of a `timeSeries` or `timeSeriesProfile` dataset to Zarr, one store per partition.

A partition holds one calendar year (UTC) of the dataset's data.

A partition's store has one array per variable (= one chunk), named after the dataset's variable:
- `timeSeries`: every variable is on one time dimension, named after the dataset's time variable.
- `timeSeriesProfile`: variables are on time or on time and depth. The depth dimension is named after the dataset's
  depth variable: the one coordinate variable on a dimension other than time. Every file in a partition must
  have the same depth values.
Missing values are NaN: `_FillValue`, `missing_value` and the variable's `decoding` are applied.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr
from xarray.coding.times import decode_cf_datetime

from app.enums import FeatureType, VariableRole
from app.file_checks import units_attribute
from app.inspection import utc_datetime
from app.schemas.variables import VariableCommon

SUPPORTED_FEATURE_TYPES = [FeatureType.time_series, FeatureType.time_series_profile]


class PartitionError(Exception):
  """A file can't be written to a partition, e.g. because a variable isn't on the time dimension."""


@dataclass
class WrittenPartition:
  """What `write_partition` wrote."""

  year: int
  rows: int  # time steps written. 0 if no file has data in the year, and then no store was written
  time_start: datetime | None = None
  time_end: datetime | None = None
  depth_min: float | None = None  # the range of the depth axis, for a `timeSeriesProfile`. `None` for a `timeSeries`
  depth_max: float | None = None
  warnings: list[str] = field(default_factory=list)


def partition_of(time: datetime) -> int:
  """The partition a time falls in: its year in UTC."""
  return time.astimezone(UTC).year


def partition_bounds(year: int) -> tuple[datetime, datetime]:
  """The time range of a partition, the end excluded."""
  return datetime(year, 1, 1, tzinfo=UTC), datetime(year + 1, 1, 1, tzinfo=UTC)


def partitions_between(start: datetime, end: datetime) -> list[int]:
  """The partitions a time range touches."""
  return list(range(partition_of(start), partition_of(end) + 1))


def write_partition(
  paths: Sequence[Path], variables: Sequence[VariableCommon], feature_type: FeatureType, year: int, target: Path
) -> WrittenPartition:
  """
  Writes the data of the files in `paths` that falls in `year` to a new Zarr store at `target`, which must not exist yet.
  The time steps are sorted. A time step that's in several files (or several times in one) keeps the values of the file that
  sorts last by path. The files must have passed `check_file`.
  """
  time_name = _time_variable(variables).name
  depth_variable = _depth_variable(variables, feature_type)
  start, end = (np.datetime64(bound.replace(tzinfo=None), "ns") for bound in partition_bounds(year))

  parts: list[tuple[Path, xr.Dataset]] = []
  for path in sorted(paths):
    ds = read_file(path, variables, feature_type)
    times = ds[time_name].values
    ds = ds.isel({time_name: (times >= start) & (times < end)})  # leave out the time steps that aren't in the partition's year
    if ds.sizes[time_name]:
      parts.append((path, ds))

  if not parts:
    # no file has data in the year, so no store is written
    return WrittenPartition(year=year, rows=0)

  if depth_variable is not None:
    _check_same_depths([(path, ds[depth_variable.name].values) for path, ds in parts], year)

  datasets = [ds for _, ds in parts]
  # the depth axis is the same in every part (checked above), so it's taken from the first
  combined = (
    xr.concat(datasets, dim=time_name, data_vars="all", coords="minimal", compat="override", join="exact") if len(datasets) > 1 else datasets[0]
  )

  # a stable sort keeps the time steps of a later file after those of an earlier one, so keeping the last duplicate keeps the later file's
  combined = combined.isel({time_name: np.argsort(np.asarray(combined[time_name].values), kind="stable")})
  duplicated = combined.indexes[time_name].duplicated(keep="last")

  warnings = []
  if duplicated.any():
    combined = combined.isel({time_name: ~duplicated})
    warnings.append(f"{year}: {int(duplicated.sum())} time steps are in more than one file, kept the values of the file that sorts last by path")

  rows: int = combined.sizes[time_name]
  encoding = {name: {"chunks": combined[name].shape} for name in combined.variables}  # one chunk per variable
  combined.to_zarr(target, mode="w-", zarr_format=3, consolidated=False, encoding=encoding)

  times = combined[time_name].values
  written = WrittenPartition(year=year, rows=rows, time_start=utc_datetime(times[0]), time_end=utc_datetime(times[-1]), warnings=warnings)
  if depth_variable is not None:
    depths = combined[depth_variable.name].values
    if np.isfinite(depths).any():
      written.depth_min, written.depth_max = float(np.nanmin(depths)), float(np.nanmax(depths))
  return written


def read_file(path: Path, variables: Sequence[VariableCommon], feature_type: FeatureType) -> xr.Dataset:
  """
  Reads a file and returns its data as an xarray Dataset.
  The file's data for the dataset, in memory: its variables renamed to the dataset's, on a time dimension named after the dataset's
  time variable (and for a `timeSeriesProfile` a depth dimension named after its depth variable), with decoded times (time steps
  without a time left out) and missing values as NaN.
  """
  if feature_type not in SUPPORTED_FEATURE_TYPES:
    raise PartitionError(f"{feature_type} datasets can't be written yet")

  time_variable = _time_variable(variables)
  depth_variable = _depth_variable(variables, feature_type)

  with xr.open_dataset(path, decode_times=False, decode_timedelta=False, mask_and_scale=True) as ds:
    time = ds.variables[time_variable.source_name]
    if time.ndim != 1:
      raise PartitionError(f"{path.name}: time variable '{time_variable.source_name}' has dimensions ({_dims(time)}), a {feature_type} needs one")
    time_dim = time.dims[0]

    depth_dim = None
    if depth_variable is not None:
      depth = ds.variables[depth_variable.source_name]
      if depth.ndim != 1 or depth.dims[0] == time_dim:
        raise PartitionError(
          f"{path.name}: depth variable '{depth_variable.source_name}' has dimensions ({_dims(depth)}), a {feature_type} needs one other than time"
        )
      depth_dim = depth.dims[0]

    # a variable's dimensions in the file, and what they're called in the store
    names = {time_dim: time_variable.name}
    allowed = [(time_dim,)]
    if depth_variable is not None:
      names[depth_dim] = depth_variable.name
      allowed += [(time_dim, depth_dim), (depth_dim, time_dim)]

    data: dict[str, tuple[tuple, np.ndarray, dict[str, Any]]] = {}  # variable name -> (dims, values, attrs)
    for variable in variables:
      if variable is time_variable or variable is depth_variable:
        continue

      var = ds.variables[variable.source_name]
      if var.dims not in allowed:
        expected = " or ".join(f"({', '.join(map(str, dims))})" for dims in allowed[:2])
        raise PartitionError(f"{path.name}: variable '{variable.source_name}' has dimensions ({_dims(var)}), a {feature_type} needs {expected}")
      if var.dims == (depth_dim, time_dim):
        var = var.transpose(time_dim, depth_dim)  # the store always has time first, then depth
      dims = tuple(names[dim] for dim in var.dims)

      data[variable.name] = (dims, _apply_decoding(np.asarray(var.values), variable.decoding), _attrs(variable))

    times = _decode_times(time)
    coords = {time_variable.name: ((time_variable.name,), times, _attrs(time_variable, unit=False))}
    if depth_variable is not None:
      depths = _apply_decoding(np.asarray(ds.variables[depth_variable.source_name].values), depth_variable.decoding)
      coords[depth_variable.name] = ((depth_variable.name,), depths, _attrs(depth_variable))

    out = xr.Dataset(data, coords=coords)
    return out.isel({time_variable.name: ~np.isnat(times)})  # leave out the time steps that have no time after decoding


def _time_variable(variables: Sequence[VariableCommon]) -> VariableCommon:
  times = [variable for variable in variables if variable.role == VariableRole.time]
  if len(times) != 1:
    raise PartitionError(f"a dataset needs exactly one time variable, it has {len(times)}")
  return times[0]


def _depth_variable(variables: Sequence[VariableCommon], feature_type: FeatureType) -> VariableCommon | None:
  """
  The depth variable of a `timeSeriesProfile`: the one coordinate variable on a single dimension other than time's`.
  `None` for a `timeSeries`.
  """
  if feature_type != FeatureType.time_series_profile:
    return None

  time_dims = _time_variable(variables).dimensions
  depths = [
    variable
    for variable in variables
    if variable.role == VariableRole.coordinate and len(variable.dimensions) == 1 and variable.dimensions != time_dims
  ]

  if len(depths) != 1:
    found = f": {', '.join(variable.name for variable in depths)}" if depths else ""
    raise PartitionError(
      f"a timeSeriesProfile needs exactly one coordinate variable on its depth dimension, e.g. depth(depth), the dataset has {len(depths)}{found}"
    )
  return depths[0]


def _check_same_depths(depths: list[tuple[Path, np.ndarray]], year: int) -> None:
  """Fails if the files of a partition don't all have the same depth values. Depth layouts that change aren't supported yet."""
  first_path, first = depths[0]
  for path, values in depths[1:]:
    if not np.array_equal(values, first, equal_nan=True):
      raise PartitionError(
        f"{year}: {path.name} has the depths {_format_depths(values)}, {first_path.name} has {_format_depths(first)}. "
        "Files with different depths can't be in the same year yet"
      )


def _format_depths(values: np.ndarray, limit: int = 10) -> str:
  """Formats the depth values of a file for an error message, e.g. `[0, 10, 20, …] (12 values)"""
  shown = ", ".join(f"{value:g}" for value in values[:limit])
  return f"[{shown}{', …' if len(values) > limit else ''}] ({len(values)} values)"


def _dims(var: xr.Variable) -> str:
  return ", ".join(map(str, var.dims))


def _decode_times(time: xr.Variable) -> np.ndarray:
  """The time values as `datetime64[ns]`. Missing times (NaN after masking) are NaT."""
  units = units_attribute(time)
  values = np.asarray(time.values)
  missing = np.isnan(values) if values.dtype.kind == "f" else np.zeros(values.shape, dtype=bool)
  decoded = np.full(values.shape, np.datetime64("NaT", "ns"))

  if (~missing).any():
    # decode the non-missing values
    # `decode_cf_datetime` returns `cftime.datetime` for calendars other than the standard one, which can't be cast to `datetime64[ns]`, so convert them to UTC first
    valid = decode_cf_datetime(values[~missing], units, time.attrs.get("calendar"))
    if valid.dtype.kind != "M":  # cftime dates, from a calendar other than the standard one
      valid = np.array([np.datetime64(utc_datetime(value).replace(tzinfo=None), "ns") for value in valid])
    decoded[~missing] = valid.astype("datetime64[ns]")

  return decoded


def _apply_decoding(values: np.ndarray, decoding: dict[str, Any] | None) -> np.ndarray:
  """Turns the placeholders in the variable's `decoding` into NaN: `missing_values`, and values outside `valid_min` and `valid_max`."""
  if not decoding or values.dtype.kind not in ["i", "u", "f"]:
    return values

  missing = np.zeros(values.shape, dtype=bool)
  if decoding.get("missing_values"):
    missing |= np.isin(values, decoding["missing_values"])
  if decoding.get("valid_min") is not None:
    missing |= values < decoding["valid_min"]
  if decoding.get("valid_max") is not None:
    missing |= values > decoding["valid_max"]

  if not missing.any():
    return values

  values = values.astype(values.dtype if values.dtype.kind == "f" else np.float64, copy=True)  # needs to be float to hold NaN
  values[missing] = np.nan

  return values


def _attrs(variable: VariableCommon, unit: bool = True) -> dict[str, str]:
  attrs = {}
  if unit and variable.unit:
    attrs["units"] = variable.unit
  if variable.description:
    attrs["description"] = variable.description

  return attrs
