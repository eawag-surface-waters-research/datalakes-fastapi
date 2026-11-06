from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Field

from app.enums import DatasetKind
from app.schemas.datasets import DatasetBase
from app.sql import TIMESTAMPTZ, created_at_field, enum_type, updated_at_field


class Dataset(DatasetBase, table=True):
  __tablename__ = "datasets"  # type: ignore[assignment]
  id: int | None = Field(default=None, primary_key=True)
  # TODO: Move these to DatasetBase (and fill REFERENCES in routes/datasets.py) once sites, persons, organisations and licenses can be created
  site_id: int | None = Field(default=None, foreign_key="sites.id", index=True)
  person_id: int | None = Field(default=None, foreign_key="persons.id", index=True)
  organisation_id: int | None = Field(default=None, foreign_key="organisations.id", index=True)
  license_id: int | None = Field(default=None, foreign_key="licenses.id", index=True)
  kind: DatasetKind = Field(default=DatasetKind.data, sa_type=enum_type(DatasetKind, "dataset_kind"), sa_column_kwargs={"server_default": "data"})
  time_start: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ)
  time_end: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ, index=True)
  depth_min: float | None = None
  depth_max: float | None = None
  downloads: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
  version: int = Field(default=1, sa_column_kwargs={"server_default": "1"})
  created_at: datetime | None = created_at_field()
  updated_at: datetime | None = updated_at_field()
  created_by: int | None = Field(default=None, sa_type=sa.BigInteger)  # TODO: fill once authentication as added back
