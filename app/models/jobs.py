from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from app.enums import JobStatus, RegenerateRunStatus
from app.schemas.jobs import JobBase, RegenerateRunBase
from app.sql import TIMESTAMPTZ, created_at_field, enum_type


class RegenerateRun(RegenerateRunBase, table=True):
  """A run that rebuilds many datasets"""

  __tablename__ = "regenerate_runs"  # type: ignore[assignment]
  id: int | None = Field(default=None, primary_key=True)
  status: RegenerateRunStatus = Field(
    default=RegenerateRunStatus.running,
    sa_type=enum_type(RegenerateRunStatus, "regenerate_run_status"),
    sa_column_kwargs={"server_default": "running"},
  )
  created_at: datetime | None = created_at_field()
  finished_at: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ)


class Job(JobBase, table=True):
  """Background work run by the worker."""

  __tablename__ = "jobs"  # type: ignore[assignment]
  __table_args__ = (
    sa.Index("ix_jobs_dataset_id_created_at", "dataset_id", "created_at"),
    sa.Index("ix_jobs_status_created_at", "status", "created_at"),
  )
  id: int | None = Field(default=None, primary_key=True)
  dataset_id: int | None = Field(default=None, foreign_key="datasets.id", ondelete="CASCADE")
  run_id: int | None = Field(default=None, foreign_key="regenerate_runs.id", ondelete="SET NULL", index=True)
  status: JobStatus = Field(default=JobStatus.queued, sa_type=enum_type(JobStatus, "job_status"), sa_column_kwargs={"server_default": "queued"})
  progress: float | None = None
  error: str | None = Field(default=None, sa_type=sa.Text)
  log: str = Field(default="", sa_type=sa.Text, sa_column_kwargs={"server_default": ""})
  result: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
  created_at: datetime | None = created_at_field()
  started_at: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ)
  finished_at: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ)
