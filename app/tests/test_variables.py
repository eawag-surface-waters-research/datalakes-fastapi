import pytest
from sqlmodel import col, select

from app.enums import DatasetKind, JobStatus, JobType
from app.models import Dataset, Job, Parameter, Sensor, Variable

TIME = {"name": "time", "source_name": "time", "role": "time", "dimensions": ["time"], "parameter_id": 1}
DEPTH = {"name": "depth", "source_name": "depth", "role": "coordinate", "dimensions": ["depth"], "unit": "m"}
TEMPERATURE = {
  "name": "temperature",
  "source_name": "temp",
  "role": "data",
  "dimensions": ["time", "depth"],
  "unit": "degC",
  "parameter_id": 5,
  "sensor_id": 7,
  "decoding": {"missing_values": [-9999]},
}
TEMPERATURE_FLAG = {
  "name": "temperature_qual",
  "source_name": "temp_qual",
  "role": "flag",
  "dimensions": ["time", "depth"],
  "flag_for": "temperature",
}
THERMISTOR_CHAIN = [TIME, DEPTH, TEMPERATURE, TEMPERATURE_FLAG]


@pytest.fixture(autouse=True)
async def vocabularies(session):
  session.add_all([Parameter(id=1, name="Time"), Parameter(id=5, name="Water temperature"), Sensor(id=7, name="TidBit MX2203")])
  await session.commit()


async def add_dataset(session, kind=DatasetKind.data) -> Dataset:
  dataset = Dataset(title="Thermistor chain", kind=kind, external_url="https://example.org" if kind == DatasetKind.link else None)
  session.add(dataset)
  await session.commit()
  await session.refresh(dataset)
  return dataset


async def add_variables(session, dataset_id: int, *variables: dict) -> list[Variable]:
  stored = [Variable(dataset_id=dataset_id, **variable) for variable in variables]
  session.add_all(stored)
  await session.commit()
  for variable in stored:
    await session.refresh(variable)
  return stored


async def add_job(session, dataset_id: int, status=JobStatus.running) -> Job:
  job = Job(type=JobType.rebuild, status=status, dataset_id=dataset_id)
  session.add(job)
  await session.commit()
  await session.refresh(job)
  return job


async def stored_variables(session, dataset_id: int) -> list[Variable]:
  session.expire_all()  # read what the request committed, not the objects cached in the session
  return list((await session.exec(select(Variable).where(Variable.dataset_id == dataset_id).order_by(col(Variable.id)))).all())


async def version(session, dataset: Dataset) -> int:
  await session.refresh(dataset)
  return dataset.version


def with_fields(variable: dict, **fields) -> dict:
  return {**variable, **fields}


# ---------------------------------------------------------------------------
# PUT /datasets/{dataset_id}/variables
# ---------------------------------------------------------------------------


async def test_set_variables(client, session):
  dataset = await add_dataset(session)
  response = await client.put(f"/datasets/{dataset.id}/variables", json=THERMISTOR_CHAIN)
  assert response.status_code == 200
  body = response.json()
  assert [v["name"] for v in body] == ["time", "depth", "temperature", "temperature_qual"]
  temperature = body[2]
  assert temperature == {
    "id": temperature["id"],
    "dataset_id": dataset.id,
    "name": "temperature",
    "source_name": "temp",
    "role": "data",
    "dimensions": ["time", "depth"],
    "unit": "degC",
    "parameter_id": 5,
    "sensor_id": 7,
    "flag_for": None,
    "description": None,
    "decoding": {"missing_values": [-9999]},
    "legacy_axis": None,
  }
  assert body[3]["flag_for"] == "temperature"
  assert [v.name for v in await stored_variables(session, dataset.id)] == ["time", "depth", "temperature", "temperature_qual"]
  assert await version(session, dataset) == 2


async def test_set_variables_without_legacy_axis_keeps_the_stored_one(client, session):
  """e.g. saving inspect suggestions, which have no legacy_axis, over migrated variables"""
  dataset = await add_dataset(session)
  await add_variables(session, dataset.id, with_fields(TIME, legacy_axis="x"), with_fields(DEPTH, legacy_axis="y"))
  response = await client.put(f"/datasets/{dataset.id}/variables", json=[TIME, DEPTH])
  assert response.status_code == 200
  assert [v["legacy_axis"] for v in response.json()] == ["x", "y"]
  assert await version(session, dataset) == 1  # nothing changed


async def test_set_variables_with_the_stored_legacy_axis(client, session):
  """Sending a stored variable's own legacy_axis is fine, e.g. sending back what GET returned."""
  dataset = await add_dataset(session)
  await add_variables(session, dataset.id, with_fields(TIME, legacy_axis="x"), DEPTH)
  response = await client.put(f"/datasets/{dataset.id}/variables", json=[with_fields(TIME, legacy_axis="x"), with_fields(DEPTH, legacy_axis=None)])
  assert response.status_code == 200
  assert [v["legacy_axis"] for v in response.json()] == ["x", None]
  assert await version(session, dataset) == 1  # nothing changed


@pytest.mark.parametrize("axis", ["a", "X", "z_1", "", "x1y"])
async def test_set_variables_with_invalid_legacy_axis(client, session, axis):
  dataset = await add_dataset(session)
  response = await client.put(f"/datasets/{dataset.id}/variables", json=[with_fields(TIME, legacy_axis=axis)])
  assert response.status_code == 422
  assert response.json() == {"detail": "0.legacy_axis: String should match pattern '^[Mxyz][0-9]*$'", "code": "validation_error"}


async def test_set_variables_replaces_the_stored_ones(client, session):
  """Variables are matched by name: a kept name is updated in place, a new name is added and a missing name is removed."""
  dataset = await add_dataset(session)
  time, _depth, temperature = await add_variables(
    session, dataset.id, with_fields(TIME, legacy_axis="x"), with_fields(DEPTH, legacy_axis="y"), with_fields(TEMPERATURE, legacy_axis="z")
  )
  oxygen = {"name": "oxygen", "source_name": "do", "role": "data", "dimensions": ["time"], "unit": "mg/L"}
  response = await client.put(f"/datasets/{dataset.id}/variables", json=[TIME, with_fields(TEMPERATURE, dimensions=["time"], unit="K"), oxygen])
  assert response.status_code == 200
  body = {v["name"]: v for v in response.json()}
  assert list(body) == ["time", "temperature", "oxygen"]
  # updated in place: same ID, legacy_axis kept as it wasn't sent
  assert (body["time"]["id"], body["time"]["legacy_axis"]) == (time.id, "x")
  assert (body["temperature"]["id"], body["temperature"]["legacy_axis"]) == (temperature.id, "z")
  assert (body["temperature"]["dimensions"], body["temperature"]["unit"]) == (["time"], "K")
  assert body["oxygen"]["legacy_axis"] is None

  stored = {v.name: v for v in await stored_variables(session, dataset.id)}
  assert set(stored) == {"time", "temperature", "oxygen"}  # depth was removed
  assert stored["temperature"].unit == "K"
  assert await version(session, dataset) == 2


async def test_set_unchanged_variables_keeps_the_version(client, session):
  dataset = await add_dataset(session)
  await add_variables(session, dataset.id, *THERMISTOR_CHAIN)
  response = await client.put(f"/datasets/{dataset.id}/variables", json=THERMISTOR_CHAIN)
  assert response.status_code == 200
  assert await version(session, dataset) == 1


async def test_set_an_empty_list_removes_all_variables(client, session):
  dataset = await add_dataset(session)
  await add_variables(session, dataset.id, *THERMISTOR_CHAIN)
  response = await client.put(f"/datasets/{dataset.id}/variables", json=[])
  assert response.status_code == 200
  assert response.json() == []
  assert await stored_variables(session, dataset.id) == []
  assert await version(session, dataset) == 2


async def test_link_dataset_cant_have_variables(client, session):
  dataset = await add_dataset(session, kind=DatasetKind.link)
  response = await client.put(f"/datasets/{dataset.id}/variables", json=THERMISTOR_CHAIN)
  assert response.status_code == 422
  assert response.json() == {"detail": f"Dataset {dataset.id} is a link dataset and can't have variables", "code": "link_dataset"}


@pytest.mark.parametrize("status", [JobStatus.queued, JobStatus.running])
async def test_set_variables_while_a_job_is_in_progress(client, session, status):
  dataset = await add_dataset(session)
  await add_variables(session, dataset.id, TIME)
  job = await add_job(session, dataset.id, status)
  response = await client.put(f"/datasets/{dataset.id}/variables", json=THERMISTOR_CHAIN)
  assert response.status_code == 409
  assert response.json() == {"detail": f"Dataset {dataset.id} already has a job {status} (job {job.id})", "code": "job_in_progress"}
  assert [v.name for v in await stored_variables(session, dataset.id)] == ["time"]


@pytest.mark.parametrize(
  ("variables", "detail"),
  [
    ([TIME, TEMPERATURE], "temperature: dimension 'depth' is not a time or coordinate variable of dataset {id}"),
    (
      [TIME, DEPTH, with_fields(TEMPERATURE, dimensions=["time", "depth", "time"])],
      "temperature: dimension 'time' is listed more than once",
    ),
    (
      # a data variable isn't a dimension
      [TIME, DEPTH, TEMPERATURE, with_fields(TEMPERATURE_FLAG, dimensions=["temperature"])],
      "temperature_qual: dimension 'temperature' is not a time or coordinate variable of dataset {id}",
    ),
    ([TIME, DEPTH, TEMPERATURE, TEMPERATURE], "Variable name 'temperature' is used more than once"),
    (
      [TIME, DEPTH, TEMPERATURE, with_fields(TEMPERATURE_FLAG, flag_for=None)],
      "temperature_qual: a flag variable needs flag_for, the name of the data variable it flags",
    ),
    ([TIME, DEPTH, with_fields(TEMPERATURE_FLAG, flag_for="oxygen")], "temperature_qual: flag_for 'oxygen' is not a data variable of dataset {id}"),
    ([TIME, DEPTH, with_fields(TEMPERATURE_FLAG, flag_for="depth")], "temperature_qual: flag_for 'depth' is not a data variable of dataset {id}"),
    ([TIME, with_fields(DEPTH, flag_for="time")], "depth: only flag variables can have flag_for"),
    ([with_fields(TIME, name="Time")], "1.name: String should match pattern '^[a-z][a-z0-9_]*$'"),
    ([with_fields(TIME, dimensions=[])], "1.dimensions: List should have at least 1 item after validation, not 0"),
    ([with_fields(TIME, role="axis")], "1.role: Input should be 'time', 'coordinate', 'data' or 'flag'"),
  ],
  ids=[
    "unknown_dimension",
    "repeated_dimension",
    "data_as_dimension",
    "duplicate_name",
    "flag_without_flag_for",
    "flag_for_unknown_variable",
    "flag_for_a_coordinate",
    "flag_for_on_a_coordinate",
    "invalid_name",
    "no_dimensions",
    "unknown_role",
  ],
)
async def test_set_invalid_variables(client, session, variables, detail):
  dataset = await add_dataset(session)
  await add_variables(session, dataset.id, TIME)
  # pydantic errors are numbered from the list's first item
  variables = [TIME, *variables] if detail.startswith("1.") else variables
  response = await client.put(f"/datasets/{dataset.id}/variables", json=variables)
  assert response.status_code == 422
  assert response.json() == {"detail": detail.format(id=dataset.id), "code": "validation_error"}
  # nothing changed
  assert [v.name for v in await stored_variables(session, dataset.id)] == ["time"]
  assert await version(session, dataset) == 1


@pytest.mark.parametrize(
  ("variable", "detail"),
  [(with_fields(TEMPERATURE, parameter_id=999), "Parameter 999 not found"), (with_fields(TEMPERATURE, sensor_id=999), "Sensor 999 not found")],
)
async def test_set_variables_with_unknown_reference(client, session, variable, detail):
  dataset = await add_dataset(session)
  response = await client.put(f"/datasets/{dataset.id}/variables", json=[TIME, DEPTH, variable])
  assert response.status_code == 422
  assert response.json() == {"detail": detail, "code": "unknown_reference"}
  assert await stored_variables(session, dataset.id) == []


# ---------------------------------------------------------------------------
# POST /datasets/{dataset_id}/variables
# ---------------------------------------------------------------------------


async def test_add_variables_one_by_one(client, session):
  dataset = await add_dataset(session)
  for variable in THERMISTOR_CHAIN:
    response = await client.post(f"/datasets/{dataset.id}/variables", json=variable)
    assert response.status_code == 201, response.json()
  body = response.json()
  assert body == {
    "id": body["id"],
    "dataset_id": dataset.id,
    "name": "temperature_qual",
    "source_name": "temp_qual",
    "role": "flag",
    "dimensions": ["time", "depth"],
    "unit": None,
    "parameter_id": None,
    "sensor_id": None,
    "flag_for": "temperature",
    "description": None,
    "decoding": None,
    "legacy_axis": None,
  }
  assert [v.name for v in await stored_variables(session, dataset.id)] == ["time", "depth", "temperature", "temperature_qual"]
  assert await version(session, dataset) == 5  # one increase per variable


async def test_add_variable_with_a_taken_legacy_axis(client, session):
  dataset, other = await add_dataset(session), await add_dataset(session)
  await add_variables(session, dataset.id, with_fields(TIME, legacy_axis="x"))
  response = await client.post(f"/datasets/{dataset.id}/variables", json=with_fields(DEPTH, legacy_axis="x"))
  assert response.status_code == 409
  assert response.json() == {"detail": f"Dataset {dataset.id} already has a variable with legacy_axis x (time)", "code": "already_exists"}
  assert await version(session, dataset) == 1
  # another dataset can use the same axis
  response = await client.post(f"/datasets/{other.id}/variables", json=with_fields(TIME, legacy_axis="x"))
  assert response.status_code == 201


async def test_add_variable_before_its_coordinates(client, session):
  dataset = await add_dataset(session)
  await add_variables(session, dataset.id, TIME)
  response = await client.post(f"/datasets/{dataset.id}/variables", json=TEMPERATURE)
  assert response.status_code == 422
  assert response.json() == {
    "detail": f"temperature: dimension 'depth' is not a time or coordinate variable of dataset {dataset.id}",
    "code": "validation_error",
  }
  assert [v.name for v in await stored_variables(session, dataset.id)] == ["time"]


async def test_add_variable_with_a_coordinate_of_another_dataset(client, session):
  dataset, other = await add_dataset(session), await add_dataset(session)
  await add_variables(session, dataset.id, TIME)
  await add_variables(session, other.id, TIME, DEPTH)
  response = await client.post(f"/datasets/{dataset.id}/variables", json=TEMPERATURE)
  assert response.status_code == 422
  assert response.json()["code"] == "validation_error"


@pytest.mark.parametrize(
  ("variable", "detail"),
  [
    (with_fields(TEMPERATURE_FLAG, flag_for="oxygen"), "temperature_qual: flag_for 'oxygen' is not a data variable of dataset {id}"),
    (with_fields(TEMPERATURE_FLAG, flag_for=None), "temperature_qual: a flag variable needs flag_for, the name of the data variable it flags"),
    (with_fields(TEMPERATURE, name="oxygen", flag_for="temperature"), "oxygen: only flag variables can have flag_for"),
  ],
)
async def test_add_variable_with_invalid_flag_for(client, session, variable, detail):
  dataset = await add_dataset(session)
  await add_variables(session, dataset.id, TIME, DEPTH, TEMPERATURE)
  response = await client.post(f"/datasets/{dataset.id}/variables", json=variable)
  assert response.status_code == 422
  assert response.json() == {"detail": detail.format(id=dataset.id), "code": "validation_error"}


async def test_add_variable_to_link_dataset(client, session):
  dataset = await add_dataset(session, kind=DatasetKind.link)
  response = await client.post(f"/datasets/{dataset.id}/variables", json=TIME)
  assert response.status_code == 422
  assert response.json() == {"detail": f"Dataset {dataset.id} is a link dataset and can't have variables", "code": "link_dataset"}


async def test_add_variable_with_invalid_name(client, session):
  dataset = await add_dataset(session)
  response = await client.post(f"/datasets/{dataset.id}/variables", json=with_fields(TIME, name="2nd_time"))
  assert response.status_code == 422
  assert response.json() == {"detail": "name: String should match pattern '^[a-z][a-z0-9_]*$'", "code": "validation_error"}
