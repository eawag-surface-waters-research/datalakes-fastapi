from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Field

from app.enums import FileStatus
from app.schemas.files import FileBase
from app.sql import TIMESTAMPTZ, enum_type


class File(FileBase, table=True):
  """A file in a dataset's archive, copied from its source."""

  __tablename__ = "files"  # type: ignore[assignment]
  __table_args__ = (
    sa.UniqueConstraint("dataset_id", "path"),
    sa.Index("ix_files_dataset_id_time_start", "dataset_id", "time_start"),
  )
  id: int | None = Field(default=None, primary_key=True)
  dataset_id: int = Field(foreign_key="datasets.id", ondelete="CASCADE")
  time_start: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ)
  time_end: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ)
  depth_layout: int | None = Field(default=None, ge=1)
  status: FileStatus = Field(default=FileStatus.new, sa_type=enum_type(FileStatus, "file_status"), sa_column_kwargs={"server_default": "new"})
  error: str | None = Field(default=None, sa_type=sa.Text)
