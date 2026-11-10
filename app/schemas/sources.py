from datetime import datetime
from typing import Any

import sqlalchemy as sa
from pydantic import BaseModel
from pydantic import Field as PydanticField
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlmodel import Field, SQLModel

from app.enums import SourceBackend
from app.errors import ErrorResponse
from app.sql import enum_type


class SourceBase(SQLModel):
  backend: SourceBackend = Field(sa_type=enum_type(SourceBackend, "source_backend"))
  options: dict[str, Any] = Field(sa_type=JSONB)
  path: str = Field(default="", sa_column_kwargs={"server_default": ""})
  include: list[str] = Field(default_factory=list, sa_type=ARRAY(sa.String), sa_column_kwargs={"server_default": "{}"})


class SourceEntry(BaseModel):
  """A file in a source."""

  path: str = PydanticField(description="Path relative to the source's `path`, with its folders.", examples=["2024/L2_ThermistorChain_202406.nc"])
  size: int = PydanticField(description="Size in bytes.")
  modified_at: datetime | None = PydanticField(description="When the file last changed in the source. `null` when the backend doesn't report it.")


class SourceTestResult(BaseModel):
  """The result of testing a source."""

  ok: bool = PydanticField(description="Whether the source could be listed anonymously.")
  error: ErrorResponse | None = PydanticField(description=("Why the source couldn't be read, or `null`."))
  files: list[SourceEntry] = PydanticField(
    description=(
      "The first files the source's `include` patterns select, up to 100, including those in subfolders. These are the files a sync would fetch."
    )
  )
  truncated: bool = PydanticField(description="`true` when there are more than 100 files.")


class SourceTypeOption(BaseModel):
  """An option of a backend. Options are always strings."""

  name: str
  required: bool
  description: str
  example: str


class SourceType(BaseModel):
  """A backend a source can use."""

  backend: SourceBackend
  name: str
  description: str
  path_description: str = Field(description="What `path` means for this backend.")
  options: list[SourceTypeOption] = Field(description="The options accepted in a source's `options`.")
