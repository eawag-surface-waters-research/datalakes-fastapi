from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlmodel import Field, SQLModel

from app.enums import JobStatus, JobType
from app.sql import enum_type


class RegenerateRunBase(SQLModel):
  dataset_ids: list[int] | None = Field(default=None, sa_type=ARRAY(sa.Integer))
  only_failed: bool = Field(default=False, sa_column_kwargs={"server_default": sa.false()})
  sync: bool = Field(default=False, sa_column_kwargs={"server_default": sa.false()})


class JobBase(SQLModel):
  type: JobType = Field(sa_type=enum_type(JobType, "job_type"))
  params: dict[str, Any] = Field(default_factory=dict, sa_type=JSONB, sa_column_kwargs={"server_default": sa.text("'{}'::jsonb")})


class JobRead(SQLModel):
  id: int = Field(description="Job ID.", schema_extra={"examples": [5812]})
  dataset_id: int | None = Field(description="The dataset the job works on, or `null` for jobs that aren't about one dataset.")
  type: JobType
  status: JobStatus
  progress: float | None = Field(ge=0, le=1, description="Fraction done, from 0 to 1, or `null` if unknown.")
  error: str | None = Field(description="Why the job failed, or `null`.")
  log: str = Field(description="The last lines of the job's log.")
  result: dict[str, Any] | None = Field(description="What the job produced. Its shape depends on `type`. `null` until the job ends.")
  created_at: datetime = Field(description="When the job was queued.")
  started_at: datetime | None = Field(description="When the worker started the job.")
  finished_at: datetime | None = Field(description="When the job ended.")
