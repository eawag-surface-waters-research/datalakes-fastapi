"""
Writes the data of a `timeSeries` dataset to Zarr, one store per partition.

A partition holds one calendar year (UTC) of the dataset's data.

A partition's store has one array per variable (= onr chunk), named after the dataset's variable, all on one time dimension named after the dataset's time variable.
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

from app.enums import VariableRole
from app.file_checks import units_attribute
from app.inspection import utc_datetime
from app.schemas.variables import VariableCommon


class PartitionError(Exception):
  """A file can't be written to a partition, e.g. because a variable isn't on the time dimension."""


@dataclass
class WrittenPartition:
  """What `write_partition` wrote."""

  year: int
  rows: int  # time steps written. 0 if no file has data in the year, and then no store was written
  time_start: datetime | None = None
  time_end: datetime | None = None
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


def write_partition(paths: Sequence[Path], variables: Sequence[VariableCommon], year: int, target: Path) -> WrittenPartition:
  """
  Writes the data of the files in `paths` that falls in `year` to a new Zarr store at `target`, which must not exist yet.
  The time steps are sorted. A time step that's in several files (or several times in one) keeps the values of the file that
  sorts last by path. The files must have passed `check_file`.
  """
  time_name = _time_variable(variables).name
  start, end = (np.datetime64(bound.replace(tzinfo=None), "ns") for bound in partition_bounds(year))

  parts = []
  for path in sorted(paths):
    ds = read_file(path, variables)
    times = ds[time_name].values
    ds = ds.isel({time_name: (times >= start) & (times < end)})  # leave out the time steps that aren't in the partition's year
    if ds.sizes[time_name]:
      parts.append(ds)

  if not parts:
    # no file has data in the year, so no store is written
    return WrittenPartition(year=year, rows=0)

  combined = xr.concat(parts, dim=time_name) if len(parts) > 1 else parts[0]

  # a stable sort keeps the time steps of a later file after those of an earlier one, so keeping the last duplicate keeps the later file's
  combined = combined.isel({time_name: np.argsort(np.asarray(combined[time_name].values), kind="stable")})
  duplicated = combined.indexes[time_name].duplicated(keep="last")

  warnings = []
  if duplicated.any():
    combined = combined.isel({time_name: ~duplicated})
    warnings.append(f"{year}: {int(duplicated.sum())} time steps are in more than one file, kept the values of the file that sorts last by path")

  rows: int = combined.sizes[time_name]
  encoding = {name: {"chunks": (rows,)} for name in combined.variables}
  combined.to_zarr(target, mode="w-", zarr_format=3, consolidated=False, encoding=encoding)

  times = combined[time_name].values
  return WrittenPartition(year=year, rows=rows, time_start=utc_datetime(times[0]), time_end=utc_datetime(times[-1]), warnings=warnings)


def read_file(path: Path, variables: Sequence[VariableCommon]) -> xr.Dataset:
  """
  The file's data for the dataset, in memory: its variables renamed to the dataset's, on one time dimension named after the dataset's
  time variable, with decoded times (time steps without a time left out) and missing values as NaN.
  """
  time_variable = _time_variable(variables)
  with xr.open_dataset(path, decode_times=False, decode_timedelta=False, mask_and_scale=True) as ds:
    time = ds.variables[time_variable.source_name]
    if time.ndim != 1:
      raise PartitionError(
        f"{path.name}: time variable '{time_variable.source_name}' has dimensions ({', '.join(map(str, time.dims))}), a timeSeries needs one"
      )
    dimension = time.dims[0]

    data: dict[str, tuple[tuple, np.ndarray, dict[str, Any]]] = {}  # variable name -> (dims, values, attrs)
    for variable in variables:
      if variable.role == VariableRole.time:
        continue
      var = ds.variables[variable.source_name]
      if var.dims != (dimension,):
        raise PartitionError(
          f"{path.name}: variable '{variable.source_name}' has dimensions ({', '.join(map(str, var.dims))}), a timeSeries needs ({dimension})"
        )
      data[variable.name] = ((time_variable.name,), _apply_decoding(np.asarray(var.values), variable.decoding), _attrs(variable))

    times = _decode_times(time)
    out = xr.Dataset(data, coords={time_variable.name: ((time_variable.name,), times, _attrs(time_variable, unit=False))})
    return out.isel({time_variable.name: ~np.isnat(times)})  # leave out the time steps that have no time after decoding


def _time_variable(variables: Sequence[VariableCommon]) -> VariableCommon:
  times = [variable for variable in variables if variable.role == VariableRole.time]
  if len(times) != 1:
    raise PartitionError(f"a timeSeries needs exactly one time variable, the dataset has {len(times)}")
  return times[0]


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
