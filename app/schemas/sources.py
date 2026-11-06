from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlmodel import Field, SQLModel

from app.enums import SourceBackend
from app.sql import enum_type


class SourceBase(SQLModel):
  backend: SourceBackend = Field(sa_type=enum_type(SourceBackend, "source_backend"))
  options: dict[str, Any] = Field(sa_type=JSONB)
  path: str = Field(default="", sa_column_kwargs={"server_default": ""})
  include: list[str] = Field(default_factory=list, sa_type=ARRAY(sa.String), sa_column_kwargs={"server_default": "{}"})
