"""
Inspects one NetCDF file and suggests the dataset's variables and feature type, for `POST /datasets/{dataset_id}/inspect`.

Only metadata and a bounded, evenly spaced sample of values are read.
Nothing is saved.
The rules are heuristics on names, attributes and dimensions.
Anything they can't decide becomes a warning.
See `docs/netcdf_inspection.md` for the rules.
"""

import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np
import xarray as xr
from xarray.coding.times import decode_cf_datetime

from app.enums import FeatureType, VariableRole
from app.errors import ApiError
from app.schemas.inspect import InspectedVariable, InspectResult, Location, SampleSummary, ScalarVariable, TimeRange

SAMPLE_SIZE = 100_000  # number of values to read per variable, evenly spaced over every dimension
SENTINELS = [-9999, -999, -99999]  # placeholders old files use for "no value" instead of a _FillValue
HUGE = 1e31  # values this large are no real measurement, likely a scaled placeholder
TINY = 1e-20  # values this small are no real measurement, likely a scaled placeholder

CF_TIME_UNITS_PATTERN = re.compile(r"^\s*\w+\s+since\s+\S", re.IGNORECASE)  # e.g. "seconds since 1970-01-01 00:00:00"
FLAG_MEANINGS_IN_UNITS_PATTERN = re.compile(r"\d\s*[=:]\s*\w", re.IGNORECASE)  # "0 = good, 1 = bad" stored as units
FLAG_SUFFIX_PATTERN = re.compile(r"_(flag|flags|qa|qc|qual|quality)$", re.IGNORECASE)  # suffixes for flag variables, e.g. temp_qual, salinity_flag
FLAG_NAME_PATTERN = re.compile(r"(^|_)(flag|flags|qa|qc|qual|quality)(_|$)", re.IGNORECASE)  # e.g. temp_qual, salinity_flag

FEATURE_TYPES = {feature_type.value.lower(): feature_type for feature_type in FeatureType}  # for the featureType attribute, case-insensitive
MATLAB_DATENUM_LIMIT = 31_536_000  # the old converter's guess: smaller values are MATLAB datenums, larger ones unix seconds
VERTICAL_NAMES = {"depth", "pressure", "press", "pres", "altitude", "height", "z", "level", "lev"}  # names that indicate a vertical axis
LATITUDE_NAMES = {"latitude", "lat"}  # names that indicate a latitude axis
LONGITUDE_NAMES = {"longitude", "lon", "long", "lng"}  # names that indicate a longitude axis
TIME_ATTRIBUTES = ["time", "datetime", "date", "start_time", "time_coverage_start"]  # global attributes that may hold the time of a cast
LISTED_FIRST = {VariableRole.time: 0, VariableRole.coordinate: 1}  # the rest keeps the file's order


def inspect_file(path: Path, sample_size: int = SAMPLE_SIZE) -> InspectResult:
  """
  Reads one NetCDF file and returns the suggested feature type and variables.
  Raises `ApiError` 422 `unreadable_file` if it isn't a `.nc` file or can't be opened.
  """
  if not path.name.lower().endswith(".nc"):
    raise ApiError(422, "unreadable_file", f"File '{path.name}' is not a NetCDF file (.nc). Only NetCDF is supported.")
  try:
    ds = xr.open_dataset(path, decode_times=False, decode_timedelta=False, mask_and_scale=False)
  except Exception as exc:
    raise ApiError(422, "unreadable_file", f"File '{path.name}' can't be read as NetCDF: {exc}") from exc

  with ds:
    warnings: list[str] = []
    variables: list[InspectedVariable] = []
    scalars: list[ScalarVariable] = []
    axes: dict[str, str] = {}  # source_name -> time, vertical, latitude or longitude
    taken: set[str] = set()
    time_range = None
    time_order = None

    for key, var in ds.variables.items():
      source_name = str(key)
      axis = _axis(source_name, var)
      if axis:
        axes[source_name] = axis
      if var.ndim == 0:
        # a variable needs at least one dimension
        scalars.append(ScalarVariable(source_name=source_name, value=_plain(var.values), unit=_text(var.attrs.get("units")), axis=axis))
        continue

      sample = _sample(var, sample_size)
      decoding, stats = _missing_values(var, sample)

      role = _role(source_name, var, axis, ds)
      if role == VariableRole.time:
        time_range, time_order = _time_range(source_name, var, sample, warnings)
        decoding = None
      elif decoding:
        # placeholder values found in the sample, but not declared as _FillValue or missing_value
        infinite = f", {stats['infinite']} of them infinite" if stats.get("infinite") else ""
        warnings.append(f"{source_name}: placeholder values found in the sample{infinite}, suggested decoding {decoding}.")

      if stats["count"] and stats.get("missing") == stats["count"]:  # only missing values in the sample
        warnings.append(f"{source_name}: no valid values in the sample ({stats['count']} values). The variable may be empty in this file.")
      if tiny := stats.get("tiny"):  # almost-zero values in the sample
        warnings.append(
          f"{source_name}: {tiny['count']} values are almost zero but not zero (e.g. {tiny['example']:g}), which looks like a placeholder. "
          "Add it to decoding.missing_values if it is one."
        )
      if var.dtype.kind not in ["i", "u", "f"]:  # 'i' = signed int, 'u' = unsigned int, 'f' = float
        warnings.append(f"{source_name}: values are not numeric ({var.dtype}). Only numeric variables can be stored.")

      unit = _unit(source_name, var, warnings) if role != VariableRole.time else None
      if role == VariableRole.flag and unit and FLAG_MEANINGS_IN_UNITS_PATTERN.search(unit):
        # e.g. units = "0 = nothing to report, 1 = more investigation"
        warnings.append(
          f"{source_name}: `units` holds the flag meanings ({unit!r}), not a unit. Suggested no unit. "
          "CF puts these in the flag_values and flag_meanings attributes."
        )
        unit = None

      variables.append(
        InspectedVariable(
          source_name=source_name,
          name=_suggest_name(source_name, taken),
          role=role,
          dimensions=[str(dim) for dim in var.dims],
          unit=unit,
          description=_text(var.attrs.get("long_name")),
          decoding=decoding,
          shape=[int(size) for size in var.shape],
          attributes={str(name): _plain(value) for name, value in var.attrs.items()},
          sample=SampleSummary(count=stats["count"], missing=stats.get("missing", 0), min=stats.get("min"), max=stats.get("max")),
        )
      )

    _link_flags(ds, variables)
    _check_unit_spelling(variables, warnings)
    _check_unnecessary_dimensions(ds, variables, axes, warnings)

    for dim in ds.dims:
      if dim not in ds.variables:
        warnings.append(f"Dimension '{dim}' has no coordinate variable, so its values are just indexes.")

    has_time = any(v.role == VariableRole.time for v in variables) or any(s.axis == "time" for s in scalars)
    if not has_time:
      _check_time_attribute(ds.attrs, warnings)

    feature_type, reason = _feature_type(ds, variables, axes, warnings)
    # within a cast, time may repeat or run backwards; in a time series it must strictly increase
    if time_order and feature_type != FeatureType.profile:
      warnings.append(f"'time' is not strictly increasing: {time_order}.")

    has_coordinates = {"latitude", "longitude"} <= {axis for name, axis in axes.items() if ds.variables[name].ndim > 0}
    location = None if has_coordinates else _location(ds.attrs, scalars, warnings)
    if not has_coordinates and location is None:
      warnings.append("No latitude and longitude in the variables or global attributes. Set the dataset's location by hand.")

    return InspectResult(
      file=path.name,
      format=_format(path),
      dimensions={str(dim): int(size) for dim, size in ds.sizes.items()},
      feature_type=feature_type,
      feature_type_reason=reason,
      time_range=time_range,
      location=location,
      variables=sorted(variables, key=lambda v: LISTED_FIRST.get(v.role, len(LISTED_FIRST))),
      scalars=scalars,
      global_attributes={str(name): _plain(value) for name, value in ds.attrs.items()},
      warnings=warnings,
    )


def _format(path: Path) -> Literal["netcdf3", "netcdf4"]:
  """`netcdf3` for the classic format, `netcdf4` for HDF5-based files, from the file's first bytes."""
  with path.open("rb") as file:
    return "netcdf3" if file.read(3) == b"CDF" else "netcdf4"


def _axis(name: str, var: xr.Variable) -> str | None:
  """What a variable describes (time, vertical, latitude or longitude), from its name, standard_name, axis and units."""
  names = {name.lower(), str(var.attrs.get("standard_name", "")).lower()}  # name and standard_name, lowercased
  units = var.attrs.get("units")
  axis = str(var.attrs.get("axis", "")).upper()

  # a variable is a time coordinate if its name or standard_name is "time",
  # or its axis is T,
  # or its units are CF time units
  if ("time" in names) or (axis == "T") or (isinstance(units, str) and CF_TIME_UNITS_PATTERN.match(units)):
    return "time"

  # a variable is a vertical coordinate if it has a name or standard_name in VERTICAL_NAMES,
  # or its axis is Z,
  # or it has a positive attribute,
  # or it is a 1-D variable whose dimension is itself and its name contains "depth"
  is_dimension = var.ndim == 1 and var.dims[0] == name  # has one dimension and that dimension is itself, e.g. depth(depth)
  if any(n in VERTICAL_NAMES for n in names) or (axis == "Z") or ("positive" in var.attrs) or (is_dimension and "depth" in name.lower()):
    return "vertical"

  if any(n in LATITUDE_NAMES for n in names):
    return "latitude"
  if any(n in LONGITUDE_NAMES for n in names):
    return "longitude"

  # variable isn't a coordinate
  return None


def _role(name: str, var: xr.Variable, axis: str | None, ds: xr.Dataset) -> VariableRole:
  """What the variable is for: time, flag, coordinate or data."""
  if axis == "time":
    return VariableRole.time

  # a flag variable if it has a flag attribute, or its name looks like a flag
  if "flag_values" in var.attrs or "flag_masks" in var.attrs or "flag_meanings" in var.attrs or FLAG_NAME_PATTERN.search(name):
    return VariableRole.flag

  # a dimension coordinate (e.g. depth(depth)), or a coordinate such as latitude(time) of a moving station
  if axis or (var.ndim == 1 and var.dims[0] == name) or name in ds.coords:
    return VariableRole.coordinate

  return VariableRole.data


def _sample(var: xr.Variable, size: int) -> np.ndarray:
  """At most about `size` values, evenly spaced over every dimension, read with one strided slice."""
  if var.size <= size:
    return np.asarray(var.values)
  step = math.ceil((var.size / size) ** (1 / var.ndim))
  return np.asarray(var[tuple(slice(None, None, step) for _ in var.dims)].values)


def _missing_values(var: xr.Variable, sample: np.ndarray) -> tuple[dict[str, Any] | None, dict[str, Any]]:
  """Suggests a `decoding` for placeholders that aren't declared as _FillValue or missing_value, and summarises the sample."""
  stats: dict[str, Any] = {"count": int(sample.size)}
  if sample.dtype.kind not in ["i", "u", "f"] or sample.size == 0:
    return None, stats
  declared = [float(v) for key in ("_FillValue", "missing_value") if key in var.attrs for v in np.atleast_1d(var.attrs[key])]
  missing = np.isnan(sample) if sample.dtype.kind == "f" else np.zeros(sample.shape, dtype=bool)
  for value in declared:
    if not math.isnan(value):
      missing |= sample == value

  decoding: dict[str, Any] = {}
  found = [value for value in SENTINELS if value not in declared and np.any(sample[~missing] == value)]
  if found:
    decoding["missing_values"] = found
    missing |= np.isin(sample, found)
  # unset floats in files without a _FillValue are the NetCDF default fill, 9.97e36
  if sample.dtype.kind == "f" and np.any(sample[~missing] > HUGE):
    decoding["valid_max"] = HUGE
    if np.any(np.isinf(sample)):
      stats["infinite"] = int(np.isinf(sample).sum())
    missing |= sample > HUGE

  valid = sample[~missing]
  stats["missing"] = int(missing.sum())
  tiny = valid[(valid != 0) & (np.abs(valid) < TINY)] if sample.dtype.kind == "f" else valid[:0]
  if tiny.size:
    stats["tiny"] = {"count": int(tiny.size), "example": float(tiny[0])}
  if valid.size:
    stats["min"] = float(valid.min())
    stats["max"] = float(valid.max())
  return decoding or None, stats


def _time_range(name: str, var: xr.Variable, sample: np.ndarray, warnings: list[str]) -> tuple[TimeRange | None, str | None]:
  """
  Decodes the first and last time from CF units. Without CF units the build rejects the file (invalid_time_units).
  Also describes time values that repeat or decrease.
  """
  units = var.attrs.get("units")
  values = sample[np.isfinite(sample)] if sample.dtype.kind == "f" else sample
  if not (isinstance(units, str) and CF_TIME_UNITS_PATTERN.match(units)):
    guess = ""
    if values.size and values.dtype.kind in ["i", "u", "f"]:
      guess = " The old API would have read them as " + ("MATLAB datenums." if values.min() < MATLAB_DATENUM_LIMIT else "unix seconds.")
    warnings.append(
      f"invalid_time_units: '{name}' has units {units!r}, not CF units like 'seconds since 1970-01-01 00:00:00'. The build will reject this file.{guess}"
    )
    return None, None
  if values.size == 0:
    warnings.append(f"'{name}' has no valid values in the sample.")
    return None, None

  order = None
  if values.ndim == 1:
    steps = np.diff(values)
    problems = [f"{n} {what}" for n, what in [(int((steps == 0).sum()), "repeated"), (int((steps < 0).sum()), "decreasing")] if n]
    order = ", ".join(problems) or None

  calendar = _text(var.attrs.get("calendar"))
  try:
    first, last = decode_cf_datetime(np.array([values.min(), values.max()]), units, calendar)
    start, end = utc_datetime(first), utc_datetime(last)
  except (ValueError, OverflowError, TypeError) as exc:
    warnings.append(f"invalid_time_units: '{name}' can't be decoded with units {units!r}: {exc}")
    return None, order
  return TimeRange(start=start, end=end, count=int(var.size), units=units, calendar=calendar), order


def utc_datetime(value: Any) -> datetime:
  """A UTC datetime from a decoded time: a numpy datetime64, or a cftime date for other calendars."""
  if isinstance(value, np.datetime64):
    return value.astype("datetime64[us]").item().replace(tzinfo=UTC)
  return datetime(value.year, value.month, value.day, value.hour, value.minute, value.second, tzinfo=UTC)


def _unit(name: str, var: xr.Variable, warnings: list[str]) -> str | None:
  """Warns if the units attribute is a list, and returns the first value as a string."""
  units = var.attrs.get("units")
  if units is None:
    return None
  if isinstance(units, str):
    return units.strip() or None
  values = [str(value) for value in np.atleast_1d(units)]
  warnings.append(
    f"{name}: `units` is a list {values}, not a string. Suggested {values[0]!r}, but the build compares units as strings (unit_mismatch), "
    "so fix the file."
  )
  return values[0]


def _check_unit_spelling(variables: list[InspectedVariable], warnings: list[str]) -> None:
  """Warns about units spelled differently in the same file, e.g. mg/l and mg/L."""
  spellings: dict[str, set[str]] = {}
  for v in variables:
    if v.unit:
      spellings.setdefault(v.unit.lower().replace(" ", ""), set()).add(v.unit)

  for found in spellings.values():
    if len(found) > 1:
      warnings.append(f"Units {sorted(found)} are spelled differently in the same file. Pick one spelling for the variables.")


def _suggest_name(source_name: str, taken: set[str]) -> str:
  """Suggests a variable name from the source_name, lowercased and with non-alphanumeric characters replaced by underscores. Ensures uniqueness."""
  name = (
    re.sub(r"[^a-z0-9]+", "_", source_name.lower()).strip("_") or "variable"
  )  # replace non-alphanumeric with underscores, lowercase, and strip leading/trailing underscores. "variable" is used as the fallback

  if not name[0].isalpha():  # if the name doesn't start with a letter, prefix it with "v_"
    name = f"v_{name}"

  unique, n = name, 2
  while unique in taken:  # ensure the name is unique by appending a number if necessary
    unique, n = f"{name}_{n}", n + 1
  taken.add(unique)

  return unique


def _link_flags(ds: xr.Dataset, variables: list[InspectedVariable]) -> None:
  """Sets `flag_for` from the CF `ancillary_variables` attribute, or from names like temp_qual. Only data variables can be flagged."""
  names = {v.source_name: v.name for v in variables if v.role == VariableRole.data}
  flags = {v.source_name: v for v in variables if v.role == VariableRole.flag}
  for v in variables:
    if v.role != VariableRole.data:
      continue
    for flag in str(ds.variables[v.source_name].attrs.get("ancillary_variables", "")).split():
      if flag in flags:
        flags[flag].flag_for = v.name
  for flag in flags.values():
    if flag.flag_for is not None:
      continue
    base = FLAG_SUFFIX_PATTERN.sub("", flag.source_name)
    if base != flag.source_name and base in names:
      flag.flag_for = names[base]


def _axis_dims(ds: xr.Dataset, axes: dict[str, str], axis: str) -> set[str]:
  """The dimensions of the 1-D coordinates of an axis, e.g. time(time) and depth(depth), or depth(time) in a cast."""
  return {str(ds.variables[name].dims[0]) for name, found in axes.items() if found == axis and ds.variables[name].ndim == 1}


def _check_unnecessary_dimensions(ds: xr.Dataset, variables: list[InspectedVariable], axes: dict[str, str], warnings: list[str]) -> None:
  """
  Warns about dimensions of length 1 other than time and depth, e.g. temp(time, site) with a single site.
  They add nothing, and the build rejects variables on them (unnecessary_dimension).
  """
  allowed = _axis_dims(ds, axes, "time") | _axis_dims(ds, axes, "vertical")  # a file may have a single time step or a single depth
  used: dict[str, list[str]] = {}
  for v in variables:
    for dim in v.dimensions:
      if dim not in allowed and ds.sizes[dim] == 1 and dim != v.source_name:  # not site(site) itself
        used.setdefault(dim, []).append(v.source_name)
  for dim, names in used.items():
    warnings.append(
      f"unnecessary_dimension: dimension '{dim}' has only one value, used by {', '.join(names)}. "
      "Remove it from the files. The build will reject variables on it."
    )


def _feature_type(ds: xr.Dataset, variables: list[InspectedVariable], axes: dict[str, str], warnings: list[str]) -> tuple[FeatureType | None, str]:
  """Guesses the feature type (timeSeries, timeSeriesProfile, profile) from the variables and their dimensions. Returns None if it can't be guessed."""
  time_dims = _axis_dims(ds, axes, "time")
  vertical_dims = _axis_dims(ds, axes, "vertical")
  data = [v for v in variables if v.role == VariableRole.data]

  # e.g. a(depth, wavelength) for spectra: the storage layout only knows time and depth.
  # Dimensions of length 1 are left out: _check_unnecessary_dimensions warns about them
  other_dims: dict[str, list[str]] = {}
  for v in data:
    for dim in v.dimensions:
      if dim not in time_dims | vertical_dims and ds.sizes[dim] > 1:
        other_dims.setdefault(dim, []).append(v.source_name)
  for dim, names in other_dims.items():
    warnings.append(f"Dimension '{dim}' is neither time nor depth, used by {', '.join(names)}. Only time and depth are supported.")

  declared = next((str(value) for key, value in ds.attrs.items() if str(key).lower() == "featuretype"), None)
  if declared:
    if feature_type := FEATURE_TYPES.get(declared.lower()):
      return feature_type, f"declared in the global attribute featureType = {declared!r}"
    warnings.append(f"The global attribute featureType = {declared!r} is not one of {', '.join(FeatureType)}.")

  # one cast per file: time and depth both change along the same dimension, e.g. depth(time) or time(depth)
  if shared := time_dims & vertical_dims:
    return FeatureType.profile, f"one cast: time and depth both vary along '{', '.join(sorted(shared))}'"

  on_both = [
    v.source_name for v in data if time_dims & set(v.dimensions) and vertical_dims & set(v.dimensions)
  ]  # data on both time and vertical dimensions
  on_time = [v.source_name for v in data if time_dims & set(v.dimensions)]  # data on a time dimension without a vertical dimension
  on_vertical = [v.source_name for v in data if vertical_dims & set(v.dimensions)]  # data on a vertical dimension without a time dimension

  if on_both:
    # If there are variables on both time and vertical dimensions, it's a time series profile
    reason = f"data on time and a vertical dimension: {', '.join(on_both)}"
    if time_only := [v.source_name for v in data if set(v.dimensions) <= time_dims]:
      reason += f". Also on time only: {', '.join(time_only)}"
    return FeatureType.time_series_profile, reason
  if on_time:
    return FeatureType.time_series, f"data on time without a vertical dimension: {', '.join(on_time)}"
  if on_vertical:
    return FeatureType.profile, f"data on a vertical dimension without a time dimension: {', '.join(on_vertical)}"

  warnings.append("Couldn't guess the feature type: no data variable is on a time or vertical dimension.")
  return None, "no data variable on a time or vertical dimension"


def _location(attrs: dict[str, Any], scalars: list[ScalarVariable], warnings: list[str]) -> Location | None:
  """Latitude and longitude from scalar variables, or else from global attributes. `None` unless both are found."""
  lowered = {str(key).lower(): value for key, value in attrs.items()}
  found: dict[str, float] = {}
  for axis, keys in (("latitude", LATITUDE_NAMES), ("longitude", LONGITUDE_NAMES)):
    scalar = next((s for s in scalars if s.axis == axis), None)
    key = next((k for k in sorted(keys) if k in lowered), None)
    value = scalar.value if scalar else lowered[key] if key else None
    if value is None:
      continue
    try:
      found[axis] = float(_plain(value))
    except TypeError, ValueError:
      warnings.append(f"The {axis} {value!r} is not a number.")
  return Location(**found) if len(found) == 2 else None


def _check_time_attribute(attrs: dict[str, Any], warnings: list[str]) -> None:
  """For a file without a time variable: the time must come from a global attribute in ISO 8601."""
  lowered = {str(key).lower(): value for key, value in attrs.items()}
  key = next((k for k in TIME_ATTRIBUTES if k in lowered), None)
  if key is None:
    warnings.append("The file has no time variable and no time in its global attributes.")
    return
  value = lowered[key]
  try:
    datetime.fromisoformat(str(value))
  except ValueError:
    warnings.append(f"The file has no time variable, and the global attribute '{key}' = {value!r} is not an ISO 8601 time.")


def _text(value: Any) -> str | None:
  """Turns a value into a string, or None if it is empty."""
  if value is None:
    return None
  if not isinstance(value, str):
    value = ", ".join(str(v) for v in np.atleast_1d(value))
  return value.strip() or None


def _plain(value: Any) -> Any:
  """Turns numpy values into JSON-friendly Python ones"""
  if isinstance(value, np.ndarray):
    return _plain(value.item()) if value.ndim == 0 else [_plain(v) for v in value.tolist()]
  if isinstance(value, np.datetime64):
    return np.datetime_as_string(value, unit="s")
  if isinstance(value, np.generic):
    value = value.item()
  if isinstance(value, float) and not math.isfinite(value):
    return None
  if isinstance(value, (list, tuple)):
    return [_plain(v) for v in value]
  if isinstance(value, bytes):
    return value.decode(errors="replace")
  return value
