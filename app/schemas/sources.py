from typing import Any, Literal

import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlmodel import Field, SQLModel

from app.enums import SourceBackend
from app.sql import enum_type


class SourceBase(SQLModel):
  backend: SourceBackend = Field(sa_type=enum_type(SourceBackend, "source_backend"))
  options: dict[str, Any] = Field(sa_type=JSONB)
  path: str = Field(default="", sa_column_kwargs={"server_default": ""})
  include: list[str] = Field(default_factory=list, sa_type=ARRAY(sa.String), sa_column_kwargs={"server_default": "{}"})


class SourceTypeOption(BaseModel):
  name: str
  type: Literal["string", "integer", "boolean"]
  required: bool
  description: str
  example: Any


class SourceType(BaseModel):
  """A backend a source can use."""

  backend: SourceBackend
  name: str = Field(description="Name to show in a form.")
  description: str
  path_description: str = Field(description="What `path` means for this backend.")
  options: list[SourceTypeOption] = Field(description="The options accepted in a source's `options`.")
