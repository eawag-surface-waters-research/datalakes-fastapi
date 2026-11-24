from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
import xarray as xr
import zarr

from app.enums import VariableRole
from app.partitions import PartitionError, partition_bounds, partition_of, partitions_between, read_file, write_partition
from app.schemas.variables import VariableCommon

UNIX_UNITS = "seconds since 1970-01-01 00:00:00"
NEW_YEAR_2024 = 1_704_067_200  # 2024-01-01 00:00:00 UTC

TIME = VariableCommon(name="time", source_name="time", role=VariableRole.time, dimensions=["time"])
TEMPERATURE = VariableCommon(
  name="temperature", source_name="temp", role=VariableRole.data, dimensions=["time"], unit="degC", description="Water temperature"
)
VARIABLES = [TIME, TEMPERATURE]


def netcdf(path: Path, times, temps, time_attrs: dict | None = None, temp_attrs: dict | None = None, **extra) -> Path:
  ds = xr.Dataset(
    {"temp": ("time", np.asarray(temps, dtype="float64"), {"units": "degC", **(temp_attrs or {})}), **extra},
    coords={"time": ("time", np.asarray(times, dtype="float64"), time_attrs or {"units": UNIX_UNITS})},
  )
  ds.to_netcdf(path=path, engine="netcdf4", format="NETCDF4")
  return path


def utc(*args) -> datetime:
  return datetime(*args, tzinfo=UTC)


def open_partition(path: Path) -> xr.Dataset:
  return xr.open_zarr(path, consolidated=False).load()


def test_partition_helpers():
  assert partition_of(utc(2024, 12, 31, 23, 59)) == 2024
  assert partition_of(datetime.fromisoformat("2025-01-01T00:30:00+01:00")) == 2024  # in UTC
  assert partition_bounds(2024) == (utc(2024, 1, 1), utc(2025, 1, 1))
  assert partitions_between(utc(2023, 6, 1), utc(2025, 2, 1)) == [2023, 2024, 2025]
  assert partitions_between(utc(2024, 6, 1), utc(2024, 7, 1)) == [2024]


def test_writes_the_files_of_a_year(tmp_path):
  # b.nc is earlier in time than a.nc: the time steps are sorted, not the files
  a = netcdf(tmp_path / "a.nc", [NEW_YEAR_2024 + 1200, NEW_YEAR_2024 + 1800], [12.0, 13.0])
  b = netcdf(tmp_path / "b.nc", [NEW_YEAR_2024, NEW_YEAR_2024 + 600], [10.0, 11.0])

  written = write_partition([a, b], VARIABLES, 2024, tmp_path / "2024")
  assert (written.year, written.rows, written.warnings) == (2024, 4, [])
  assert (written.time_start, written.time_end) == (utc(2024, 1, 1), utc(2024, 1, 1, 0, 30))

  ds = open_partition(tmp_path / "2024")
  assert list(ds.data_vars) == ["temperature"]  # the dataset's name, not the file's
  assert ds["temperature"].dims == ("time",)
  assert ds["temperature"].values.tolist() == [10.0, 11.0, 12.0, 13.0]
  assert ds["time"].values[0] == np.datetime64("2024-01-01T00:00:00")
  assert ds["temperature"].attrs == {"units": "degC", "description": "Water temperature"}

  group = zarr.open_group(tmp_path / "2024", mode="r")
  assert group["temperature"].chunks == (4,)  # one chunk
  assert group.metadata.zarr_format == 3


def test_keeps_only_the_partitions_year(tmp_path):
  path = netcdf(tmp_path / "a.nc", [NEW_YEAR_2024 - 600, NEW_YEAR_2024, NEW_YEAR_2024 + 600], [1.0, 2.0, 3.0])

  written = write_partition([path], VARIABLES, 2023, tmp_path / "2023")
  assert (written.rows, written.time_start, written.time_end) == (1, utc(2023, 12, 31, 23, 50), utc(2023, 12, 31, 23, 50))
  assert open_partition(tmp_path / "2023")["temperature"].values.tolist() == [1.0]

  written = write_partition([path], VARIABLES, 2024, tmp_path / "2024")
  assert open_partition(tmp_path / "2024")["temperature"].values.tolist() == [2.0, 3.0]


def test_year_without_data_writes_nothing(tmp_path):
  path = netcdf(tmp_path / "a.nc", [NEW_YEAR_2024], [1.0])
  written = write_partition([path], VARIABLES, 2022, tmp_path / "2022")
  assert (written.rows, written.time_start, written.time_end) == (0, None, None)
  assert not (tmp_path / "2022").exists()


def test_duplicate_time_steps_keep_the_last_file(tmp_path):
  a = netcdf(tmp_path / "a.nc", [NEW_YEAR_2024, NEW_YEAR_2024 + 600], [1.0, 2.0])
  b = netcdf(tmp_path / "b.nc", [NEW_YEAR_2024 + 600, NEW_YEAR_2024 + 1200], [20.0, 30.0])

  written = write_partition([b, a], VARIABLES, 2024, tmp_path / "2024")  # sorted by path, whatever order they come in
  assert written.rows == 3
  assert written.warnings == ["2024: 1 time steps are in more than one file, kept the values of the file that sorts last by path"]
  assert open_partition(tmp_path / "2024")["temperature"].values.tolist() == [1.0, 20.0, 30.0]


def test_missing_values_become_nan(tmp_path):
  path = netcdf(
    tmp_path / "a.nc",
    [NEW_YEAR_2024 + 600 * i for i in range(5)],
    [1.0, -9999.0, 1e36, -1.0, 5.0],
    temp_attrs={"missing_value": -1.0},
  )
  variable = TEMPERATURE.model_copy(update={"decoding": {"missing_values": [-9999], "valid_max": 1e31}})

  write_partition([path], [TIME, variable], 2024, tmp_path / "2024")
  values = open_partition(tmp_path / "2024")["temperature"].values
  assert np.array_equal(values, [1.0, np.nan, np.nan, np.nan, 5.0], equal_nan=True)


def test_integer_variable_with_decoding_becomes_float(tmp_path):
  path = netcdf(tmp_path / "a.nc", [NEW_YEAR_2024, NEW_YEAR_2024 + 600], [1.0, 2.0], count=("time", np.array([3, -9999], dtype="int32")))
  count = VariableCommon(name="count", source_name="count", role=VariableRole.data, dimensions=["time"], decoding={"missing_values": [-9999]})

  ds = read_file(path, [TIME, count])
  assert np.array_equal(ds["count"].values, [3.0, np.nan], equal_nan=True)


def test_renames_the_time_dimension(tmp_path):
  ds = xr.Dataset(
    {"temp": ("t", np.array([1.0, 2.0]), {"units": "degC"})},
    coords={"datetime": ("t", np.array([NEW_YEAR_2024, NEW_YEAR_2024 + 60], dtype="float64"), {"units": UNIX_UNITS})},
  )
  ds.to_netcdf(tmp_path / "a.nc")
  time = VariableCommon(name="time", source_name="datetime", role=VariableRole.time, dimensions=["t"])
  temperature = TEMPERATURE.model_copy(update={"dimensions": ["t"]})

  data = read_file(tmp_path / "a.nc", [time, temperature])
  assert data["temperature"].dims == ("time",)
  assert list(data["time"].values) == [np.datetime64("2024-01-01T00:00:00"), np.datetime64("2024-01-01T00:01:00")]


def test_time_steps_without_a_time_are_left_out(tmp_path):
  path = netcdf(tmp_path / "a.nc", [NEW_YEAR_2024, np.nan, NEW_YEAR_2024 + 600], [1.0, 2.0, 3.0])
  assert read_file(path, VARIABLES)["temperature"].values.tolist() == [1.0, 3.0]


def test_other_calendars_are_converted(tmp_path):
  path = netcdf(tmp_path / "a.nc", [0, 1], [1.0, 2.0], time_attrs={"units": "days since 2024-02-28", "calendar": "noleap"})
  times = read_file(path, VARIABLES)["time"].values
  assert times.dtype == np.dtype("datetime64[ns]")
  assert list(times) == [np.datetime64("2024-02-28"), np.datetime64("2024-03-01")]  # a noleap year has no 29 February


def test_variable_not_on_the_time_dimension_fails(tmp_path):
  path = netcdf(tmp_path / "a.nc", [NEW_YEAR_2024], [1.0], depth=("depth", np.array([1.0, 2.0])))
  depth = VariableCommon(name="depth", source_name="depth", role=VariableRole.coordinate, dimensions=["depth"])
  with pytest.raises(PartitionError, match=r"a.nc: variable 'depth' has dimensions \(depth\), a timeSeries needs \(time\)"):
    read_file(path, [*VARIABLES, depth])


def test_needs_one_time_variable(tmp_path):
  path = netcdf(tmp_path / "a.nc", [NEW_YEAR_2024], [1.0])
  with pytest.raises(PartitionError, match="exactly one time variable, the dataset has 0"):
    write_partition([path], [TEMPERATURE], 2024, tmp_path / "2024")


def test_does_not_overwrite_a_store(tmp_path):
  path = netcdf(tmp_path / "a.nc", [NEW_YEAR_2024], [1.0])
  write_partition([path], VARIABLES, 2024, tmp_path / "2024")
  with pytest.raises(FileExistsError):
    write_partition([path], VARIABLES, 2024, tmp_path / "2024")
