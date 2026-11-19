from typing import Any

import sqlalchemy as sa
from pydantic import ConfigDict, field_validator, model_validator
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlmodel import Field, SQLModel

from app.enums import VariableRole
from app.errors import invalid
from app.schemas.vocabularies import ParameterRead
from app.sql import enum_type

NAME_PATTERN = r"^[a-z][a-z0-9_]*$"


class VariableBase(SQLModel):
  name: str = Field(schema_extra={"pattern": NAME_PATTERN})
  source_name: str
  role: VariableRole = Field(sa_type=enum_type(VariableRole, "variable_role"))
  dimensions: list[str] = Field(min_length=1, sa_type=ARRAY(sa.String))
  unit: str | None = None
  parameter_id: int | None = Field(default=None, foreign_key="parameters.id", index=True)
  flag_for: str | None = None
  description: str | None = None
  decoding: dict[str, Any] | None = Field(default=None, sa_type=JSONB)


LEGACY_AXIS_PATTERN = r"^[Mxyz][0-9]*$"
LEGACY_AXIS_DESCRIPTION = (
  "The variable's axis in the old API (`x`, `y`, `z` or `M`, optionally followed by a number, e.g. `z1`), "
  "so that old links that pick variables with `axis:[...]` keep working. Unique within a dataset. `null` if the variable has none. "
  "Can only be set when the variable is added, and can't be changed afterwards."
)


class VariableCreate(VariableBase):
  """
  A new variable. Unknown fields are ignored.
  """

  legacy_axis: str | None = Field(default=None, schema_extra={"pattern": LEGACY_AXIS_PATTERN, "examples": ["z"]}, description=LEGACY_AXIS_DESCRIPTION)


class VariableUpdate(SQLModel):
  """
  The fields to change. Fields that aren't sent stay as they are, and `null` clears a field.
  Anything else is rejected, e.g. `legacy_axis`, which can't be changed.
  """

  model_config = ConfigDict(extra="forbid")  # type: ignore[assignment]

  name: str | None = Field(default=None, schema_extra={"pattern": NAME_PATTERN})
  source_name: str | None = None
  role: VariableRole | None = None
  dimensions: list[str] | None = Field(default=None, min_length=1)
  unit: str | None = None
  parameter_id: int | None = None
  flag_for: str | None = None
  description: str | None = None
  decoding: dict[str, Any] | None = None

  @model_validator(mode="before")
  @classmethod
  def legacy_axis_cant_change(cls, data: Any) -> Any:
    if isinstance(data, dict) and "legacy_axis" in data:
      raise invalid("legacy_axis can't be changed: it can only be set when the variable is added")
    return data

  @field_validator("name", "source_name", "role", "dimensions")
  @classmethod
  def not_null(cls, value: Any) -> Any:
    # only runs for fields that were sent, so a missing `name` is fine but `"name": null` isn't
    if value is None:
      raise invalid("Can't be null")
    return value


class VariableRead(VariableBase):
  id: int
  dataset_id: int
  legacy_axis: str | None = Field(description=LEGACY_AXIS_DESCRIPTION)
  parameter: ParameterRead | None = Field(default=None, description="The parameter of `parameter_id`. Only with `expand=true`.")
