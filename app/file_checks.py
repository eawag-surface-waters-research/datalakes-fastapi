"""
Checks an archived file against the dataset's variables before it's processed.
The variables are the schema every file must match:
- The file is NetCDF (`.nc`) and can be opened
- It has every variable's `source_name`, with the declared dimensions
- No variable is on a dimension of length 1 other than the time and depth dimensions, e.g. temp(time, site) with a single site
- Where the variable has a `unit`, the file's `units` attribute is the same string, apart from surrounding whitespace. If `units` is a list, its first value counts.
- Time variables have CF units, e.g. `seconds since 1970-01-01 00:00:00`, and their values decode.

A file collects all its problems.
Variables in the file that the dataset doesn't use are allowed, but they're named in a warning.
Only metadata and the time values are read.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path

import numpy as np
import xarray as xr
from xarray.coding.times import decode_cf_datetime

from app.enums import VariableRole
from app.inspection import CF_TIME_UNITS_PATTERN, utc_datetime
from app.schemas.variables import VariableCommon

CF_TIME_UNITS_EXAMPLE = "seconds since 1970-01-01 00:00:00"


class ProblemCode(StrEnum):
  unsupported_format = "unsupported_format"  # not a `.nc` file
  unreadable_file = "unreadable_file"  # a `.nc` file that can't be opened
  missing_variable = "missing_variable"
  wrong_dimensions = "wrong_dimensions"
  unnecessary_dimension = "unnecessary_dimension"  # a dimension of length 1 that isn't time or depth, e.g. a single site
  unit_mismatch = "unit_mismatch"
  invalid_time_units = "invalid_time_units"


@dataclass(frozen=True)
class Problem:
  code: ProblemCode
  file: str  # the file's name
  detail: str  # what was expected and found, without the file's name, so the same problem in many files can be grouped
  variable: str | None = None  # the name of the dataset's variable it's about, if any

  @property
  def message(self) -> str:
    """The problem with the file's name, e.g. for the file index."""
    return f"{self.file}: {self.detail}"


@dataclass
class FileCheck:
  """The outcome of checking one file."""

  problems: list[Problem] = field(default_factory=list)
  warnings: list[str] = field(default_factory=list)
  time_start: datetime | None = None  # the time the file covers, from its time variable. `None` if it can't be decoded
  time_end: datetime | None = None
  depths: tuple[float, ...] | None = None  # the values of the depth variable, if one was given and it's 1-D. Decides the depth layout

  @property
  def ok(self) -> bool:
    return not self.problems

  @property
  def unsupported(self) -> bool:
    """Whether the file isn't NetCDF. A built dataset ignores such files instead of failing them."""
    return any(problem.code == ProblemCode.unsupported_format for problem in self.problems)


def check_file(path: Path, variables: Sequence[VariableCommon], depth_variable: VariableCommon | None = None) -> FileCheck:
  """
  Checks the file at `path` against the dataset's variables. With a `depth_variable` (a `timeSeriesProfile`'s), its values are
  recorded in `depths`, as they decide which depth layout the file belongs to.
  """
  check = FileCheck()

  if not path.name.lower().endswith(".nc"):
    check.problems.append(
      Problem(
        ProblemCode.unsupported_format,
        path.name,
        "not a NetCDF file (.nc)",
      )
    )
    return check

  try:
    ds = xr.open_dataset(path, decode_times=False, decode_timedelta=False, mask_and_scale=False)
  except Exception as exc:  # noqa: BLE001
    check.problems.append(Problem(ProblemCode.unreadable_file, path.name, f"can't be read as NetCDF: {exc}"))
    return check

  with ds:
    # a file may have a single time step or a single depth, but no other dimension of length 1
    allowed = {dim for variable in variables if variable.role == VariableRole.time or variable is depth_variable for dim in variable.dimensions}

    for variable in variables:
      label = _label(variable)
      var = ds.variables.get(variable.source_name)
      if var is None:
        check.problems.append(
          Problem(
            ProblemCode.missing_variable,
            path.name,
            f"no variable '{variable.source_name}'{label}",
            variable.name,
          )
        )
        continue

      _check_dimensions(path.name, variable, var, check)
      _check_unnecessary_dimensions(path.name, variable, var, allowed, check)

      if variable is depth_variable and var.ndim == 1:
        check.depths = tuple(float(value) for value in np.asarray(var.values, dtype="float64"))
      if variable.role == VariableRole.time:
        _check_time(path.name, variable, var, check)
      elif variable.unit is not None:
        _check_unit(path.name, variable, var, check)

    used = {variable.source_name for variable in variables}
    unused = sorted(str(key) for key in ds.variables if str(key) not in used)
    if unused:
      check.warnings.append(f"{path.name}: variables not used by the dataset: {', '.join(unused)}")

  return check


def _label(variable: VariableCommon) -> str:
  """How a message names the dataset's variable, if its name differs from the source_name."""
  return "" if variable.name == variable.source_name else f" (source of '{variable.name}')"


def _check_dimensions(name: str, variable: VariableCommon, var: xr.Variable, check: FileCheck) -> None:
  found = [str(dim) for dim in var.dims]
  if sorted(found) != sorted(variable.dimensions):
    check.problems.append(
      Problem(
        ProblemCode.wrong_dimensions,
        name,
        f"variable '{variable.source_name}'{_label(variable)} has dimensions ({', '.join(found)}), expected ({', '.join(variable.dimensions)})",
        variable.name,
      )
    )


def _check_unnecessary_dimensions(name: str, variable: VariableCommon, var: xr.Variable, allowed: set[str], check: FileCheck) -> None:
  unnecessary = [str(dim) for dim, size in zip(var.dims, var.shape, strict=True) if str(dim) not in allowed and size == 1]

  if unnecessary:
    check.problems.append(
      Problem(
        ProblemCode.unnecessary_dimension,
        name,
        f"variable '{variable.source_name}'{_label(variable)} has dimensions of length 1 other than time and depth: ({', '.join(unnecessary)}). "
        "Remove them from the file",
        variable.name,
      )
    )


def _check_unit(name: str, variable: VariableCommon, var: xr.Variable, check: FileCheck) -> None:
  assert variable.unit is not None
  units = units_attribute(var)
  if units == variable.unit.strip():
    return
  what = "no units attribute" if units is None else f"units {units!r}"
  check.problems.append(
    Problem(
      ProblemCode.unit_mismatch,
      name,
      f"variable '{variable.source_name}'{_label(variable)} has {what}, expected {variable.unit!r}",
      variable.name,
    )
  )


def _check_time(name: str, variable: VariableCommon, var: xr.Variable, check: FileCheck) -> None:
  """Checks that the time variable has CF units and decodes, and records the time range the file covers."""
  units = units_attribute(var)
  if not (units and CF_TIME_UNITS_PATTERN.match(units)):
    check.problems.append(
      Problem(
        ProblemCode.invalid_time_units,
        name,
        f"time variable '{variable.source_name}'{_label(variable)} has units {units!r}, not CF units like {CF_TIME_UNITS_EXAMPLE!r}",
        variable.name,
      )
    )
    return

  values = np.asarray(var.values).ravel()
  if values.dtype.kind not in ["i", "u", "f"]:
    check.problems.append(
      Problem(
        ProblemCode.invalid_time_units,
        name,
        f"time variable '{variable.source_name}'{_label(variable)} has {values.dtype} values, expected numbers",
        variable.name,
      )
    )
    return

  # leave out missing values, which would decode to nonsense dates
  values = values[np.isfinite(values)] if values.dtype.kind == "f" else values
  for attribute in ["_FillValue", "missing_value"]:
    if (missing := var.attrs.get(attribute, var.encoding.get(attribute))) is not None:
      values = values[~np.isin(values, np.atleast_1d(missing))]
  if values.size == 0:
    check.warnings.append(f"{name}: time variable '{variable.source_name}'{_label(variable)} has no values")
    return

  calendar = var.attrs.get("calendar")
  try:
    first, last = decode_cf_datetime(np.array([values.min(), values.max()]), units, calendar)
    check.time_start, check.time_end = utc_datetime(first), utc_datetime(last)
  except (ValueError, OverflowError, TypeError) as exc:
    check.problems.append(
      Problem(
        ProblemCode.invalid_time_units,
        name,
        f"time variable '{variable.source_name}'{_label(variable)} can't be decoded with units {units!r}: {exc}",
        variable.name,
      )
    )


def units_attribute(var: xr.Variable) -> str | None:
  """The `units` attribute without surrounding whitespace. If it's a list, its first value. `None` if it's missing or empty."""
  units = var.attrs.get("units")
  if units is not None and not isinstance(units, str):
    values = np.atleast_1d(units)
    units = str(values[0]) if values.size else None
  return (units or "").strip() or None
