from datetime import datetime
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlmodel import Field, SQLModel

SQLModel.metadata.naming_convention = {
  "ix": "ix_%(column_0_label)s",
  "uq": "uq_%(table_name)s_%(column_0_N_name)s",
  "ck": "ck_%(table_name)s_%(constraint_name)s",
  "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
  "pk": "pk_%(table_name)s",
}

TIMESTAMPTZ = sa.DateTime(timezone=True)


def enum_type(enum: type[StrEnum], name: str) -> sa.Enum:
  """Stores an enum's values as text with a CHECK constraint instead of a native Postgres enum, so adding a value is a plain migration."""
  return sa.Enum(enum, name=name, native_enum=False, create_constraint=True, length=32, values_callable=lambda e: [m.value for m in e])


def created_at_field() -> Any:
  return Field(default=None, nullable=False, sa_type=TIMESTAMPTZ, sa_column_kwargs={"server_default": sa.func.now()})


def updated_at_field() -> Any:
  return Field(default=None, nullable=False, sa_type=TIMESTAMPTZ, sa_column_kwargs={"server_default": sa.func.now(), "onupdate": sa.func.now()})


# -----------------------------------------------------------------------------
# Enums
# -----------------------------------------------------------------------------


class DatasetStatus(StrEnum):
  published = "published"
  draft = "draft"


class DatasetKind(StrEnum):
  data = "data"
  link = "link"


class FeatureType(StrEnum):
  time_series = "timeSeries"
  time_series_profile = "timeSeriesProfile"
  profile = "profile"


class VariableRole(StrEnum):
  time = "time"
  coordinate = "coordinate"
  data = "data"
  flag = "flag"


class IssueCategory(StrEnum):
  problem = "problem"
  event = "event"


class IssueState(StrEnum):
  open = "open"
  resolved = "resolved"


class SourceBackend(StrEnum):
  s3 = "s3"
  switchdrive = "switchdrive"


class JobType(StrEnum):
  sync = "sync"
  inspect = "inspect"
  rebuild = "rebuild"
  export = "export"


class JobStatus(StrEnum):
  queued = "queued"
  running = "running"
  succeeded = "succeeded"
  failed = "failed"
  cancelled = "cancelled"


class FileStatus(StrEnum):
  new = "new"
  ok = "ok"
  failed = "failed"


class ExportFormat(StrEnum):
  netcdf = "netcdf"
  csv = "csv"


class ExportStatus(StrEnum):
  queued = "queued"
  running = "running"
  ready = "ready"
  failed = "failed"
  expired = "expired"


class RegenerateRunStatus(StrEnum):
  running = "running"
  finished = "finished"
  stopped = "stopped"


# -----------------------------------------------------------------------------
# Vocabularies
# -----------------------------------------------------------------------------


class ParameterBase(SQLModel):
  name: str = Field(unique=True)
  cf_name: str | None = None
  description: str | None = None
  unit: str | None = None
  characteristic: str | None = None
  name_de: str | None = None
  name_fr: str | None = None
  name_it: str | None = None


class Parameter(ParameterBase, table=True):
  __tablename__ = "parameters"
  id: int | None = Field(default=None, primary_key=True)


class OrganisationBase(SQLModel):
  name: str = Field(unique=True)
  link: str | None = None


class Organisation(OrganisationBase, table=True):
  __tablename__ = "organisations"
  id: int | None = Field(default=None, primary_key=True)


class PersonBase(SQLModel):
  name: str
  organisation_id: int | None = Field(default=None, foreign_key="organisations.id", index=True)
  email: str | None = None


class Person(PersonBase, table=True):
  __tablename__ = "persons"
  id: int | None = Field(default=None, primary_key=True)


class SensorBase(SQLModel):
  name: str = Field(unique=True)
  manufacturer: str | None = None
  accuracy: str | None = None
  link: str | None = None


class Sensor(SensorBase, table=True):
  __tablename__ = "sensors"
  id: int | None = Field(default=None, primary_key=True)


class LicenseBase(SQLModel):
  name: str = Field(unique=True)
  description: str | None = None
  link: str | None = None


class License(LicenseBase, table=True):
  __tablename__ = "licenses"
  id: int | None = Field(default=None, primary_key=True)


class SiteType(SQLModel, table=True):
  """An entry of the `site_types` vocabulary, identified by its name, e.g. `lake`."""

  __tablename__ = "site_types"
  name: str = Field(primary_key=True)
  description: str | None = None


# -----------------------------------------------------------------------------
# Sites, datasets, variables, collections, issues
# -----------------------------------------------------------------------------


class SiteBase(SQLModel):
  name: str = Field(min_length=1)
  type: str = Field(foreign_key="site_types.name", index=True)
  geometry: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
  properties: dict[str, Any] = Field(default_factory=dict, sa_type=JSONB, sa_column_kwargs={"server_default": sa.text("'{}'::jsonb")})


class Site(SiteBase, table=True):
  """A place where data is measured"""

  __tablename__ = "sites"
  id: int | None = Field(default=None, primary_key=True)
  # Bounding box of `geometry`, kept in sync by the API for `?bbox=` filters
  lon_min: float | None = None
  lat_min: float | None = None
  lon_max: float | None = None
  lat_max: float | None = None


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


class Dataset(DatasetBase, table=True):
  __tablename__ = "datasets"
  id: int | None = Field(default=None, primary_key=True)
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


class VariableBase(SQLModel):
  name: str = Field(regex=r"^[a-z][a-z0-9_]*$")
  source_name: str
  role: VariableRole = Field(sa_type=enum_type(VariableRole, "variable_role"))
  dimensions: list[str] = Field(min_length=1, sa_type=ARRAY(sa.String))
  unit: str | None = None
  parameter_id: int | None = Field(default=None, foreign_key="parameters.id", index=True)
  sensor_id: int | None = Field(default=None, foreign_key="sensors.id", index=True)
  flag_for: str | None = None
  description: str | None = None
  decoding: dict[str, Any] | None = Field(default=None, sa_type=JSONB)


class Variable(VariableBase, table=True):
  """A variable of a dataset"""

  __tablename__ = "variables"
  __table_args__ = (
    sa.UniqueConstraint("dataset_id", "name"),
    sa.UniqueConstraint("dataset_id", "legacy_axis"),
  )
  id: int | None = Field(default=None, primary_key=True)
  dataset_id: int = Field(foreign_key="datasets.id", ondelete="CASCADE")
  # The old axis (`x`, `y`, `z1`…), set only by the migration so old iframe links keep working (=> read-only)
  legacy_axis: str | None = None


class CollectionBase(SQLModel):
  slug: str = Field(unique=True, regex=r"^(?=.*[a-z])[a-z0-9]+(-[a-z0-9]+)*$")
  name: str = Field(min_length=1)
  description: str | None = None
  link: str | None = None
  site_id: int | None = Field(default=None, foreign_key="sites.id", index=True)
  geometry: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
  properties: dict[str, Any] = Field(default_factory=dict, sa_type=JSONB, sa_column_kwargs={"server_default": sa.text("'{}'::jsonb")})


class Collection(CollectionBase, table=True):
  """A group of datasets"""

  __tablename__ = "collections"
  id: int | None = Field(default=None, primary_key=True)


class CollectionDataset(SQLModel, table=True):
  __tablename__ = "collection_datasets"
  collection_id: int = Field(primary_key=True, foreign_key="collections.id", ondelete="CASCADE")
  dataset_id: int = Field(primary_key=True, foreign_key="datasets.id", ondelete="CASCADE", index=True)


class IssueBase(SQLModel):
  variable_id: int | None = Field(default=None, foreign_key="variables.id", ondelete="CASCADE", index=True)
  start: datetime = Field(sa_type=TIMESTAMPTZ)
  end: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ)
  depth_min: float | None = None
  depth_max: float | None = None
  type: str
  description: str | None = None
  reporter: str | None = None
  state: IssueState | None = Field(default=None, sa_type=enum_type(IssueState, "issue_state"))


class Issue(IssueBase, table=True):
  """A problem with a dataset's data (has a state, masked by `mask=true`) or an event that explains it (no state, never masked)."""

  __tablename__ = "issues"
  __table_args__ = (sa.Index("ix_issues_dataset_id_start", "dataset_id", "start"),)
  id: int | None = Field(default=None, primary_key=True)
  dataset_id: int = Field(foreign_key="datasets.id", ondelete="CASCADE")
  category: IssueCategory = Field(
    default=IssueCategory.problem, sa_type=enum_type(IssueCategory, "issue_category"), sa_column_kwargs={"server_default": "problem"}
  )
  created_at: datetime | None = created_at_field()
  updated_at: datetime | None = updated_at_field()


# -----------------------------------------------------------------------------
# Ingestion: sources, jobs, file index
# -----------------------------------------------------------------------------


class Source(SQLModel, table=True):
  """Where a dataset's files come from."""

  __tablename__ = "sources"
  id: int | None = Field(default=None, primary_key=True)
  dataset_id: int = Field(unique=True, foreign_key="datasets.id", ondelete="CASCADE")
  backend: SourceBackend = Field(sa_type=enum_type(SourceBackend, "source_backend"))
  options: dict[str, Any] = Field(sa_type=JSONB)
  path: str = Field(default="", sa_column_kwargs={"server_default": ""})
  include: list[str] = Field(default_factory=list, sa_type=ARRAY(sa.String), sa_column_kwargs={"server_default": "{}"})
  last_tested_at: datetime = Field(sa_type=TIMESTAMPTZ)
  last_synced_at: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ)
  created_at: datetime | None = created_at_field()
  updated_at: datetime | None = updated_at_field()


class RegenerateRun(SQLModel, table=True):
  """A run that rebuilds many datasets"""

  __tablename__ = "regenerate_runs"
  id: int | None = Field(default=None, primary_key=True)
  status: RegenerateRunStatus = Field(
    default=RegenerateRunStatus.running,
    sa_type=enum_type(RegenerateRunStatus, "regenerate_run_status"),
    sa_column_kwargs={"server_default": "running"},
  )
  dataset_ids: list[int] | None = Field(default=None, sa_type=ARRAY(sa.Integer))
  only_failed: bool = Field(default=False, sa_column_kwargs={"server_default": sa.false()})
  sync: bool = Field(default=False, sa_column_kwargs={"server_default": sa.false()})
  created_at: datetime | None = created_at_field()
  finished_at: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ)


class Job(SQLModel, table=True):
  """Background work run by the worker."""

  __tablename__ = "jobs"
  __table_args__ = (
    sa.Index("ix_jobs_dataset_id_created_at", "dataset_id", "created_at"),
    sa.Index("ix_jobs_status_created_at", "status", "created_at"),
  )
  id: int | None = Field(default=None, primary_key=True)
  dataset_id: int | None = Field(default=None, foreign_key="datasets.id", ondelete="CASCADE")
  run_id: int | None = Field(default=None, foreign_key="regenerate_runs.id", ondelete="SET NULL", index=True)
  type: JobType = Field(sa_type=enum_type(JobType, "job_type"))
  status: JobStatus = Field(default=JobStatus.queued, sa_type=enum_type(JobStatus, "job_status"), sa_column_kwargs={"server_default": "queued"})
  params: dict[str, Any] = Field(default_factory=dict, sa_type=JSONB, sa_column_kwargs={"server_default": sa.text("'{}'::jsonb")})
  progress: float | None = None
  error: str | None = Field(default=None, sa_type=sa.Text)
  log: str = Field(default="", sa_type=sa.Text, sa_column_kwargs={"server_default": ""})
  result: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
  created_at: datetime | None = created_at_field()
  started_at: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ)
  finished_at: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ)


class File(SQLModel, table=True):
  """A file in a dataset's archive, copied from its source."""

  __tablename__ = "files"
  __table_args__ = (
    sa.UniqueConstraint("dataset_id", "path"),
    sa.Index("ix_files_dataset_id_time_start", "dataset_id", "time_start"),
  )
  id: int | None = Field(default=None, primary_key=True)
  dataset_id: int = Field(foreign_key="datasets.id", ondelete="CASCADE")
  path: str
  size: int = Field(sa_type=sa.BigInteger)
  modified_at: datetime = Field(sa_type=TIMESTAMPTZ)
  hash: str | None = None
  time_start: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ)
  time_end: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ)
  status: FileStatus = Field(default=FileStatus.new, sa_type=enum_type(FileStatus, "file_status"), sa_column_kwargs={"server_default": "new"})
  error: str | None = Field(default=None, sa_type=sa.Text)


# -----------------------------------------------------------------------------
# Data: availability and exports
# -----------------------------------------------------------------------------


class Coverage(SQLModel, table=True):
  """A period in which a variable has valid data."""

  __tablename__ = "coverage"
  __table_args__ = (sa.Index("ix_coverage_dataset_id_variable_id_start", "dataset_id", "variable_id", "start"),)
  id: int | None = Field(default=None, primary_key=True)
  dataset_id: int = Field(foreign_key="datasets.id", ondelete="CASCADE")
  variable_id: int = Field(foreign_key="variables.id", ondelete="CASCADE", index=True)
  start: datetime = Field(sa_type=TIMESTAMPTZ)
  end: datetime = Field(sa_type=TIMESTAMPTZ)
  valid_count: int = Field(sa_type=sa.BigInteger)


class Export(SQLModel, table=True):
  """A large download prepared by the worker. The ID is a hash of the dataset, its version and the query, so identical requests share it."""

  __tablename__ = "exports"
  id: str = Field(primary_key=True)
  dataset_id: int = Field(foreign_key="datasets.id", ondelete="CASCADE", index=True)
  version: int
  query: dict[str, Any] = Field(sa_type=JSONB)
  format: ExportFormat = Field(sa_type=enum_type(ExportFormat, "export_format"))
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


# -----------------------------------------------------------------------------
# Maintenance
# -----------------------------------------------------------------------------


class SyncToken(SQLModel, table=True):
  """The token an external system uses to trigger syncs of one dataset. Only its hash is stored."""

  __tablename__ = "sync_tokens"
  dataset_id: int = Field(primary_key=True, foreign_key="datasets.id", ondelete="CASCADE")
  token_hash: str
  created_at: datetime | None = created_at_field()


class CatalogueVersion(SQLModel, table=True):
  """A single-row counter, bumped on any metadata change and used in the cache keys of listings."""

  __tablename__ = "catalogue_version"
  id: int = Field(default=1, primary_key=True)
  version: int = Field(default=1, sa_column_kwargs={"server_default": "1"})
