from datetime import datetime

from sqlmodel import Field

from app.schemas.sources import SourceBase
from app.sql import TIMESTAMPTZ, created_at_field, updated_at_field


class Source(SourceBase, table=True):
  """Where a dataset's files come from."""

  __tablename__ = "sources"  # type: ignore[assignment]
  id: int | None = Field(default=None, primary_key=True)
  dataset_id: int = Field(unique=True, foreign_key="datasets.id", ondelete="CASCADE")
  last_tested_at: datetime = Field(sa_type=TIMESTAMPTZ)
  last_synced_at: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ)
  created_at: datetime | None = created_at_field()
  updated_at: datetime | None = updated_at_field()
