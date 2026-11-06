from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlmodel import Field, SQLModel

from app.enums import JobType
from app.sql import enum_type


class RegenerateRunBase(SQLModel):
  dataset_ids: list[int] | None = Field(default=None, sa_type=ARRAY(sa.Integer))
  only_failed: bool = Field(default=False, sa_column_kwargs={"server_default": sa.false()})
  sync: bool = Field(default=False, sa_column_kwargs={"server_default": sa.false()})


class JobBase(SQLModel):
  type: JobType = Field(sa_type=enum_type(JobType, "job_type"))
  params: dict[str, Any] = Field(default_factory=dict, sa_type=JSONB, sa_column_kwargs={"server_default": sa.text("'{}'::jsonb")})
