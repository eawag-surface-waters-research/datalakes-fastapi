from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlmodel import Field, SQLModel

from app.enums import VariableRole
from app.schemas.vocabularies import ParameterRead, SensorRead
from app.sql import enum_type


class VariableBase(SQLModel):
  name: str = Field(schema_extra={"pattern": r"^[a-z][a-z0-9_]*$"})
  source_name: str
  role: VariableRole = Field(sa_type=enum_type(VariableRole, "variable_role"))
  dimensions: list[str] = Field(min_length=1, sa_type=ARRAY(sa.String))
  unit: str | None = None
  parameter_id: int | None = Field(default=None, foreign_key="parameters.id", index=True)
  sensor_id: int | None = Field(default=None, foreign_key="sensors.id", index=True)
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


class VariableRead(VariableBase):
  id: int
  dataset_id: int
  legacy_axis: str | None = Field(description=LEGACY_AXIS_DESCRIPTION)
  parameter: ParameterRead | None = Field(default=None, description="The parameter of `parameter_id`. Only with `expand=true`.")
  sensor: SensorRead | None = Field(default=None, description="The sensor of `sensor_id`. Only with `expand=true`.")
