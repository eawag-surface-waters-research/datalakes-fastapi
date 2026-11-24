from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
import xarray as xr
import zarr

from app.enums import FeatureType, VariableRole
from app.partitions import PartitionError, partition_bounds, partition_of, partitions_between, read_file, write_partition
from app.schemas.variables import VariableCommon

UNIX_UNITS = "seconds since 1970-01-01 00:00:00"
NEW_YEAR_2024 = 1_704_067_200  # 2024-01-01 00:00:00 UTC

TIME = VariableCommon(name="time", source_name="time", role=VariableRole.time, dimensions=["time"])
TEMPERATURE = VariableCommon(
  name="temperature", source_name="temp", role=VariableRole.data, dimensions=["time"], unit="degC", description="Water temperature"
)
VARIABLES = [TIME, TEMPERATURE]
TS = FeatureType.time_series
TSP = FeatureType.time_series_profile

DEPTH = VariableCommon(name="depth", source_name="depth", role=VariableRole.coordinate, dimensions=["depth"], unit="m")
PROFILE_TEMPERATURE = TEMPERATURE.model_copy(update={"dimensions": ["time", "depth"]})
PROFILE_VARIABLES = [TIME, DEPTH, PROFILE_TEMPERATURE]


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

  written = write_partition([a, b], VARIABLES, TS, 2024, tmp_path / "2024")
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

  written = write_partition([path], VARIABLES, TS, 2023, tmp_path / "2023")
  assert (written.rows, written.time_start, written.time_end) == (1, utc(2023, 12, 31, 23, 50), utc(2023, 12, 31, 23, 50))
  assert open_partition(tmp_path / "2023")["temperature"].values.tolist() == [1.0]

  written = write_partition([path], VARIABLES, TS, 2024, tmp_path / "2024")
  assert open_partition(tmp_path / "2024")["temperature"].values.tolist() == [2.0, 3.0]


def test_year_without_data_writes_nothing(tmp_path):
  path = netcdf(tmp_path / "a.nc", [NEW_YEAR_2024], [1.0])
  written = write_partition([path], VARIABLES, TS, 2022, tmp_path / "2022")
  assert (written.rows, written.time_start, written.time_end) == (0, None, None)
  assert not (tmp_path / "2022").exists()


def test_duplicate_time_steps_keep_the_last_file(tmp_path):
  a = netcdf(tmp_path / "a.nc", [NEW_YEAR_2024, NEW_YEAR_2024 + 600], [1.0, 2.0])
  b = netcdf(tmp_path / "b.nc", [NEW_YEAR_2024 + 600, NEW_YEAR_2024 + 1200], [20.0, 30.0])

  written = write_partition([b, a], VARIABLES, TS, 2024, tmp_path / "2024")  # sorted by path, whatever order they come in
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

  write_partition([path], [TIME, variable], TS, 2024, tmp_path / "2024")
  values = open_partition(tmp_path / "2024")["temperature"].values
  assert np.array_equal(values, [1.0, np.nan, np.nan, np.nan, 5.0], equal_nan=True)


def test_integer_variable_with_decoding_becomes_float(tmp_path):
  path = netcdf(tmp_path / "a.nc", [NEW_YEAR_2024, NEW_YEAR_2024 + 600], [1.0, 2.0], count=("time", np.array([3, -9999], dtype="int32")))
  count = VariableCommon(name="count", source_name="count", role=VariableRole.data, dimensions=["time"], decoding={"missing_values": [-9999]})

  ds = read_file(path, [TIME, count], TS)
  assert np.array_equal(ds["count"].values, [3.0, np.nan], equal_nan=True)


def test_renames_the_time_dimension(tmp_path):
  ds = xr.Dataset(
    {"temp": ("t", np.array([1.0, 2.0]), {"units": "degC"})},
    coords={"datetime": ("t", np.array([NEW_YEAR_2024, NEW_YEAR_2024 + 60], dtype="float64"), {"units": UNIX_UNITS})},
  )
  ds.to_netcdf(tmp_path / "a.nc")
  time = VariableCommon(name="time", source_name="datetime", role=VariableRole.time, dimensions=["t"])
  temperature = TEMPERATURE.model_copy(update={"dimensions": ["t"]})

  data = read_file(tmp_path / "a.nc", [time, temperature], TS)
  assert data["temperature"].dims == ("time",)
  assert list(data["time"].values) == [np.datetime64("2024-01-01T00:00:00"), np.datetime64("2024-01-01T00:01:00")]


def test_time_steps_without_a_time_are_left_out(tmp_path):
  path = netcdf(tmp_path / "a.nc", [NEW_YEAR_2024, np.nan, NEW_YEAR_2024 + 600], [1.0, 2.0, 3.0])
  assert read_file(path, VARIABLES, TS)["temperature"].values.tolist() == [1.0, 3.0]


def test_other_calendars_are_converted(tmp_path):
  path = netcdf(tmp_path / "a.nc", [0, 1], [1.0, 2.0], time_attrs={"units": "days since 2024-02-28", "calendar": "noleap"})
  times = read_file(path, VARIABLES, TS)["time"].values
  assert times.dtype == np.dtype("datetime64[ns]")
  assert list(times) == [np.datetime64("2024-02-28"), np.datetime64("2024-03-01")]  # a noleap year has no 29 February


def test_variable_not_on_the_time_dimension_fails(tmp_path):
  path = netcdf(tmp_path / "a.nc", [NEW_YEAR_2024], [1.0], depth=("depth", np.array([1.0, 2.0])))
  depth = VariableCommon(name="depth", source_name="depth", role=VariableRole.coordinate, dimensions=["depth"])
  with pytest.raises(PartitionError, match=r"a.nc: variable 'depth' has dimensions \(depth\), a timeSeries needs \(time\)"):
    read_file(path, [*VARIABLES, depth], TS)


def test_needs_one_time_variable(tmp_path):
  path = netcdf(tmp_path / "a.nc", [NEW_YEAR_2024], [1.0])
  with pytest.raises(PartitionError, match="exactly one time variable, it has 0"):
    write_partition([path], [TEMPERATURE], TS, 2024, tmp_path / "2024")


def test_does_not_overwrite_a_store(tmp_path):
  path = netcdf(tmp_path / "a.nc", [NEW_YEAR_2024], [1.0])
  write_partition([path], VARIABLES, TS, 2024, tmp_path / "2024")
  with pytest.raises(FileExistsError):
    write_partition([path], VARIABLES, TS, 2024, tmp_path / "2024")


# timeSeriesProfile


def profile_netcdf(path: Path, times, depths, temps, depth_first: bool = False, **extra) -> Path:
  """A file with temp(time, depth), or temp(depth, time) if `depth_first`."""
  temps = np.asarray(temps, dtype="float64")
  temp = ("depth", "time") if depth_first else ("time", "depth")
  ds = xr.Dataset(
    {"temp": (temp, temps.T if depth_first else temps, {"units": "degC"}), **extra},
    coords={
      "time": ("time", np.asarray(times, dtype="float64"), {"units": UNIX_UNITS}),
      "depth": ("depth", np.asarray(depths, dtype="float64"), {"units": "m"}),
    },
  )
  ds.to_netcdf(path=path, engine="netcdf4", format="NETCDF4")
  return path


def test_profile_writes_time_and_depth(tmp_path):
  a = profile_netcdf(tmp_path / "a.nc", [NEW_YEAR_2024 + 600], [1.0, 5.0, 10.0], [[4.0, 5.0, 6.0]])
  b = profile_netcdf(tmp_path / "b.nc", [NEW_YEAR_2024], [1.0, 5.0, 10.0], [[1.0, 2.0, 3.0]], depth_first=True)

  written = write_partition([a, b], PROFILE_VARIABLES, TSP, 2024, tmp_path / "2024")
  assert (written.rows, written.depth_min, written.depth_max) == (2, 1.0, 10.0)
  assert (written.time_start, written.time_end) == (utc(2024, 1, 1), utc(2024, 1, 1, 0, 10))

  ds = open_partition(tmp_path / "2024")
  assert ds["temperature"].dims == ("time", "depth")  # temp(depth, time) is transposed
  assert ds["temperature"].values.tolist() == [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]
  assert ds["depth"].values.tolist() == [1.0, 5.0, 10.0]
  assert ds["depth"].attrs == {"units": "m"}

  group = zarr.open_group(tmp_path / "2024", mode="r")
  assert group["temperature"].chunks == (2, 3)  # one chunk
  assert group["depth"].chunks == (3,)


def test_profile_renames_the_dimensions(tmp_path):
  ds = xr.Dataset(
    {"T": (("t", "z"), np.array([[1.0, 2.0]]), {"units": "degC"}), "battery": ("t", np.array([12.5]))},
    coords={"datetime": ("t", np.array([NEW_YEAR_2024], dtype="float64"), {"units": UNIX_UNITS}), "Z": ("z", np.array([2.0, 4.0]))},
  )
  ds.to_netcdf(tmp_path / "a.nc")
  variables = [
    VariableCommon(name="time", source_name="datetime", role=VariableRole.time, dimensions=["t"]),
    VariableCommon(name="depth", source_name="Z", role=VariableRole.coordinate, dimensions=["z"]),
    VariableCommon(name="temperature", source_name="T", role=VariableRole.data, dimensions=["t", "z"]),
    VariableCommon(name="battery", source_name="battery", role=VariableRole.data, dimensions=["t"]),
  ]

  data = read_file(tmp_path / "a.nc", variables, TSP)
  assert data["temperature"].dims == ("time", "depth")
  assert data["battery"].dims == ("time",)  # variables on time only are fine
  assert data["depth"].values.tolist() == [2.0, 4.0]


def test_profile_files_with_different_depths_fail(tmp_path):
  a = profile_netcdf(tmp_path / "a.nc", [NEW_YEAR_2024], [1.0, 5.0], [[1.0, 2.0]])
  b = profile_netcdf(tmp_path / "b.nc", [NEW_YEAR_2024 + 600], [1.0, 5.0, 10.0], [[1.0, 2.0, 3.0]])
  message = (
    r"2024: b.nc has the depths \[1, 5, 10\] \(3 values\), a.nc has \[1, 5\] \(2 values\). Files with different depths can't be in the same year yet"
  )
  with pytest.raises(PartitionError, match=message):
    write_partition([a, b], PROFILE_VARIABLES, TSP, 2024, tmp_path / "2024")
  assert not (tmp_path / "2024").exists()


def test_profile_files_with_different_depths_in_other_years_are_fine(tmp_path):
  a = profile_netcdf(tmp_path / "a.nc", [NEW_YEAR_2024 - 600], [1.0, 5.0], [[1.0, 2.0]])
  b = profile_netcdf(tmp_path / "b.nc", [NEW_YEAR_2024], [1.0, 5.0, 10.0], [[1.0, 2.0, 3.0]])
  assert write_partition([a, b], PROFILE_VARIABLES, TSP, 2024, tmp_path / "2024").depth_max == 10.0


def test_profile_needs_one_depth_variable(tmp_path):
  path = profile_netcdf(tmp_path / "a.nc", [NEW_YEAR_2024], [1.0], [[1.0]])
  with pytest.raises(PartitionError, match=r"exactly one coordinate variable on its depth dimension, e.g. depth\(depth\), the dataset has 0"):
    read_file(path, [TIME, PROFILE_TEMPERATURE], TSP)

  pressure = DEPTH.model_copy(update={"name": "pressure"})
  with pytest.raises(PartitionError, match="the dataset has 2: depth, pressure"):
    read_file(path, [*PROFILE_VARIABLES, pressure], TSP)


def test_profile_depth_that_varies_over_time_fails(tmp_path):
  # depth(time, sensor): measured, e.g. by pressure sensors on a mooring line that moves
  ds = xr.Dataset(
    {
      "temp": (("time", "sensor"), np.array([[1.0, 2.0]]), {"units": "degC"}),
      "depth": (("time", "sensor"), np.array([[1.2, 2.1]]), {"units": "m"}),
    },
    coords={"time": ("time", np.array([NEW_YEAR_2024], dtype="float64"), {"units": UNIX_UNITS})},
  )
  ds.to_netcdf(tmp_path / "a.nc")
  depth = DEPTH.model_copy(update={"dimensions": ["time", "sensor"]})
  temperature = TEMPERATURE.model_copy(update={"dimensions": ["time", "sensor"]})
  with pytest.raises(PartitionError, match="exactly one coordinate variable on its depth dimension"):
    read_file(tmp_path / "a.nc", [TIME, depth, temperature], TSP)


def test_profile_variable_on_another_dimension_fails(tmp_path):
  path = profile_netcdf(tmp_path / "a.nc", [NEW_YEAR_2024], [1.0], [[1.0]], spectrum=("wavelength", np.array([1.0, 2.0])))
  spectrum = VariableCommon(name="spectrum", source_name="spectrum", role=VariableRole.data, dimensions=["wavelength"])
  message = r"a.nc: variable 'spectrum' has dimensions \(wavelength\), a timeSeriesProfile needs \(time\) or \(time, depth\)"
  with pytest.raises(PartitionError, match=message):
    read_file(path, [*PROFILE_VARIABLES, spectrum], TSP)


def test_profile_datasets_cant_be_written_yet(tmp_path):
  path = netcdf(tmp_path / "a.nc", [NEW_YEAR_2024], [1.0])
  with pytest.raises(PartitionError, match="profile datasets can't be written yet"):
    read_file(path, VARIABLES, FeatureType.profile)
