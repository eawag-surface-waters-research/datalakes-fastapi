from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlmodel import Field, SQLModel

from app.enums import VariableRole
from app.sql import enum_type


class VariableBase(SQLModel):
  name: str = Field(regex=r"^[a-z][a-z0-9_]*$")
  source_name: str
  role: VariableRole = Field(sa_type=enum_type(VariableRole, "variable_role"))
  dimensions: list[str] = Field(min_length=1, sa_type=ARRAY(sa.String))
  unit: str | None = None
  parameter_id: int | None = Field(default=None, foreign_key="parameters.id", index=True)
  sensor_id: int | None = Field(default=None, foreign_key="sensors.id", index=True)
  flag_for: str | None = None
  description: str | None = None
  decoding: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
