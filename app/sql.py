from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlmodel import Field, SQLModel

SQLModel.metadata.naming_convention = {
  "ix": "ix_%(column_0_label)s",
  "uq": "uq_%(table_name)s_%(column_0_N_name)s",
  "ck": "ck_%(table_name)s_%(constraint_name)s",
  "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
  "pk": "pk_%(table_name)s",
}

TIMESTAMPTZ = sa.DateTime(timezone=True)


def enum_type(enum: type[StrEnum], name: str) -> sa.Enum:
  """Stores an enum's values as text with a CHECK constraint instead of a native Postgres enum, so adding a value is a plain migration."""
  return sa.Enum(enum, name=name, native_enum=False, create_constraint=True, length=32, values_callable=lambda e: [m.value for m in e])


def created_at_field() -> Any:
  return Field(default=None, nullable=False, sa_type=TIMESTAMPTZ, sa_column_kwargs={"server_default": sa.func.now()})


def updated_at_field() -> Any:
  return Field(default=None, nullable=False, sa_type=TIMESTAMPTZ, sa_column_kwargs={"server_default": sa.func.now(), "onupdate": sa.func.now()})
