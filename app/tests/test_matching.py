import json
from pathlib import Path

import pytest

from app.enums import DatasetKind, DatasetStatus, VariableRole
from app.matching import History, guess_parameters, parameter_history
from app.models import Dataset, Parameter, Variable
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


def candidates(variable: InspectedVariable, history: History | None = None) -> list[tuple[int, float]]:
  """The candidates of one variable as (parameter_id, score), guessed from the seed vocabulary."""
  _, _ = guessed(variable, parameters=SEED_PARAMETERS, history=history)
  return [(c.parameter.id, c.score) for c in variable.parameter_candidates]


def guessed(*variables: InspectedVariable, parameters=PARAMETERS, history: History | None = None) -> tuple[dict[str, int | None], list[str]]:
  """The best candidate's parameter id by source_name (None without candidates), and the warnings."""
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
  guess_parameters(result, parameters, history)
  return {v.source_name: v.parameter_candidates[0].parameter.id if v.parameter_candidates else None for v in result.variables}, result.warnings


def test_candidates_are_the_five_best_parameters_best_first():
  # Wind Speed, Wind Speed Gust, Wind Stress, Sensor Speed, Wind Direction
  assert candidates(variable("WS")) == [(7, 95.0), (53, 85.5), (35, 63.3), (156, 53.3), (8, 50.9)]


def test_candidates_scores_include_the_history_bonus():
  assert candidates(variable("WS"), history={"ws": {35: 5}})[:2] == [(35, 113.3), (7, 95.0)]


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


# ---------------------------------------------------------------------------
# history: what published datasets chose before
# ---------------------------------------------------------------------------


def test_history_decides_a_close_call():
  # by name, temp is as close to Air Temperature as to Water temperature
  assert guessed(variable("temp"), parameters=SEED_PARAMETERS)[0]["temp"] == 6
  assert guessed(variable("temp"), parameters=SEED_PARAMETERS, history={"temp": {5: 1}})[0]["temp"] == 5


def test_history_of_one_dataset_doesnt_beat_a_clear_name():
  assert guessed(variable("WS"), parameters=SEED_PARAMETERS, history={"ws": {35: 1}})[0]["WS"] == 7


def test_history_of_many_datasets_beats_the_name():
  assert guessed(variable("WS"), parameters=SEED_PARAMETERS, history={"ws": {35: 5}})[0]["WS"] == 35


def test_history_ignores_case():
  assert guessed(variable("TEMP"), parameters=SEED_PARAMETERS, history={"temp": {5: 1}})[0]["TEMP"] == 5


def test_history_follows_the_majority():
  assert guessed(variable("Ptemp"), parameters=SEED_PARAMETERS, history={"ptemp": {98: 4, 6: 1}})[0]["Ptemp"] == 98


def test_history_of_a_parameter_not_in_the_vocabulary_is_ignored():
  assert guessed(variable("temp"), parameters=SEED_PARAMETERS, history={"temp": {999: 5}})[0]["temp"] == 6


async def add_dataset(session, status: DatasetStatus, *variables: tuple[str, int | None]) -> int:
  """A dataset with time and data variables given as (source_name, parameter_id). Returns its id."""
  dataset = Dataset(title="Weather station", kind=DatasetKind.data, status=status)
  session.add(dataset)
  await session.commit()
  await session.refresh(dataset)
  dataset_id = dataset.id
  assert dataset_id is not None
  session.add(Variable(dataset_id=dataset_id, name="time", source_name="time", role=VariableRole.time, dimensions=["time"]))
  for i, (source_name, parameter_id) in enumerate(variables):
    session.add(
      Variable(dataset_id=dataset_id, name=f"v{i}", source_name=source_name, role=VariableRole.data, dimensions=["time"], parameter_id=parameter_id)
    )
  await session.commit()
  return dataset_id


async def test_parameter_history_counts_published_datasets_by_name(session):
  session.add_all(
    [Parameter(id=5, name="Water temperature"), Parameter(id=11, name="Solar Irradiance"), Parameter(id=68, name="Surface temperature")]
  )
  await session.commit()
  await add_dataset(session, DatasetStatus.published, ("TEMP", 68), ("Slrw", 11))
  await add_dataset(session, DatasetStatus.published, ("temp", 68))
  await add_dataset(session, DatasetStatus.published, ("temp", 5), ("other", 5))
  await add_dataset(session, DatasetStatus.published, ("temp", None))  # no parameter chosen
  await add_dataset(session, DatasetStatus.draft, ("temp", 5))  # drafts don't count
  inspected = await add_dataset(session, DatasetStatus.published, ("temp", 5))  # nor does the dataset being inspected

  assert await parameter_history(session, inspected, ["temp", "SLRW", "unknown"]) == {"temp": {68: 2, 5: 1}, "slrw": {11: 1}}
