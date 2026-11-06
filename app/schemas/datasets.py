from datetime import datetime
from typing import Any

from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.enums import DatasetKind, DatasetStatus, FeatureType
from app.sql import enum_type


class DatasetBase(SQLModel):
  title: str = Field(min_length=1)
  description: str | None = None
  status: DatasetStatus = Field(
    default=DatasetStatus.draft, index=True, sa_type=enum_type(DatasetStatus, "dataset_status"), sa_column_kwargs={"server_default": "draft"}
  )
  external_url: str | None = None
  feature_type: FeatureType | None = Field(default=None, index=True, sa_type=enum_type(FeatureType, "feature_type"))
  site_id: int | None = Field(default=None, foreign_key="sites.id", index=True)
  person_id: int | None = Field(default=None, foreign_key="persons.id", index=True)
  organisation_id: int | None = Field(default=None, foreign_key="organisations.id", index=True)
  license_id: int | None = Field(default=None, foreign_key="licenses.id", index=True)
  repository_url: str | None = None
  origin: str | None = None
  citation: str | None = None
  datasource_link: str | None = None
  plot_config: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
  update_interval: int | None = Field(default=None, ge=1)
  sync_interval: int | None = Field(default=None, ge=1)
  latitude: float | None = Field(default=None, ge=-90, le=90)
  longitude: float | None = Field(default=None, ge=-180, le=180)


class DatasetCreate(DatasetBase):
  kind: DatasetKind = DatasetKind.data


class DatasetRead(DatasetBase):
  id: int
  kind: DatasetKind
  time_start: datetime | None
  time_end: datetime | None
  depth_min: float | None
  depth_max: float | None
  downloads: int
  version: int
  created_at: datetime
  updated_at: datetime
  created_by: int | None
