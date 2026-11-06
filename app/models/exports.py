from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Field

from app.enums import ExportStatus
from app.schemas.exports import ExportBase
from app.sql import TIMESTAMPTZ, created_at_field, enum_type


class Export(ExportBase, table=True):
  """A large download prepared by the worker. The ID is a hash of the dataset, its version and the query."""

  __tablename__ = "exports"  # type: ignore[assignment]
  id: str = Field(primary_key=True)
  dataset_id: int = Field(foreign_key="datasets.id", ondelete="CASCADE", index=True)
  version: int
  status: ExportStatus = Field(
    default=ExportStatus.queued, index=True, sa_type=enum_type(ExportStatus, "export_status"), sa_column_kwargs={"server_default": "queued"}
  )
  size: int | None = Field(default=None, sa_type=sa.BigInteger)
  path: str | None = None
  job_id: int | None = Field(default=None, foreign_key="jobs.id", ondelete="SET NULL", index=True)
  error: str | None = Field(default=None, sa_type=sa.Text)
  created_at: datetime | None = created_at_field()
  ready_at: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ)
  expires_at: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ)
