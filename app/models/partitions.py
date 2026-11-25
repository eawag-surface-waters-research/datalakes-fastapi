from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY
from sqlmodel import Field, SQLModel

from app.sql import TIMESTAMPTZ, created_at_field, updated_at_field


class DepthLayout(SQLModel, table=True):
  """
  A depth axis that files of a `timeSeriesProfile` dataset have.
  Files with exactly the same depths share a layout, and a file's layout is its `depth_layout` in the file index. Layouts are
  numbered 1, 2, etc. in the order they're found, and keep their number: an axis that comes back gets its old layout.
  """

  __tablename__ = "depth_layouts"  # type: ignore[assignment]
  __table_args__ = (sa.UniqueConstraint("dataset_id", "number"),)
  id: int | None = Field(default=None, primary_key=True)
  dataset_id: int = Field(foreign_key="datasets.id", ondelete="CASCADE")
  number: int
  depths: list[float] = Field(sa_type=ARRAY(sa.Float))
  created_at: datetime | None = created_at_field()


class Partition(SQLModel, table=True):
  """
  One year (UTC) of a dataset's data, and the Zarr store that currently holds it. A `timeSeriesProfile` has one partition per year
  and depth layout, as files with different depths can't share a store. A year without data has no row.
  """

  __tablename__ = "partitions"  # type: ignore[assignment]
  __table_args__ = (sa.UniqueConstraint("dataset_id", "year", "layout", postgresql_nulls_not_distinct=True),)
  id: int | None = Field(default=None, primary_key=True)
  dataset_id: int = Field(foreign_key="datasets.id", ondelete="CASCADE")
  year: int
  layout: int | None = None  # the depth layout's number, for a `timeSeriesProfile`. `None` for a `timeSeries`
  path: str  # the current store, relative to the dataset's `zarr_dir`: `{year}/{job_id}`, or `{year}/{layout}/{job_id}` with a layout
  rows: int  # time steps in the store
  time_start: datetime = Field(sa_type=TIMESTAMPTZ)
  time_end: datetime = Field(sa_type=TIMESTAMPTZ)
  depth_min: float | None = None  # the range of the depth axis, for a `timeSeriesProfile`
  depth_max: float | None = None
  created_at: datetime | None = created_at_field()
  updated_at: datetime | None = updated_at_field()
