import json
from pathlib import Path

import pytest

from app.enums import VariableRole
from app.matching import guess_parameters
from app.models import Parameter
from app.schemas.inspect import InspectedVariable, InspectResult, SampleSummary

SEED_PARAMETERS = [Parameter(**row) for row in json.loads((Path(__file__).parents[2] / "scripts" / "seed_data" / "parameters.json").read_text())]
PARAMETERS = [
  Parameter(id=1, name="Time", cf_name="time"),
  Parameter(id=2, name="Depth", cf_name="depth"),
  Parameter(id=5, name="Water temperature", cf_name="water_temperature"),
  Parameter(id=22, name="Oxygen Saturation", cf_name="water_oxygen_saturation"),
  Parameter(id=38, name="Salinity", cf_name="salinity"),
  Parameter(id=140, name="Total organic carbon", cf_name=None),
]


def variable(source_name: str, role=VariableRole.data, **attributes) -> InspectedVariable:
  return InspectedVariable(
    name="v",
    source_name=source_name,
    role=role,
    dimensions=["time"],
    shape=[4],
    attributes=attributes,
    sample=SampleSummary(count=4, missing=0, min=None, max=None),
  )


def guessed(*variables: InspectedVariable, parameters=PARAMETERS) -> tuple[dict[str, int | None], list[str]]:
  """The guessed parameter IDs by source_name, and the warnings."""
  result = InspectResult(
    file="data.nc",
    format="netcdf4",
    dimensions={"time": 4},
    feature_type=None,
    feature_type_reason="",
    time_range=None,
    location=None,
    variables=list(variables),
    scalars=[],
    global_attributes={},
    warnings=[],
  )
  guess_parameters(result, parameters)
  return {v.source_name: v.parameter_id for v in result.variables}, result.warnings


def test_parameters_are_guessed_from_standard_name_long_name_and_name():
  ids, warnings = guessed(
    variable("temp", standard_name="water_temperature"),  # standard_name against cf_name
    variable("o2", long_name="Oxygen saturation"),  # long_name against the name
    variable("sal", long_name="salinity"),  # long_name against cf_name
    variable("Total Organic Carbon"),  # name in the file, without cf_name
  )
  assert ids == {"temp": 5, "o2": 22, "sal": 38, "Total Organic Carbon": 140}
  assert warnings == []


def test_standard_name_comes_before_long_name():
  assert guessed(variable("temp", standard_name="salinity", long_name="Water temperature"))[0]["temp"] == 38


@pytest.mark.parametrize(
  ("source_name", "parameter_id"),
  [
    ("AirTC", 6),
    ("AirTC_2m", 6),
    ("AirTemp", 6),
    ("RH", 24),
    ("Slrw", 11),
    ("SlrW", 11),
    ("WS_ms_Avg", 7),
    ("WindDir", 8),
    ("Rain_mm_Tot", 9),
    ("BP_mbar", 10),
    ("WindGust", 53),
    ("BattV", 112),
    ("Water Temp", 5),
    ("DO", 12),
    ("O2_sat", 22),
    ("cond", 17),
    ("chla", 15),
    ("pH", 14),
    ("TKE_diss", 73),
    ("Lufttemperatur", 6),
  ],
)
def test_names_are_matched_fuzzily(source_name, parameter_id):
  assert guessed(variable(source_name), parameters=SEED_PARAMETERS)[0][source_name] == parameter_id


def test_names_that_say_what_was_measured_where_get_the_same_parameter():
  ids, warnings = guessed(
    variable("AirTC_2m", long_name="Air temperature at 2m"),
    variable("AirTC_4m", long_name="Air temperature at 4m"),
    parameters=SEED_PARAMETERS,
  )
  assert ids == {"AirTC_2m": 6, "AirTC_4m": 6}
  assert warnings == []


@pytest.mark.parametrize("source_name", ["mixed_layer", "u", "Ptemp"])
def test_weak_matches_still_set_a_parameter(source_name):
  ids, warnings = guessed(variable(source_name), parameters=SEED_PARAMETERS)
  assert ids[source_name] is not None
  assert warnings == []


def test_several_equally_good_matches_pick_the_lowest_id():
  # listed first, but Water temperature (5) has the lower id
  parameters = [Parameter(id=68, name="Surface temperature", cf_name="water_temperature"), *PARAMETERS]
  ids, warnings = guessed(variable("temp", long_name="Water temperature"), parameters=parameters)
  assert ids["temp"] == 5
  assert warnings == []


def test_flags_are_not_guessed():
  assert guessed(variable("salinity_qual", role=VariableRole.flag, long_name="salinity"))[0]["salinity_qual"] is None


def test_no_parameters():
  assert guessed(variable("temp", standard_name="water_temperature"), parameters=[])[0] == {"temp": None}
