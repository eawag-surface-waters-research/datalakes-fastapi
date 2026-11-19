from collections import Counter
from typing import Annotated

from fastapi import APIRouter, Body
from fastapi.openapi.models import Example
from sqlmodel import col, select

from app.database import SessionDep
from app.enums import DatasetKind, VariableRole
from app.errors import ApiError, ErrorResponse, not_found
from app.models import Dataset, Parameter, Variable
from app.openapi import PUBLIC_TAG
from app.params import Expand
from app.references import Reference, check_references, expand_references
from app.routes.sync import CONFLICT_RESPONSE, ensure_no_active_job
from app.schemas import ParameterRead, VariableBase, VariableCreate, VariableRead, VariableUpdate

router = APIRouter(prefix="/datasets", tags=["Variables"])

REFERENCES: dict[str, Reference] = {
  "parameter_id": Reference("Parameter", Parameter, ParameterRead),
}

AXIS_ROLES = (VariableRole.time, VariableRole.coordinate)  # the roles a dimension can name
VARIABLE_FIELDS = set(VariableBase.model_fields)  # the fields of a variable that can be set, without `id`, `dataset_id` and `legacy_axis`

TIME = {"name": "time", "source_name": "time", "role": "time", "dimensions": ["time"], "parameter_id": 1}
DEPTH = {"name": "depth", "source_name": "depth", "role": "coordinate", "dimensions": ["depth"], "unit": "m", "parameter_id": 2}
TEMPERATURE = {
  "name": "temperature",
  "source_name": "temp",
  "role": "data",
  "dimensions": ["time", "depth"],
  "unit": "degC",
  "parameter_id": 5,
  "description": "Water temperature from the calibrated loggers",
  "decoding": {"missing_values": [-9999]},
}
TEMPERATURE_FLAG = {
  "name": "temperature_qual",
  "source_name": "temp_qual",
  "role": "flag",
  "dimensions": ["time", "depth"],
  "flag_for": "temperature",
  "description": "1 where the temperature failed the quality checks",
}

VARIABLE_CREATE_EXAMPLES: dict[str, Example] = {
  "data": {"summary": "A data variable on time and depth", "value": TEMPERATURE},
  "time": {"summary": "A time coordinate, decoded from the CF units in the file", "value": TIME},
  "flag": {"summary": "A quality flag for a data variable", "value": TEMPERATURE_FLAG},
  "legacy_axis": {"summary": "A variable that old links know as axis z", "value": {**TEMPERATURE, "legacy_axis": "z"}},
}
VARIABLE_LIST_EXAMPLES: dict[str, Example] = {
  "thermistor_chain": {"summary": "The variables of a thermistor chain", "value": [TIME, DEPTH, TEMPERATURE, TEMPERATURE_FLAG]},
}


def read_example(id: int, variable: dict) -> dict:
  defaults = {"unit": None, "parameter_id": None, "flag_for": None, "description": None, "decoding": None, "legacy_axis": None}
  return {"id": id, "dataset_id": 123, **defaults, **variable}


NOT_FOUND_RESPONSE = {404: {"model": ErrorResponse, "description": "The dataset doesn't exist."}}
LINK_DATASET_EXAMPLE = {
  "summary": "The dataset is a link dataset",
  "value": {"detail": "Dataset 2 is a link dataset and can't have variables", "code": "link_dataset"},
}
UNKNOWN_DIMENSION_EXAMPLE = {
  "summary": "A dimension isn't a coordinate of the dataset",
  "value": {"detail": "temperature: dimension 'depth' is not a time or coordinate variable of dataset 123", "code": "validation_error"},
}
UNKNOWN_REFERENCE_EXAMPLE = {
  "summary": "The parameter doesn't exist",
  "value": {"detail": "Parameter 999 not found", "code": "unknown_reference"},
}


async def get_dataset(session: SessionDep, dataset_id: int) -> Dataset:
  """
  The dataset, locked until the end of the request, so that concurrent changes to its variables run one after the other and no job can be queued between the job check and the commit.
  """
  dataset = await session.get(Dataset, dataset_id, with_for_update=True)
  if dataset is None:
    raise not_found("Dataset", dataset_id)
  if dataset.kind == DatasetKind.link:
    raise ApiError(422, "link_dataset", f"Dataset {dataset_id} is a link dataset and can't have variables")
  return dataset


def invalid_variable(variable: VariableBase, detail: str) -> ApiError:
  return ApiError(422, "validation_error", f"{variable.name}: {detail}")


def check_variables(dataset_id: int, variables: list[VariableBase]) -> None:
  """
  Checks a dataset's variables as a whole, as they will be stored: each name is used once, each dimension names a time or coordinate variable,
  and each flag variable names a data variable in `flag_for`.
  """
  for name, count in Counter(v.name for v in variables).items():
    if count > 1:
      raise ApiError(422, "validation_error", f"Variable name '{name}' is used more than once")
  axes = {v.name for v in variables if v.role in AXIS_ROLES}
  data = {v.name for v in variables if v.role == VariableRole.data}

  for variable in variables:
    for dimension, count in Counter(variable.dimensions).items():
      if count > 1:
        raise invalid_variable(variable, f"dimension '{dimension}' is listed more than once")
      if dimension not in axes:
        raise invalid_variable(variable, f"dimension '{dimension}' is not a time or coordinate variable of dataset {dataset_id}")

    if variable.role == VariableRole.flag:
      if variable.flag_for is None:
        raise invalid_variable(variable, "a flag variable needs flag_for, the name of the data variable it flags")
      if variable.flag_for not in data:
        raise invalid_variable(variable, f"flag_for '{variable.flag_for}' is not a data variable of dataset {dataset_id}")
    elif variable.flag_for is not None:
      raise invalid_variable(variable, "only flag variables can have flag_for")


async def stored_variables(session: SessionDep, dataset_id: int) -> list[Variable]:
  return list((await session.exec(select(Variable).where(Variable.dataset_id == dataset_id).order_by(col(Variable.id)))).all())


ROLE_ORDER = {VariableRole.time: 0, VariableRole.coordinate: 1, VariableRole.data: 2, VariableRole.flag: 2}  # data and flags keep their order

TIME_PARAMETER = {"id": 1, "name": "Time", "cf_name": "time", "description": "Time of the measurement.", "unit": None, "characteristic": None}
DEPTH_PARAMETER = {
  "id": 2,
  "name": "Depth",
  "cf_name": "depth",
  "description": "Distance below the water surface.",
  "unit": "m",
  "characteristic": "Physical",
}
TEMPERATURE_PARAMETER = {
  "id": 5,
  "name": "Water temperature",
  "cf_name": "water_temperature",
  "description": "In situ temperature of the water.",
  "unit": "degC",
  "characteristic": "Physical",
}
NO_TRANSLATIONS = {"name_de": None, "name_fr": None, "name_it": None}


@router.get(
  "/{dataset_id}/variables",
  response_model=list[VariableRead],
  response_model_exclude_unset=True,  # leaves out the referenced objects unless they're expanded
  summary="List a dataset's variables",
  tags=[PUBLIC_TAG],
  responses={
    200: {
      "description": "The variables.",
      "content": {
        "application/json": {
          "example": [
            {**read_example(901, {**TIME, "legacy_axis": "x"}), "parameter": {**TIME_PARAMETER, **NO_TRANSLATIONS}},
            {**read_example(902, {**DEPTH, "legacy_axis": "y"}), "parameter": {**DEPTH_PARAMETER, **NO_TRANSLATIONS}},
            {
              **read_example(903, {**TEMPERATURE, "legacy_axis": "z"}),
              "parameter": {**TEMPERATURE_PARAMETER, **NO_TRANSLATIONS},
            },
            {**read_example(904, {**TEMPERATURE_FLAG, "legacy_axis": "z1"}), "parameter": None},
          ]
        }
      },
    },
    **NOT_FOUND_RESPONSE,
  },
)
async def list_variables(dataset_id: int, session: SessionDep, expand: Expand = True):
  """
  Lists the variables of a dataset: coordinates (time, depth…) first, then data and flag variables.

  Variables migrated from the old API have a `legacy_axis`.

  - **Drafts:** their variables are returned like any other.
  - **Link datasets** have no variables, so the list is empty.

  Each variable has its `parameter` in full, unless `expand=false`.
  """
  if await session.get(Dataset, dataset_id) is None:
    raise not_found("Dataset", dataset_id)

  stored = sorted(await stored_variables(session, dataset_id), key=lambda v: ROLE_ORDER[v.role])  # stable, so each role stays ordered by ID
  variables = [VariableRead.model_validate(v) for v in stored]
  if expand:
    await expand_references(session, variables, REFERENCES)

  return variables


@router.put(
  "/{dataset_id}/variables",
  response_model=list[VariableRead],
  response_model_exclude_unset=True,  # leaves out the referenced objects, which are never expanded here
  summary="Set all of a dataset's variables",
  responses={
    200: {
      "description": "The dataset's variables, in the order sent. `parameter` isn't expanded.",
      "content": {
        "application/json": {
          "example": [read_example(901, TIME), read_example(902, DEPTH), read_example(903, TEMPERATURE), read_example(904, TEMPERATURE_FLAG)]
        }
      },
    },
    **NOT_FOUND_RESPONSE,
    **CONFLICT_RESPONSE,
    422: {
      "model": ErrorResponse,
      "description": "The variables are invalid, e.g. a name is used twice or a dimension doesn't name a coordinate, or the dataset is a link dataset.",
      "content": {
        "application/json": {
          "examples": {
            "unknown_dimension": UNKNOWN_DIMENSION_EXAMPLE,
            "duplicate_name": {
              "summary": "Two variables have the same name",
              "value": {"detail": "Variable name 'temperature' is used more than once", "code": "validation_error"},
            },
            "duplicate_legacy_axis": {
              "summary": "Two variables have the same legacy_axis",
              "value": {"detail": "legacy_axis 'z' is used by more than one variable: temperature, oxygen", "code": "validation_error"},
            },
            "legacy_axis_changed": {
              "summary": "A stored variable's legacy_axis is changed",
              "value": {"detail": "temperature: legacy_axis can't be changed on an existing variable (it is z)", "code": "validation_error"},
            },
            "unknown_reference": UNKNOWN_REFERENCE_EXAMPLE,
            "link_dataset": LINK_DATASET_EXAMPLE,
          }
        }
      },
    },
  },
)
async def set_variables(
  dataset_id: int,
  variables: Annotated[list[VariableCreate], Body(openapi_examples=VARIABLE_LIST_EXAMPLES)],
  session: SessionDep,
):
  """
  Sets all of the dataset's variables at once.

  The list is checked as a whole:
  - each `name` is used once, and so is each `legacy_axis`,
  - each of the `dimensions` names a `time` or `coordinate` variable of the list (a coordinate can name itself),
  - a `flag` variable names a `data` variable of the list in `flag_for`,
  - `parameter_id` exists in the parameters vocabulary.

  Variables are matched to the stored ones by `name`. A stored variable with the same name is updated and keeps its `id`,
  a new name adds a variable, and a stored variable whose name isn't in the list is removed. An empty list removes all variables.

  `legacy_axis` is the variable's axis in the old API (e.g. `z1`), so that old links keep working.
  It can only be set when a variable is added. A stored variable keeps its `legacy_axis`: leave it out or send the same value.

  Increases the dataset's `version` if anything changed. The variables can't be changed while a fetch, sync or rebuild of the dataset is queued
  or running.

  - **Drafts:** variables can be set as usual.
  - **Link datasets** can't have variables.

  **Access:** user (own datasets) or owner.
  """
  dataset = await get_dataset(session, dataset_id)
  await ensure_no_active_job(session, dataset_id)

  check_variables(dataset_id, list(variables))

  stored = {v.name: v for v in await stored_variables(session, dataset_id)}

  # legacy_axis can only be set when a variable is added: an existing variable keeps its own
  for variable in variables:
    existing = stored.get(variable.name)
    if existing is not None and "legacy_axis" in variable.model_fields_set and variable.legacy_axis != existing.legacy_axis:
      raise invalid_variable(variable, f"legacy_axis can't be changed on an existing variable (it is {existing.legacy_axis or 'null'})")

  legacy_axes = {v.name: stored[v.name].legacy_axis if v.name in stored else v.legacy_axis for v in variables}
  for axis, count in Counter(axis for axis in legacy_axes.values() if axis is not None).items():  # each legacy_axis is used at most once
    if count > 1:
      variable_name_users = [name for name, used in legacy_axes.items() if used == axis]
      raise ApiError(422, "validation_error", f"legacy_axis '{axis}' is used by more than one variable: {', '.join(variable_name_users)}")

  for variable in variables:
    await check_references(session, variable, REFERENCES)

  # Delete the removed variables before adding the new ones, as a new variable can take a removed one's legacy_axis,
  # and the unique constraint on (dataset_id, legacy_axis) is checked row by row.
  removed = [v for name, v in stored.items() if name not in legacy_axes]
  for variable in removed:
    await session.delete(variable)
  await session.flush()

  changed = bool(removed)
  result = []
  for variable in variables:
    existing = stored.get(variable.name)
    if existing is None:
      existing = Variable.model_validate(variable, update={"dataset_id": dataset_id})
      changed = True
    else:
      values = variable.model_dump(exclude={"legacy_axis"})
      if any(getattr(existing, field) != value for field, value in values.items()):
        existing.sqlmodel_update(values)
        changed = True
    session.add(existing)
    result.append(existing)

  if changed:
    dataset.version += 1
    session.add(dataset)
  await session.commit()
  for variable in result:
    await session.refresh(variable)
  return result


@router.post(
  "/{dataset_id}/variables",
  response_model=VariableRead,
  response_model_exclude_unset=True,  # leaves out the referenced objects, which are never expanded here
  status_code=201,
  summary="Add a variable",
  responses={
    201: {"description": "The variable was added.", "content": {"application/json": {"example": read_example(903, TEMPERATURE)}}},
    **NOT_FOUND_RESPONSE,
    409: {
      "model": ErrorResponse,
      "description": "The dataset already has a variable with this name or `legacy_axis`, or a fetch, sync or rebuild of it is queued or running.",
      "content": {
        "application/json": {
          "examples": {
            "already_exists": {
              "summary": "The name is taken",
              "value": {"detail": "Dataset 123 already has a variable named temperature", "code": "already_exists"},
            },
            "legacy_axis_taken": {
              "summary": "The legacy_axis is taken",
              "value": {"detail": "Dataset 123 already has a variable with legacy_axis z (temperature)", "code": "already_exists"},
            },
            "job_in_progress": {
              "summary": "A job of the dataset is running",
              "value": {"detail": "Dataset 123 already has a job running (job 5812)", "code": "job_in_progress"},
            },
          }
        }
      },
    },
    422: {
      "model": ErrorResponse,
      "description": "The variable is invalid, e.g. its dimensions don't name coordinates of the dataset, or the dataset is a link dataset.",
      "content": {
        "application/json": {
          "examples": {
            "unknown_dimension": UNKNOWN_DIMENSION_EXAMPLE,
            "unknown_reference": UNKNOWN_REFERENCE_EXAMPLE,
            "link_dataset": LINK_DATASET_EXAMPLE,
          }
        }
      },
    },
  },
)
async def create_variable(
  dataset_id: int,
  variable: Annotated[VariableCreate, Body(openapi_examples=VARIABLE_CREATE_EXAMPLES)],
  session: SessionDep,
):
  """
  Adds a variable to a dataset. Increases the dataset's `version`. To set all variables at once, use `PUT /datasets/{dataset_id}/variables`.

  A variable is described by what it is:
  - `role`: `time` or `coordinate` for the axes the data runs along, `data` for measured values, `flag` for quality flags.
  - `dimensions`: the names of the `time` and `coordinate` variables it runs along, in the order of the data. They must exist already, except
    that a coordinate can name itself: `depth` has `["depth"]`. So add the time and coordinates first.
  - `flag_for`: for a `flag` variable, the name of an existing `data` variable it flags. Other roles have no `flag_for`.

  `legacy_axis` is the variable's axis in the old API (e.g. `z1`), so that old links keep working. It's optional and unique within the dataset, and can't be changed once the variable exists.

  The variables can't be changed while a fetch, sync or rebuild of the dataset is queued or running.

  - **Drafts:** variables can be added as usual.
  - **Link datasets** can't have variables (422).

  **Access:** user (own datasets) or owner.
  """
  dataset = await get_dataset(session, dataset_id)
  await ensure_no_active_job(session, dataset_id)

  stored = await stored_variables(session, dataset_id)
  if any(v.name == variable.name for v in stored):
    raise ApiError(409, "already_exists", f"Dataset {dataset_id} already has a variable named {variable.name}")
  if variable.legacy_axis is not None and (owner := next((v for v in stored if v.legacy_axis == variable.legacy_axis), None)) is not None:
    raise ApiError(409, "already_exists", f"Dataset {dataset_id} already has a variable with legacy_axis {variable.legacy_axis} ({owner.name})")

  check_variables(dataset_id, [*stored, variable])
  await check_references(session, variable, REFERENCES)

  created = Variable.model_validate(variable, update={"dataset_id": dataset_id})
  dataset.version += 1
  session.add_all([created, dataset])
  await session.commit()
  await session.refresh(created)

  return created


async def get_variable(session: SessionDep, dataset_id: int, variable_id: int) -> tuple[Dataset, Variable]:
  """The dataset and its variable."""
  dataset = await session.get(Dataset, dataset_id, with_for_update=True)
  if dataset is None:
    raise not_found("Dataset", dataset_id)
  variable = await session.get(Variable, variable_id)
  if variable is None or variable.dataset_id != dataset_id:
    raise not_found("Variable", variable_id)
  return dataset, variable


def renamed(names: list[str], old: str, new: str) -> list[str]:
  return [new if name == old else name for name in names]


@router.patch(
  "/{dataset_id}/variables/{variable_id}",
  response_model=VariableRead,
  response_model_exclude_unset=True,  # leaves out the referenced objects, which are never expanded here
  summary="Update a variable",
  responses={
    200: {
      "description": "The updated variable.",
      "content": {"application/json": {"example": read_example(903, {**TEMPERATURE, "legacy_axis": "z"})}},
    },
    404: {"model": ErrorResponse, "description": "The dataset or the variable doesn't exist."},
    409: {
      "model": ErrorResponse,
      "description": "Another variable of the dataset already has this name, or a fetch, sync or rebuild of the dataset is queued or running.",
      "content": {
        "application/json": {
          "examples": {
            "already_exists": {
              "summary": "The name is taken",
              "value": {"detail": "Dataset 123 already has a variable named temperature", "code": "already_exists"},
            },
            "job_in_progress": {
              "summary": "A job of the dataset is running",
              "value": {"detail": "Dataset 123 already has a job running (job 5812)", "code": "job_in_progress"},
            },
          }
        }
      },
    },
    422: {
      "model": ErrorResponse,
      "description": (
        "The change is invalid, e.g. a dimension doesn't name a coordinate, the change breaks another variable, a required field is `null`, "
        "or `legacy_axis` is sent."
      ),
      "content": {
        "application/json": {
          "examples": {
            "unknown_dimension": UNKNOWN_DIMENSION_EXAMPLE,
            "breaks_another_variable": {
              "summary": "A coordinate that other variables use as a dimension becomes a data variable",
              "value": {"detail": "temperature: dimension 'depth' is not a time or coordinate variable of dataset 123", "code": "validation_error"},
            },
            "legacy_axis": {
              "summary": "legacy_axis is sent",
              "value": {"detail": "legacy_axis can't be changed: it can only be set when the variable is added", "code": "validation_error"},
            },
            "unknown_reference": UNKNOWN_REFERENCE_EXAMPLE,
          }
        }
      },
    },
  },
)
async def update_variable(
  dataset_id: int,
  variable_id: int,
  data: Annotated[
    VariableUpdate,
    Body(
      openapi_examples={
        "unit": {"summary": "Set the unit", "value": {"unit": "degC"}},
        "rename": {"summary": "Rename a coordinate", "value": {"name": "depth_below_surface"}},
      }
    ),
  ],
  session: SessionDep,
):
  """
  Only the fields sent are changed, and `null` clears a field. `name`, `source_name`, `role` and `dimensions` can't be `null`.
  Increases the dataset's `version` if anything changed.

  Renaming a coordinate also renames it in the `dimensions` of the variables that use it.
  Renaming a data variable also renames it in the `flag_for` of its flag variables.

  The dataset's variables are checked as a whole after the change, like in `PUT /datasets/{dataset_id}/variables`.

  `legacy_axis` can't be changed: it can only be set when the variable is added.

  The variables can't be changed while a fetch, sync or rebuild of the dataset is queued or running.

  **Access:** user (own datasets) or owner.
  """
  dataset, variable = await get_variable(session, dataset_id, variable_id)
  await ensure_no_active_job(session, dataset_id)

  changes = data.model_dump(exclude_unset=True)
  stored = await stored_variables(session, dataset_id)
  old_name, new_name = variable.name, changes.get("name", variable.name)
  if new_name != old_name and any(v.name == new_name for v in stored):
    raise ApiError(409, "already_exists", f"Dataset {dataset_id} already has a variable named {new_name}")
  await check_references(session, data, REFERENCES)

  def after_change(v: Variable) -> dict:
    """The fields of a variable once the change is applied, with a rename carried over to `dimensions` and `flag_for`."""
    values = v.model_dump(include=VARIABLE_FIELDS)
    if v.id == variable_id:
      values |= changes
    if new_name != old_name:
      if not (v.id == variable_id and "dimensions" in changes):  # dimensions that are sent already use the new name
        values["dimensions"] = renamed(values["dimensions"], old_name, new_name)
      if values["flag_for"] == old_name:
        values["flag_for"] = new_name
    return values

  # check the dataset's variables as they will be
  after = {v.id: after_change(v) for v in stored}
  check_variables(dataset_id, [VariableBase.model_construct(**values) for values in after.values()])

  if changed := [v for v in stored if v.model_dump(include=VARIABLE_FIELDS) != after[v.id]]:
    for updated_variable in changed:
      updated_variable.sqlmodel_update(after[updated_variable.id])
    dataset.version += 1
    session.add_all([*changed, dataset])
    await session.commit()
    await session.refresh(variable)

  return variable


@router.delete(
  "/{dataset_id}/variables/{variable_id}",
  status_code=204,
  summary="Remove a variable",
  responses={
    204: {"description": "The variable was removed."},
    404: {"model": ErrorResponse, "description": "The dataset or the variable doesn't exist."},
    409: {
      "model": ErrorResponse,
      "description": (
        "Other variables use this coordinate as a dimension, or flag this data variable, or a fetch, sync or rebuild of the dataset is queued or running."
      ),
      "content": {
        "application/json": {
          "examples": {
            "dimension": {
              "summary": "The coordinate is a dimension of other variables",
              "value": {"detail": "Variable 'depth' is a dimension of: temperature, temperature_qual", "code": "in_use"},
            },
            "flagged": {
              "summary": "A flag variable flags this variable",
              "value": {"detail": "Variable 'temperature' is flagged by: temperature_qual", "code": "in_use"},
            },
            "job_in_progress": {
              "summary": "A job of the dataset is running",
              "value": {"detail": "Dataset 123 already has a job running (job 5812)", "code": "job_in_progress"},
            },
          }
        }
      },
    },
  },
)
async def delete_variable(dataset_id: int, variable_id: int, session: SessionDep):
  """
  Removes a variable. Increases the dataset's `version`. The issues about the variable are removed with it.

  A coordinate that other variables use as a dimension can't be removed until they're removed or changed. Neither can a data variable that a
  flag variable names in `flag_for`.

  The variables can't be changed while a fetch, sync or rebuild of the dataset is queued or running.

  **Access:** user (own datasets) or owner.
  """
  dataset, variable = await get_variable(session, dataset_id, variable_id)
  await ensure_no_active_job(session, dataset_id)

  others = [v for v in await stored_variables(session, dataset_id) if v.id != variable_id]
  if referencing_variables := [v.name for v in others if variable.name in v.dimensions]:
    raise ApiError(409, "in_use", f"Variable '{variable.name}' is a dimension of: {', '.join(referencing_variables)}")
  if dependent_variables := [v.name for v in others if v.flag_for == variable.name]:
    raise ApiError(409, "in_use", f"Variable '{variable.name}' is flagged by: {', '.join(dependent_variables)}")

  await session.delete(variable)
  dataset.version += 1
  session.add(dataset)

  await session.commit()
