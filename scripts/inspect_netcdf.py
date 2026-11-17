"""
Reads one local NetCDF file and suggests the dataset's variables and feature type.

uv run python -m scripts.inspect_netcdf path/to/file.nc [--json] [--sample-size 100000]

Only metadata and a bounded, evenly spaced sample of values are read. Nothing is saved, and no database is needed.
"""

import argparse
import json
import math
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr

SAMPLE_SIZE = 100_000  # numbre of values to read per variable, evenly spaced over every dimension
SENTINELS = [-9999, -999, -99999]  # placeholders old files use for "no value" instead of a _FillValue
HUGE = 1e31  # values this large are no real measurement, likely a scaled placeholder
TINY = 1e-20  # values this small are no real measurement, likely a scaled placeholder

CF_TIME_UNITS_PATTERN = re.compile(r"^\s*\w+\s+since\s+\S", re.IGNORECASE)  # e.g. "seconds since 1970-01-01 00:00:00"
FLAG_MEANINGS_IN_UNITS_PATTERN = re.compile(r"\d\s*[=:]\s*\w", re.IGNORECASE)  # "0 = good, 1 = bad" stored as units
FLAG_SUFFIX_PATTERN = re.compile(r"_(flag|flags|qa|qc|qual|quality)$", re.IGNORECASE)  # suffixes for flag variables, e.g. temp_qual, salinity_flag
FLAG_NAME_PATTERN = re.compile(r"(^|_)(flag|flags|qa|qc|qual|quality)(_|$)", re.IGNORECASE)  # e.g. temp_qual, salinity_flag

FEATURE_TYPES = {"timeseries": "timeSeries", "timeseriesprofile": "timeSeriesProfile", "profile": "profile"}
MATLAB_DATENUM_LIMIT = 31_536_000  # the old converter's guess: smaller values are MATLAB datenums, larger ones unix seconds
VERTICAL_NAMES = {"depth", "pressure", "press", "pres", "altitude", "height", "z", "level", "lev"}  # names that indicate a vertical axis
LATITUDE_NAMES = {"latitude", "lat"}  # names that indicate a latitude axis
LONGITUDE_NAMES = {"longitude", "lon", "long", "lng"}  # names that indicate a longitude axis
ATTRIBUTE_KEYS = {
  "latitude": ["latitude", "lat"],
  "longitude": ["longitude", "lon", "long", "lng"],
  "altitude": ["altitude", "elevation"],
  "depth": ["depth"],
  "max_depth": ["max_depth"],
  "time": ["time", "datetime", "date", "start_time", "time_coverage_start"],
}  # global attributes read when the file has no variable for that axis


class InspectError(Exception):
  def __init__(self, code: str, message: str):
    super().__init__(message)
    self.code = code


def inspect_file(path: Path, sample_size: int = SAMPLE_SIZE) -> dict[str, Any]:
  """Reads one NetCDF file and returns the suggested feature type and variables, with warnings for whatever would fail the build."""
  if not path.name.lower().endswith(".nc"):
    raise InspectError("unreadable_file", f"File '{path.name}' is not a NetCDF file (.nc). Only NetCDF is supported.")
  started = time.perf_counter()
  try:
    ds = xr.open_dataset(path, decode_times=False, decode_timedelta=False, mask_and_scale=False)
  except Exception as exc:
    raise InspectError("unreadable_file", f"File '{path.name}' can't be read as NetCDF: {exc}") from exc

  with ds:
    warnings: list[str] = []
    variables: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    axes: dict[str, str] = {}  # source_name -> time, vertical, latitude or longitude
    taken: set[str] = set()
    time_range = None

    for source_name, var in ds.variables.items():
      source_name = str(source_name)

      axis = _axis(source_name, var)
      if axis:
        axes[source_name] = axis
      if var.ndim == 0:
        # a variable needs at least one dimension
        skipped.append({"source_name": source_name, "value": _plain(var.values), "unit": _text(var.attrs.get("units")), "axis": axis})
        continue

      sample = _sample(var, sample_size)
      decoding, stats = _missing_values(var, sample)

      role = _role(source_name, var, axis, ds)
      if role == "time":
        time_range = _time_range(source_name, var, sample, warnings)
        decoding = None
      elif decoding:
        # placeholder values found in the sample, but not declared as _FillValue or missing_value
        infinite = f", {stats['infinite']} of them infinite" if stats.get("infinite") else ""
        warnings.append(f"{source_name}: placeholder values found in the sample{infinite}, suggested decoding {decoding}.")

      if stats.get("missing") == stats["count"]:  # only missing values in the sample
        warnings.append(f"{source_name}: no valid values in the sample ({stats['count']} values); the variable may be empty in this file.")
      if stats.get("tiny"):  # almost-zero values in the sample
        tiny = stats["tiny"]
        warnings.append(
          f"{source_name}: {tiny['count']} values are almost zero but not zero (e.g. {tiny['example']:g}), which looks like a placeholder. "
          "Add it to decoding.missing_values if it is one."
        )

      if var.dtype.kind not in ["i", "u", "f"]:  # 'i' = signed int, 'u' = unsigned int, 'f' = float
        warnings.append(f"{source_name}: values are not numeric ({var.dtype}). Only numeric variables can be stored.")

      unit = _unit(source_name, var, warnings) if role != "time" else None
      hints = {"standard_name": _text(var.attrs.get("standard_name")), "long_name": _text(var.attrs.get("long_name")), "sample": stats}

      if role == "flag":
        hints["flag_values"] = _plain(var.attrs.get("flag_values"))
        hints["flag_meanings"] = _text(var.attrs.get("flag_meanings"))
        if unit and FLAG_MEANINGS_IN_UNITS_PATTERN.search(unit):
          # e.g. units = "0 = nothing to report, 1 = more investigation"
          warnings.append(
            f"{source_name}: `units` holds the flag meanings ({unit!r}), not a unit. Suggested no unit; "
            "CF puts these in the flag_values and flag_meanings attributes."
          )
          hints["flag_meanings"] = hints["flag_meanings"] or unit
          unit = None

      variables.append(
        {
          "source_name": source_name,
          "name": _suggest_name(source_name, taken),
          "role": role,
          "dimensions": [str(dim) for dim in var.dims],
          "unit": unit,
          "parameter_id": None,
          "flag_for": None,
          "description": _text(var.attrs.get("long_name")),
          "decoding": decoding,
          "hints": hints,
        }
      )

    _link_flags(ds, variables, warnings)
    _check_unit_spelling(variables, warnings)

    for dim in ds.dims:
      if dim not in ds.variables:
        warnings.append(f"Dimension '{dim}' has no coordinate variable, so its values are just indexes.")

    if not any(v["role"] == "time" for v in variables) and not any(s["axis"] == "time" for s in skipped):
      warnings.append("The file has no time variable.")

    feature_type, reason = _feature_type(ds, variables, axes, warnings)
    # within a cast, time may repeat or run backwards; in a time series it must strictly increase
    order = (time_range or {}).pop("order", None)
    if order and feature_type != "profile":
      warnings.append(f"'time' is not strictly increasing: {order}.")

    found_axes = set(axes.values()) | {s["axis"] for s in skipped if s["axis"]}
    from_attributes = _from_attributes(ds.attrs, found_axes, warnings)

    if not ({"latitude", "longitude"} <= found_axes | set(from_attributes)):
      warnings.append("No latitude and longitude in the variables or global attributes; set the dataset's location by hand.")

    return {
      "file": path.name,
      "size": path.stat().st_size,
      "seconds": round(time.perf_counter() - started, 3),
      "dimensions": {str(dim): int(size) for dim, size in ds.sizes.items()},
      "feature_type": feature_type,
      "feature_type_reason": reason,
      "time_range": time_range,
      "from_attributes": from_attributes,
      "scalars": skipped,
      "variables": variables,
      "global_attributes": {str(key): _plain(value) for key, value in ds.attrs.items()},
      "warnings": warnings,
    }


def _axis(name: str, var: xr.Variable) -> str | None:
  """What a variable describes (time, vertical, latitude or longitude), from its name, standard_name, axis and units."""
  names = {name.lower(), str(var.attrs.get("standard_name", "")).lower()}  # name and standard_name, lowercased
  units = var.attrs.get("units")
  axis = str(var.attrs.get("axis", "")).upper()

  # a variable is a time coordinate if it has a name or standard_name in TIME_NAMES,
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


def _role(name: str, var: xr.Variable, axis: str | None, ds: xr.Dataset) -> str:
  """What the variable is for: time, flag, coordinate or data."""
  if axis == "time":
    return "time"

  # a flag variable if it has a flag attribute, or its name looks like a flag
  if ("flag_values" in var.attrs) or ("flag_masks" in var.attrs) or ("flag_meanings" in var.attrs) or FLAG_NAME_PATTERN.search(name):
    return "flag"

  # a dimension coordinate (e.g. depth(depth)), or a coordinate such as latitude(time) of a moving station
  if axis or (var.ndim == 1 and var.dims[0] == name) or name in ds.coords:
    return "coordinate"

  return "data"


def _sample(var: xr.Variable, size: int) -> np.ndarray:
  """At most about `size` values, evenly spaced over every dimension, read with one strided slice."""
  if var.size <= size:
    return np.asarray(var.values)
  step = math.ceil((var.size / size) ** (1 / var.ndim))
  return np.asarray(var[tuple(slice(None, None, step) for _ in var.dims)].values)


def _missing_values(var: xr.Variable, sample: np.ndarray) -> tuple[dict[str, Any] | None, dict[str, Any]]:
  """Suggests a `decoding` for placeholders that aren't declared as _FillValue or missing_value, and summarises the sample."""
  stats: dict[str, Any] = {"count": int(sample.size)}
  if sample.dtype.kind not in "iuf" or sample.size == 0:
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
    stats["tiny"] = {"count": int(tiny.size), "example": _plain(tiny[0])}
  if valid.size:
    stats["min"] = _plain(valid.min())
    stats["max"] = _plain(valid.max())
  return decoding or None, stats


def _time_range(name: str, var: xr.Variable, sample: np.ndarray, warnings: list[str]) -> dict[str, Any] | None:
  """Decodes the first and last time from CF units. Without CF units the build rejects the file (invalid_time_units)."""
  units = var.attrs.get("units")
  values = sample[np.isfinite(sample)] if sample.dtype.kind == "f" else sample
  if not (isinstance(units, str) and CF_TIME_UNITS_PATTERN.match(units)):
    guess = ""
    if values.size and values.dtype.kind in "iuf":
      guess = " The old API would have read them as " + ("MATLAB datenums." if values.min() < MATLAB_DATENUM_LIMIT else "unix seconds.")
    warnings.append(
      f"invalid_time_units: '{name}' has units {units!r}, not CF units like 'seconds since 1970-01-01 00:00:00'. The build will reject this file.{guess}"
    )
    return None
  if values.size == 0:
    warnings.append(f"'{name}' has no valid values in the sample.")
    return None
  order = None
  if values.ndim == 1:
    steps = np.diff(values)
    problems = [f"{n} {what}" for n, what in [(int((steps == 0).sum()), "repeated"), (int((steps < 0).sum()), "decreasing")] if n]
    order = ", ".join(problems) or None
  try:
    first, last = xr.coding.times.decode_cf_datetime(np.array([values.min(), values.max()]), units, var.attrs.get("calendar"))
  except (ValueError, OverflowError) as exc:
    warnings.append(f"invalid_time_units: '{name}' can't be decoded with units {units!r}: {exc}")
    return None
  return {"start": _plain(first), "end": _plain(last), "units": units, "calendar": var.attrs.get("calendar"), "count": int(var.size), "order": order}


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


def _check_unit_spelling(variables: list[dict[str, Any]], warnings: list[str]) -> None:
  """Warns about units spelled differently in the same file, e.g. mg/l and mg/L."""
  spellings: dict[str, set[str]] = {}
  for v in variables:
    if v["unit"]:
      spellings.setdefault(v["unit"].lower().replace(" ", ""), set()).add(v["unit"])
  for found in spellings.values():
    if len(found) > 1:
      warnings.append(f"Units {sorted(found)} are spelled differently in the same file; pick one spelling for the variables.")


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


def _link_flags(ds: xr.Dataset, variables: list[dict[str, Any]], warnings: list[str]) -> None:
  """Sets `flag_for` from the CF `ancillary_variables` attribute, or from names like temp_qual. Only data variables can be flagged."""
  roles = {v["source_name"]: v["role"] for v in variables}
  names = {v["source_name"]: v["name"] for v in variables if v["role"] == "data"}
  for v in variables:
    if v["role"] != "data":
      continue
    for flag in str(ds.variables[v["source_name"]].attrs.get("ancillary_variables", "")).split():
      flag_variable = next((f for f in variables if f["source_name"] == flag and f["role"] == "flag"), None)
      if flag_variable:
        flag_variable["flag_for"] = v["name"]
  for v in variables:
    if v["role"] == "flag" and v["flag_for"] is None:
      base = FLAG_SUFFIX_PATTERN.sub("", v["source_name"])
      if base != v["source_name"] and base in names:
        v["flag_for"] = names[base]
      elif base in roles:
        warnings.append(f"{v['source_name']}: looks like a flag for '{base}', a {roles[base]} variable; only data variables can be flagged.")


def _feature_type(ds: xr.Dataset, variables: list[dict[str, Any]], axes: dict[str, str], warnings: list[str]) -> tuple[str | None, str]:
  """Guesses the feature type (timeSeries, timeSeries, profile) from the variables and their dimensions. Returns None if it can't be guessed."""
  # the dimensions of the 1-D time and vertical coordinates, e.g. time(time) and depth(depth), or depth(time) in a cast
  time_dims = {ds.variables[name].dims[0] for name, axis in axes.items() if axis == "time" and ds.variables[name].ndim == 1}
  vertical_dims = {ds.variables[name].dims[0] for name, axis in axes.items() if axis == "vertical" and ds.variables[name].ndim == 1}
  data = [v for v in variables if v["role"] == "data"]

  # e.g. a(depth, wavelength) for spectra: the storage layout only knows time and depth
  other_dims: dict[str, list[str]] = {}
  for v in data:
    for dim in v["dimensions"]:
      if dim not in time_dims | vertical_dims:
        other_dims.setdefault(dim, []).append(v["source_name"])
  for dim, names in other_dims.items():
    warnings.append(f"Dimension '{dim}' is neither time nor depth, used by {', '.join(names)}; the storage layout only covers time and depth.")

  declared = next((str(value) for key, value in ds.attrs.items() if str(key).lower() == "featuretype"), None)
  if declared:
    feature_type = FEATURE_TYPES.get(declared.lower())
    if feature_type:
      return feature_type, f"declared in the global attribute featureType = {declared!r}"
    warnings.append(f"The global attribute featureType = {declared!r} is not one of {sorted(FEATURE_TYPES.values())}.")

  # one cast per file: time and depth both change along the same dimension, e.g. depth(time) or time(depth)
  shared = time_dims & vertical_dims
  if shared:
    return "profile", f"one cast: time and depth both vary along '{', '.join(sorted(shared))}'"

  on_both = [v["source_name"] for v in data if time_dims & set(v["dimensions"]) and vertical_dims & set(v["dimensions"])]
  on_time = [v["source_name"] for v in data if time_dims & set(v["dimensions"])]
  on_vertical = [v["source_name"] for v in data if vertical_dims & set(v["dimensions"])]
  if on_both:
    reason = f"data on time and a vertical dimension: {', '.join(on_both)}"
    time_only = [v["source_name"] for v in data if set(v["dimensions"]) <= time_dims]
    if time_only:
      reason += f"; also on time only: {', '.join(time_only)}"
    return "timeSeriesProfile", reason
  if on_time:
    return "timeSeries", f"data on time without a vertical dimension: {', '.join(on_time)}"
  if on_vertical:
    return "profile", f"data on a vertical dimension without a time dimension: {', '.join(on_vertical)}"
  warnings.append("Couldn't guess the feature type: no data variable is on a time or vertical dimension.")
  return None, "no data variable on a time or vertical dimension"


def _from_attributes(attrs: dict[str, Any], axes: set[str], warnings: list[str]) -> dict[str, Any]:
  """Location, altitude, depth and time from global attributes, for what the file has no variable for."""
  lowered = {str(key).lower(): value for key, value in attrs.items()}
  axis_of = {"latitude": "latitude", "longitude": "longitude", "depth": "vertical", "time": "time"}
  found: dict[str, Any] = {}
  for field, keys in ATTRIBUTE_KEYS.items():
    if axis_of.get(field) in axes:
      continue
    key = next((k for k in keys if k in lowered), None)
    if key is None:
      continue
    value = _plain(lowered[key])
    if field == "time":
      try:
        datetime.fromisoformat(str(value))
      except ValueError:
        warnings.append(f"Global attribute '{key}' = {value!r} is not an ISO 8601 time (open question 29).")
    found[field] = {"attribute": key, "value": value}
  return found


def _text(value: Any) -> str | None:
  """Turns a value into a string, or None if it is empty."""
  if value is None:
    return None
  if not isinstance(value, str):
    value = ", ".join(str(v) for v in np.atleast_1d(value))
  return value.strip() or None


def _plain(value: Any) -> Any:
  """Turns numpy values into JSON-friendly Python ones."""
  if isinstance(value, np.ndarray):
    return value.item() if value.ndim == 0 else [_plain(v) for v in value.tolist()]
  if isinstance(value, np.datetime64):
    return np.datetime_as_string(value, unit="s")
  if isinstance(value, np.generic):
    value = value.item()
  if isinstance(value, float) and not math.isfinite(value):
    return None
  if isinstance(value, (list, tuple)):
    return [_plain(v) for v in value]
  if hasattr(value, "isoformat"):
    return value.isoformat()
  return value


def _short(value: Any) -> str:
  """Shortens a value for printing in the report."""
  if value is None:
    return "-"
  return f"{value:.6g}" if isinstance(value, float) else str(value)


def print_report(result: dict[str, Any]) -> None:
  print(f"File        {result['file']} ({result['size'] / 1e6:.1f} MB), read in {result['seconds']} s")
  print(f"Dimensions  {', '.join(f'{dim}={size}' for dim, size in result['dimensions'].items())}")
  print(f"Feature     {result['feature_type'] or '?'} ({result['feature_type_reason']})")
  if result["time_range"]:
    tr = result["time_range"]
    print(f"Time        {tr['start']} to {tr['end']} ({tr['count']} values, {tr['units']})")
  for field, found in result["from_attributes"].items():
    print(f"{field.capitalize():<12}{found['value']} (global attribute '{found['attribute']}')")
  for scalar in result["scalars"]:
    unit = f" {scalar['unit']}" if scalar["unit"] else ""
    print(f"Scalar      {scalar['source_name']} = {scalar['value']}{unit} (no dimension, not suggested as a variable)")

  print("\nSuggested variables")
  rows = [["source_name", "name", "role", "dimensions", "unit", "flag_for", "decoding", "sample min..max (missing/count)", "description"]]
  for v in result["variables"]:
    stats = v["hints"]["sample"]
    # Format the sample data
    sample = f"{_short(stats.get('min'))}..{_short(stats.get('max'))} ({stats.get('missing', '-')}/{stats['count']})"
    decoding = json.dumps(v["decoding"]) if v["decoding"] else ""
    row = [v["source_name"], v["name"], v["role"], ",".join(v["dimensions"]), v["unit"] or "", v["flag_for"] or "", decoding, sample]
    rows.append([*row, v["description"] or ""])
  widths = [max(len(str(row[i])) for row in rows) for i in range(len(rows[0]))]
  for row in rows:
    print("  " + "  ".join(str(cell).ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip())

  if result["warnings"]:
    print("\nWarnings")
    for warning in result["warnings"]:
      print(f"  - {warning}")


if __name__ == "__main__":
  parser = argparse.ArgumentParser(prog="python -m scripts.inspect_netcdf")
  parser.add_argument("file", type=Path, help="A local NetCDF file (.nc).")
  parser.add_argument("--json", action="store_true", help="Print the result as JSON, close to what the endpoint will return.")
  parser.add_argument("--sample-size", type=int, default=SAMPLE_SIZE, help="Read at most about this many values per variable.")
  args = parser.parse_args()
  if not args.file.is_file():
    sys.exit(f"No such file: {args.file}")
  try:
    result = inspect_file(args.file, args.sample_size)
  except InspectError as exc:
    sys.exit(f"{exc.code}: {exc}")
  if args.json:
    print(json.dumps(result, indent=2, ensure_ascii=False))
  else:
    print_report(result)
