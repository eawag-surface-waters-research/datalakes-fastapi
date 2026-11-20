from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from app.enums import VariableRole
from app.file_checks import ProblemCode, check_file
from app.models import Variable

UNIX_UNITS = "seconds since 1970-01-01 00:00:00"
TIMES = np.array([1_700_000_000, 1_700_000_600, 1_700_001_200], dtype="float64")


def variable(name: str, role=VariableRole.data, dimensions=("time",), unit: str | None = None, source_name: str | None = None) -> Variable:
  return Variable(dataset_id=1, name=name, source_name=source_name or name, role=role, dimensions=list(dimensions), unit=unit)


VARIABLES = [
  variable("time", role=VariableRole.time),
  variable("depth", role=VariableRole.coordinate, dimensions=["depth"], unit="m"),
  variable("temperature", dimensions=["time", "depth"], unit="degC", source_name="temp"),
]


def dataset(time_attrs: dict | None = None, temp_attrs: dict | None = None, times=TIMES) -> xr.Dataset:
  return xr.Dataset(
    {"temp": (("time", "depth"), np.arange(6.0).reshape(3, 2), {"units": "degC"} if temp_attrs is None else temp_attrs)},
    coords={
      "time": ("time", times, {"units": UNIX_UNITS} if time_attrs is None else time_attrs),
      "depth": ("depth", [0.5, 1.0], {"units": "m"}),
    },
  )


def write(tmp_path: Path, ds: xr.Dataset, name: str = "L2_202410.nc") -> Path:
  path = tmp_path / name
  ds.to_netcdf(path=path, engine="netcdf4", format="NETCDF4")
  return path


def codes(check) -> list[ProblemCode]:
  return [problem.code for problem in check.problems]


def test_matching_file_passes_with_its_time_range(tmp_path):
  check = check_file(write(tmp_path, dataset()), VARIABLES)
  assert check.ok
  assert not check.unsupported
  assert check.problems == []
  assert check.warnings == []
  assert check.time_start == datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)
  assert check.time_end == datetime(2023, 11, 14, 22, 33, 20, tzinfo=UTC)


def test_messages_use_the_given_name(tmp_path):
  check = check_file(write(tmp_path, dataset()), [*VARIABLES, variable("oxygen")], name="2024/L2_202410.nc")
  assert check.problems[0].message.startswith("2024/L2_202410.nc: ")


def test_unsupported_format(tmp_path):
  path = tmp_path / "README.md"
  path.write_text("# notes")
  check = check_file(path, VARIABLES)
  assert codes(check) == [ProblemCode.unsupported_format]
  assert check.unsupported
  assert check.problems[0].message == "README.md: not a NetCDF file (.nc)"
  assert check.problems[0].variable is None


def test_unreadable_file(tmp_path):
  path = tmp_path / "broken.nc"
  path.write_bytes(b"not netcdf at all")
  check = check_file(path, VARIABLES)
  assert codes(check) == [ProblemCode.unreadable_file]
  assert not check.unsupported
  assert check.problems[0].message.startswith("broken.nc: can't be read as NetCDF: ")


def test_missing_variable(tmp_path):
  check = check_file(write(tmp_path, dataset()), [*VARIABLES, variable("oxygen", source_name="o2")])
  assert codes(check) == [ProblemCode.missing_variable]
  assert check.problems[0].variable == "oxygen"
  assert check.problems[0].message == "L2_202410.nc: no variable 'o2' (source of 'oxygen')"


def test_wrong_dimensions(tmp_path):
  variables = [*VARIABLES[:2], variable("temperature", dimensions=["time"], unit="degC", source_name="temp")]
  check = check_file(write(tmp_path, dataset()), variables)
  assert codes(check) == [ProblemCode.wrong_dimensions]
  assert check.problems[0].variable == "temperature"
  assert check.problems[0].message == "L2_202410.nc: variable 'temp' (source of 'temperature') has dimensions (time, depth), expected (time)"


def test_dimensions_in_another_order_are_fine(tmp_path):
  variables = [*VARIABLES[:2], variable("temperature", dimensions=["depth", "time"], unit="degC", source_name="temp")]
  assert check_file(write(tmp_path, dataset()), variables).ok


@pytest.mark.parametrize(
  ("temp_attrs", "found"),
  [({"units": "K"}, "units 'K'"), ({}, "no units attribute"), ({"units": ""}, "no units attribute"), ({"units": ["K", "degC"]}, "units 'K'")],
)
def test_unit_mismatch(tmp_path, temp_attrs, found):
  check = check_file(write(tmp_path, dataset(temp_attrs=temp_attrs)), VARIABLES)
  assert codes(check) == [ProblemCode.unit_mismatch]
  assert check.problems[0].variable == "temperature"
  assert check.problems[0].message == f"L2_202410.nc: variable 'temp' (source of 'temperature') has {found}, expected 'degC'"


def test_units_are_compared_without_surrounding_whitespace(tmp_path):
  assert check_file(write(tmp_path, dataset(temp_attrs={"units": " degC "})), VARIABLES).ok


def test_first_of_a_list_of_units_counts(tmp_path):
  assert check_file(write(tmp_path, dataset(temp_attrs={"units": ["degC", "K"]}, time_attrs={"units": [UNIX_UNITS, "days"]})), VARIABLES).ok


def test_units_are_case_sensitive(tmp_path):
  # e.g. mg/l and mg/L.
  variables = [*VARIABLES[:2], variable("temperature", dimensions=["time", "depth"], unit="degc", source_name="temp")]
  assert codes(check_file(write(tmp_path, dataset()), variables)) == [ProblemCode.unit_mismatch]


def test_variable_without_unit_accepts_any_units(tmp_path):
  variables = [*VARIABLES[:2], variable("temperature", dimensions=["time", "depth"], source_name="temp")]
  assert check_file(write(tmp_path, dataset(temp_attrs={"units": "K"})), variables).ok


@pytest.mark.parametrize(("time_attrs", "found"), [({"units": "days"}, "'days'"), ({}, "None")])
def test_time_without_cf_units(tmp_path, time_attrs, found):
  check = check_file(write(tmp_path, dataset(time_attrs=time_attrs)), VARIABLES)
  assert codes(check) == [ProblemCode.invalid_time_units]
  assert check.problems[0].variable == "time"
  assert check.problems[0].message == f"L2_202410.nc: time variable 'time' has units {found}, not CF units like 'seconds since 1970-01-01 00:00:00'"
  assert check.time_start is None and check.time_end is None


def test_time_that_cant_be_decoded(tmp_path):
  check = check_file(write(tmp_path, dataset(time_attrs={"units": "seconds since 1970-13-45"})), VARIABLES)
  assert codes(check) == [ProblemCode.invalid_time_units]
  assert check.problems[0].message.startswith("L2_202410.nc: time variable 'time' can't be decoded with units 'seconds since 1970-13-45': ")


def test_time_with_another_calendar(tmp_path):
  check = check_file(
    write(tmp_path, dataset(time_attrs={"units": "days since 2000-01-01", "calendar": "noleap"}, times=np.array([0.0, 59, 365]))), VARIABLES
  )
  assert check.ok
  assert (check.time_start, check.time_end) == (datetime(2000, 1, 1, tzinfo=UTC), datetime(2001, 1, 1, tzinfo=UTC))


def test_missing_time_values_are_left_out_of_the_range(tmp_path):
  ds = dataset(times=np.array([-9999.0, 1_700_000_000, 1_700_000_600]))
  ds["time"].encoding["_FillValue"] = -9999.0
  check = check_file(write(tmp_path, ds), VARIABLES)
  assert check.ok
  assert check.time_start == datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)


def test_time_without_values_warns(tmp_path):
  ds = dataset(times=np.array([np.nan, np.nan, np.nan]))
  check = check_file(write(tmp_path, ds), VARIABLES)
  assert check.ok
  assert check.time_start is None
  assert check.warnings == ["L2_202410.nc: time variable 'time' has no values"]


def test_unused_variables_warn(tmp_path):
  ds = dataset().assign(cond=("time", np.ones(3)), qa=("time", np.zeros(3)))
  check = check_file(write(tmp_path, ds), VARIABLES)
  assert check.ok
  assert check.warnings == ["L2_202410.nc: variables not used by the dataset: cond, qa"]


def test_a_file_collects_every_problem(tmp_path):
  variables = [*VARIABLES, variable("oxygen", source_name="o2")]
  check = check_file(write(tmp_path, dataset(time_attrs={"units": "days"}, temp_attrs={"units": "K"})), variables)
  assert codes(check) == [ProblemCode.invalid_time_units, ProblemCode.unit_mismatch, ProblemCode.missing_variable]
  assert [problem.variable for problem in check.problems] == ["time", "temperature", "oxygen"]
