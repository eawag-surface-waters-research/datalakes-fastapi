from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from app.enums import FeatureType, VariableRole
from app.errors import ApiError
from app.inspection import guess_parameters, inspect_file
from app.models import Parameter

UNIX_UNITS = "seconds since 1970-01-01 00:00:00"
TIMES = np.array([1_700_000_000, 1_700_000_600, 1_700_001_200, 1_700_001_800], dtype="float64")


def write(tmp_path: Path, ds: xr.Dataset, name="data.nc") -> Path:
  path = tmp_path / name
  ds.to_netcdf(path=path, engine="netcdf4", format="NETCDF4")
  return path


def by_source(result) -> dict:
  return {v.source_name: v for v in result.variables}


def time_series(**time_attrs) -> xr.Dataset:
  return xr.Dataset(
    {
      "Water Temp": ("time", np.array([10.5, -9999, 11.0, 12.0]), {"units": "degC", "long_name": "water temperature", "ancillary_variables": "qa"}),
      "qa": ("time", np.array([0, 1, 0, 0], dtype="int8"), {"flag_values": [0, 1], "flag_meanings": "good bad"}),
      "cond_qual": ("time", np.array([0, 0, 0, 0], dtype="int8")),
      "cond": ("time", np.array([300.0, 301.0, 1e36, 302.0]), {"units": "uS/cm"}),
    },
    coords={"time": ("time", TIMES, {"units": UNIX_UNITS, **time_attrs})},
    attrs={"title": "Test buoy", "latitude": 46.5, "longitude": 6.6},
  )


def time_series_profile() -> xr.Dataset:
  return xr.Dataset(
    {"temp": (("depth", "time"), np.arange(8.0).reshape(2, 4), {"units": "degC"}), "mixed_layer": ("time", np.ones(4), {"units": "m"})},
    coords={"time": ("time", TIMES, {"units": UNIX_UNITS}), "depth": ("depth", [0.5, 1.0], {"units": "m"})},
  )


def test_time_series(tmp_path):
  result = inspect_file(write(tmp_path, time_series()))

  # to satisfy mypy
  assert result.time_range
  assert result.location

  assert result.file == "data.nc"
  assert result.format == "netcdf4"
  assert result.dimensions == {"time": 4}
  assert result.feature_type == FeatureType.time_series
  assert result.feature_type_reason == "data on time without a vertical dimension: Water Temp, cond"
  assert result.time_range.model_dump() == {
    "start": datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC),
    "end": datetime(2023, 11, 14, 22, 43, 20, tzinfo=UTC),
    "count": 4,
    "units": UNIX_UNITS,
    "calendar": None,
  }
  assert result.location.model_dump() == {"latitude": 46.5, "longitude": 6.6}
  assert result.global_attributes == {"title": "Test buoy", "latitude": 46.5, "longitude": 6.6}
  assert result.scalars == []


def test_suggested_variable(tmp_path):
  temp = by_source(inspect_file(write(tmp_path, time_series())))["Water Temp"]
  assert temp.model_dump() == {
    "name": "water_temp",
    "source_name": "Water Temp",
    "role": VariableRole.data,
    "dimensions": ["time"],
    "unit": "degC",
    "parameter_id": None,
    "sensor_id": None,
    "flag_for": None,
    "description": "water temperature",
    "decoding": {"missing_values": [-9999]},
    "shape": [4],
    # xarray writes a NaN fill value, which isn't valid JSON
    "attributes": {"_FillValue": None, "units": "degC", "long_name": "water temperature", "ancillary_variables": "qa"},
    "sample": {"count": 4, "missing": 1, "min": 10.5, "max": 12.0},
  }


def test_time_and_coordinates_come_first(tmp_path):
  result = inspect_file(write(tmp_path, time_series_profile()))
  assert [v.source_name for v in result.variables] == ["time", "depth", "temp", "mixed_layer"]


def test_huge_values_and_declared_fill_values(tmp_path):
  ds = time_series()
  ds["Water Temp"].encoding["_FillValue"] = -9999.0
  variables = by_source(inspect_file(write(tmp_path, ds)))
  assert variables["Water Temp"].decoding is None
  assert variables["Water Temp"].sample.missing == 1
  assert variables["cond"].decoding == {"valid_max": 1e31}
  assert variables["time"].decoding is None


def test_infinite_values_are_named_in_the_warning(tmp_path):
  ds = time_series()
  ds["cond"] = ("time", np.array([300.0, np.inf, 301.0, 302.0]), {"units": "uS/cm"})
  result = inspect_file(write(tmp_path, ds))
  assert by_source(result)["cond"].decoding == {"valid_max": 1e31}
  assert any(w.startswith("cond: placeholder values found in the sample, 1 of them infinite") for w in result.warnings)


def test_flags_are_linked_by_attribute_and_by_name(tmp_path):
  variables = by_source(inspect_file(write(tmp_path, time_series())))
  assert variables["qa"].role == VariableRole.flag
  assert variables["qa"].flag_for == "water_temp"
  assert variables["cond_qual"].role == VariableRole.flag
  assert variables["cond_qual"].flag_for == "cond"


def test_flag_on_time_is_not_linked(tmp_path):
  ds = time_series()
  ds["time_qual"] = ("time", np.zeros(4), {"units": "0 = nothing to report, 1 = more investigation"})
  result = inspect_file(write(tmp_path, ds))
  flag = by_source(result)["time_qual"]
  assert flag.flag_for is None
  assert flag.unit is None
  assert flag.attributes["units"] == "0 = nothing to report, 1 = more investigation"
  assert "time_qual: looks like a flag for 'time', a time variable. Only data variables can be flagged." in result.warnings
  assert any(w.startswith("time_qual: `units` holds the flag meanings") for w in result.warnings)


def test_time_series_profile(tmp_path):
  result = inspect_file(write(tmp_path, time_series_profile()))
  variables = by_source(result)
  assert result.feature_type == FeatureType.time_series_profile
  assert result.feature_type_reason == "data on time and a vertical dimension: temp. Also on time only: mixed_layer"
  assert variables["depth"].role == VariableRole.coordinate
  assert variables["temp"].dimensions == ["depth", "time"]
  assert variables["temp"].shape == [2, 4]


def test_one_cast_is_a_profile(tmp_path):
  """A CTD cast: depth and time both vary along one dimension, and time may repeat."""
  ds = xr.Dataset(
    {"Temp": ("time", [12.0, 10.0, 8.0], {"units": "degC"}), "Press": ("time", [0.5, 1.0, 1.5], {"units": "dbar"})},
    coords={"time": ("time", [1_700_000_000.0] * 3, {"units": UNIX_UNITS}), "depth": ("time", [0.5, 1.0, 1.5], {"units": "m"})},
  )
  result = inspect_file(write(tmp_path, ds))
  variables = by_source(result)
  assert result.feature_type == FeatureType.profile
  assert result.feature_type_reason == "one cast: time and depth both vary along 'time'"
  assert variables["depth"].role == VariableRole.coordinate
  assert variables["Press"].role == VariableRole.coordinate
  assert not any("not strictly increasing" in w for w in result.warnings)


def test_profile_with_scalar_time_and_location(tmp_path):
  ds = xr.Dataset(
    {
      "temp": ("depth", [12.0, 8.0], {"units": "degC"}),
      "lat": ((), 46.5, {"units": "degrees_north"}),
      "lon": ((), 6.6, {"units": "degrees_east"}),
      "time": ((), 1_700_000_000.0, {"units": UNIX_UNITS}),
    },
    coords={"depth": ("depth", [0.5, 1.0], {"units": "m", "positive": "down"})},
  )
  result = inspect_file(write(tmp_path, ds))
  # to satisfy mypy
  assert result.location

  assert result.feature_type == FeatureType.profile
  assert [v.source_name for v in result.variables] == ["depth", "temp"]
  assert [s.model_dump() for s in result.scalars] == [
    {"source_name": "lat", "value": 46.5, "unit": "degrees_north", "axis": "latitude"},
    {"source_name": "lon", "value": 6.6, "unit": "degrees_east", "axis": "longitude"},
    {"source_name": "time", "value": 1_700_000_000.0, "unit": UNIX_UNITS, "axis": "time"},
  ]
  assert result.location.model_dump() == {"latitude": 46.5, "longitude": 6.6}
  assert result.warnings == []


def test_location_along_a_dimension_is_not_a_single_location(tmp_path):
  ds = time_series()
  ds.attrs = {}
  ds["lat"] = ("time", [46.5, 46.6, 46.7, 46.8])
  ds["lng"] = ("time", [6.6, 6.7, 6.8, 6.9])
  result = inspect_file(write(tmp_path, ds))
  assert result.location is None
  assert by_source(result)["lat"].role == VariableRole.coordinate
  assert not any("latitude" in w for w in result.warnings)


def test_no_location(tmp_path):
  ds = time_series()
  ds.attrs = {"latitude": "46.5 N", "longitude": 6.6}
  result = inspect_file(write(tmp_path, ds))
  assert result.location is None
  assert "The latitude '46.5 N' is not a number." in result.warnings
  assert "No latitude and longitude in the variables or global attributes. Set the dataset's location by hand." in result.warnings


def test_declared_feature_type_wins(tmp_path):
  ds = time_series()
  ds.attrs["featureType"] = "timeSeriesProfile"
  result = inspect_file(write(tmp_path, ds))
  assert result.feature_type == FeatureType.time_series_profile
  assert result.feature_type_reason == "declared in the global attribute featureType = 'timeSeriesProfile'"


def test_unknown_declared_feature_type_falls_back_to_a_guess(tmp_path):
  ds = time_series()
  ds.attrs["featureType"] = "trajectory"
  result = inspect_file(write(tmp_path, ds))
  assert result.feature_type == FeatureType.time_series
  assert "The global attribute featureType = 'trajectory' is not one of timeSeries, timeSeriesProfile, profile." in result.warnings


def test_no_feature_type(tmp_path):
  result = inspect_file(write(tmp_path, xr.Dataset({"eps": ("n2", [1.0, 2.0])}, coords={"n2": ("n2", [0.1, 0.2])})))
  assert result.feature_type is None
  assert "Couldn't guess the feature type: no data variable is on a time or vertical dimension." in result.warnings


@pytest.mark.parametrize(
  ("values", "units", "guess"),
  [(np.array([738000.5, 738001.5]), "days", "MATLAB datenums"), (np.array([1_700_000_000.0, 1_700_000_600.0]), None, "unix seconds")],
)
def test_time_without_cf_units_warns(tmp_path, values, units, guess):
  attrs = {"units": units} if units else {}
  ds = xr.Dataset({"temp": ("time", [1.0, 2.0])}, coords={"time": ("time", values, attrs)})
  result = inspect_file(write(tmp_path, ds))
  assert result.time_range is None
  assert by_source(result)["time"].role == VariableRole.time
  [warning] = [w for w in result.warnings if w.startswith("invalid_time_units")]
  assert guess in warning


def test_time_with_another_calendar(tmp_path):
  ds = time_series_profile()
  ds["time"] = ("time", [0.0, 1.0, 2.0, 3.0], {"units": "days since 2024-02-28", "calendar": "360_day"})

  time_range = inspect_file(write(tmp_path, ds)).time_range
  assert time_range
  assert time_range.start == datetime(2024, 2, 28, tzinfo=UTC)
  assert time_range.end == datetime(2024, 3, 1, tzinfo=UTC)
  assert time_range.calendar == "360_day"


def test_time_that_cant_be_decoded(tmp_path):
  ds = time_series_profile()
  ds["time"] = ("time", [0.0, 1.0, 2.0, 3.0], {"units": "days since 2024-02-30", "calendar": "360_day"})
  result = inspect_file(write(tmp_path, ds))
  assert result.time_range is None
  assert any(w.startswith("invalid_time_units: 'time' can't be decoded") for w in result.warnings)


def test_time_not_increasing_warns(tmp_path):
  result = inspect_file(write(tmp_path, time_series().isel(time=[1, 0, 2, 2])))
  assert "'time' is not strictly increasing: 1 repeated, 1 decreasing." in result.warnings


def test_no_time_variable(tmp_path):
  ds = xr.Dataset({"temp": ("depth", [12.0, 8.0])}, coords={"depth": ("depth", [0.5, 1.0])}, attrs={"time": "20 March 2024"})
  result = inspect_file(write(tmp_path, ds))
  assert "The file has no time variable, and the global attribute 'time' = '20 March 2024' is not an ISO 8601 time." in result.warnings


def test_no_time_at_all(tmp_path):
  ds = xr.Dataset({"temp": ("depth", [12.0, 8.0])}, coords={"depth": ("depth", [0.5, 1.0])})
  assert "The file has no time variable and no time in its global attributes." in inspect_file(write(tmp_path, ds)).warnings


def test_iso_time_attribute_is_accepted(tmp_path):
  ds = xr.Dataset({"temp": ("depth", [12.0, 8.0])}, coords={"depth": ("depth", [0.5, 1.0])}, attrs={"time": "2024-03-20T10:00:00+00:00"})
  assert not any("time" in w for w in inspect_file(write(tmp_path, ds)).warnings)


def test_units_stored_as_a_list(tmp_path):
  ds = time_series()
  ds["cond"].attrs["units"] = ["uS/cm", "microsiemens"]
  result = inspect_file(write(tmp_path, ds))
  assert by_source(result)["cond"].unit == "uS/cm"
  assert any(w.startswith("cond: `units` is a list ['uS/cm', 'microsiemens']") for w in result.warnings)


def test_units_spelled_differently(tmp_path):
  ds = time_series()
  ds["oxygen"] = ("time", np.ones(4), {"units": "mg/l"})
  ds["bottom_oxygen"] = ("time", np.ones(4), {"units": "mg/L"})
  result = inspect_file(write(tmp_path, ds))
  assert "Units ['mg/L', 'mg/l'] are spelled differently in the same file. Pick one spelling for the variables." in result.warnings


def test_empty_variable(tmp_path):
  ds = time_series()
  ds["cond"] = ("time", np.full(4, np.nan), {"units": "uS/cm"})
  result = inspect_file(write(tmp_path, ds))
  assert by_source(result)["cond"].sample.model_dump() == {"count": 4, "missing": 4, "min": None, "max": None}
  assert "cond: no valid values in the sample (4 values). The variable may be empty in this file." in result.warnings


def test_tiny_values_warn_without_changing_decoding(tmp_path):
  ds = time_series()
  ds["cond"] = ("time", np.array([300.0, -9.99e-29, 0.0, 302.0]), {"units": "uS/cm"})
  result = inspect_file(write(tmp_path, ds))
  assert by_source(result)["cond"].decoding is None
  assert any(w.startswith("cond: 1 values are almost zero but not zero (e.g. -9.99e-29)") for w in result.warnings)


def test_other_dimensions_and_dimensions_without_coordinates(tmp_path):
  ds = xr.Dataset(
    {"rrs": (("time", "wavelength"), np.ones((4, 2))), "temp": (("time", "sensor"), np.ones((4, 2)))},
    coords={"time": ("time", TIMES, {"units": UNIX_UNITS}), "wavelength": ("wavelength", [400.0, 500.0], {"units": "nm"})},
  )
  result = inspect_file(write(tmp_path, ds))
  assert "Dimension 'wavelength' is neither time nor depth, used by rrs. Only time and depth are supported." in result.warnings
  assert "Dimension 'sensor' has no coordinate variable, so its values are just indexes." in result.warnings


def test_non_numeric_variable(tmp_path):
  ds = time_series()
  ds["station"] = ("time", np.array(["a", "b", "c", "d"], dtype=object))
  result = inspect_file(write(tmp_path, ds))
  assert by_source(result)["station"].sample.model_dump() == {"count": 4, "missing": 0, "min": None, "max": None}
  assert any(w.startswith("station: values are not numeric") for w in result.warnings)


def test_suggested_names_are_valid_and_unique(tmp_path):
  ds = xr.Dataset({"O2-sat": ("x", [1.0]), "o2_sat": ("x", [1.0]), "2nd": ("x", [1.0]), "Temp (°C)": ("x", [1.0])})
  names = {v.source_name: v.name for v in inspect_file(write(tmp_path, ds)).variables}
  assert names == {"O2-sat": "o2_sat", "o2_sat": "o2_sat_2", "2nd": "v_2nd", "Temp (°C)": "temp_c"}


def test_sample_is_bounded(tmp_path):
  ds = xr.Dataset({"temp": (("time", "depth"), np.full((1000, 100), -9999.0))})
  temp = by_source(inspect_file(write(tmp_path, ds), sample_size=1000))["temp"]
  assert temp.sample.count <= 1000
  assert temp.shape == [1000, 100]
  assert temp.decoding == {"missing_values": [-9999]}


@pytest.mark.parametrize("name", ["data.csv", "README.md"])
def test_not_netcdf_extension(tmp_path, name):
  path = tmp_path / name
  path.write_text("time,temp\n")
  with pytest.raises(ApiError) as error:
    inspect_file(path)
  assert (error.value.status_code, error.value.code) == (422, "unreadable_file")
  assert error.value.detail == f"File '{name}' is not a NetCDF file (.nc). Only NetCDF is supported."


def test_unreadable_netcdf(tmp_path):
  path = tmp_path / "broken.nc"
  path.write_bytes(b"not netcdf at all")
  with pytest.raises(ApiError) as error:
    inspect_file(path)
  assert (error.value.status_code, error.value.code) == (422, "unreadable_file")
  assert error.value.detail.startswith("File 'broken.nc' can't be read as NetCDF")


PARAMETERS = [
  Parameter(id=1, name="Time", cf_name="time"),
  Parameter(id=2, name="Depth", cf_name="depth"),
  Parameter(id=5, name="Water temperature", cf_name="water_temperature"),
  Parameter(id=22, name="Oxygen Saturation", cf_name="water_oxygen_saturation"),
  Parameter(id=38, name="Salinity", cf_name="salinity"),
  Parameter(id=140, name="Total organic carbon", cf_name=None),
]


def guessed(ds: xr.Dataset, tmp_path: Path, parameters=PARAMETERS):
  """The guessed parameter IDs by source_name, and only the warnings that guessing added."""
  result = inspect_file(write(tmp_path, ds))
  before = len(result.warnings)
  guess_parameters(result, parameters)
  return {v.source_name: v.parameter_id for v in result.variables}, result.warnings[before:]


def test_parameters_are_guessed_from_standard_name_long_name_and_name(tmp_path):
  ds = time_series_profile()
  ds["temp"].attrs["standard_name"] = "water_temperature"  # standard_name against cf_name
  ds["o2"] = ("time", np.ones(4), {"long_name": "Oxygen saturation"})  # long_name against the name
  ds["sal"] = ("time", np.ones(4), {"long_name": "salinity"})  # long_name against cf_name
  ds["Total Organic Carbon"] = ("time", np.ones(4))  # name in the file, without cf_name
  ids, warnings = guessed(ds, tmp_path)
  assert ids == {"time": 1, "depth": 2, "temp": 5, "mixed_layer": None, "o2": 22, "sal": 38, "Total Organic Carbon": 140}
  assert warnings == []


def test_standard_name_comes_before_long_name(tmp_path):
  ds = time_series_profile()
  ds["temp"].attrs.update(standard_name="salinity", long_name="Water temperature")
  assert guessed(ds, tmp_path)[0]["temp"] == 38


def test_no_match_leaves_parameter_empty(tmp_path):
  ds = time_series_profile()
  ds["temp"].attrs.update(standard_name="sea_water_temperature", long_name="temperature")
  ids, warnings = guessed(ds, tmp_path)
  assert ids["temp"] is None
  assert warnings == []


def test_several_matches_warn_and_guess_nothing(tmp_path):
  parameters = [*PARAMETERS, Parameter(id=68, name="Surface temperature", cf_name="water_temperature")]
  ds = time_series_profile()
  ds["temp"].attrs["long_name"] = "Water temperature"
  ids, warnings = guessed(ds, tmp_path, parameters)
  assert ids["temp"] is None
  assert "temp: 'Water temperature' matches several parameters: 'Surface temperature' (68), 'Water temperature' (5). Pick one." in warnings


def test_flags_are_not_guessed(tmp_path):
  ds = time_series_profile()
  ds["salinity_qual"] = ("time", np.zeros(4, dtype="int8"), {"long_name": "salinity"})
  assert guessed(ds, tmp_path)[0]["salinity_qual"] is None


def test_no_parameters(tmp_path):
  assert set(guessed(time_series_profile(), tmp_path, [])[0].values()) == {None}
