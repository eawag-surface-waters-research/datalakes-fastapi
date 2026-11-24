from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Field, SQLModel

from app.sql import TIMESTAMPTZ, created_at_field, updated_at_field


class Partition(SQLModel, table=True):
  """
  One year (UTC) of a dataset's data, and the Zarr store that currently holds it.
  A year without data has no row.
  """

  __tablename__ = "partitions"  # type: ignore[assignment]
  __table_args__ = (sa.UniqueConstraint("dataset_id", "year"),)
  id: int | None = Field(default=None, primary_key=True)
  dataset_id: int = Field(foreign_key="datasets.id", ondelete="CASCADE")
  year: int
  path: str  # the current store, relative to the dataset's `zarr_dir`, e.g. `2024/123` (year/job id)
  rows: int  # time steps in the store
  time_start: datetime = Field(sa_type=TIMESTAMPTZ)
  time_end: datetime = Field(sa_type=TIMESTAMPTZ)
  depth_min: float | None = None  # the range of the depth axis, for a `timeSeriesProfile`
  depth_max: float | None = None
  created_at: datetime | None = created_at_field()
  updated_at: datetime | None = updated_at_field()
