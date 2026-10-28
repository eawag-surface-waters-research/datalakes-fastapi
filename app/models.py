from datetime import datetime

from pydantic import field_validator
from sqlalchemy.types import JSON, TIMESTAMP
from sqlmodel import Column, Field, SQLModel

from app.functions import validate_ssh_url


class DatasetsBase(SQLModel):
  title: str | None = None
  description: str | None = None
  owner: str | None = None
  origin: str | None = None
  mapplot: str | None = None
  mapplotfunction: str | None = None
  datasource: str | None = None
  datasourcelink: str | None = None
  citation: str | None = None
  downloads: int | None = None
  fileconnect: str | None = None
  liveconnect: str | None = None
  renku: int | None = None
  prefile: str | None = None
  prescript: str | None = None
  mindepth: float | None = None
  maxdepth: float | None = None
  latitude: float | None = None
  longitude: float | None = None
  licenses_id: int | None = None
  organisations_id: int | None = None
  repositories_id: int | None = None
  lakes_id: int | None = None
  persons_id: int | None = None
  projects_id: int | None = None
  embargo: int | None = None
  password: str | None = None
  accompanyingdata: str | None = None
  dataportal: str | None = None
  monitor: int | None = None


class Datasets(DatasetsBase, table=True):
  __tablename__ = "datasets"
  id: int | None = Field(default=None, primary_key=True)
  plotproperties: dict | None = Field(default=None, sa_column=Column(JSON))
  mindatetime: datetime | None = Field(default=None, sa_type=TIMESTAMP(timezone=True))
  maxdatetime: datetime | None = Field(default=None, sa_type=TIMESTAMP(timezone=True))


class DatasetsCreate(DatasetsBase):
  id: int | None = None
  plotproperties: dict | None = None
  mindatetime: datetime | None = None
  maxdatetime: datetime | None = None


class DatasetsUpdate(DatasetsBase):
  plotproperties: dict | None = None
  mindatetime: datetime | None = None
  maxdatetime: datetime | None = None


class RepositoriesBase(SQLModel):
  ssh: str = Field(..., description="SSH URL for git repository (git@host:user/repo.git format)")
  branch: str | None = None

  @classmethod
  def get_allowed_domains(cls) -> list[str]:
    """Override this method in subclasses to customize allowed domains."""
    return ["github.com", "gitlab.com", "bitbucket.org", "gitlab.eawag.ch"]

  @field_validator("ssh")
  @classmethod
  def validate_ssh_url(cls, v: str) -> str:
    """
    Validate SSH URL using the shared validator.
    """
    allowed_domains = cls.get_allowed_domains()
    return validate_ssh_url(v, allowed_domains)


class Repositories(RepositoriesBase, table=True):
  __tablename__ = "repositories"
  id: int | None = Field(default=None, primary_key=True)
  status: str | None = None


class MaintenanceBase(SQLModel):
  depths: str | None = None
  description: str | None = None
  reporter: str | None = None
  state: str | None = None
  issue: str | None = None
  request: str | None = None


class MaintenanceCreate(MaintenanceBase):
  datasets_id: int
  parameters_id: int
  datasetparameters_id: int
  starttime: datetime | None = None
  endtime: datetime | None = None


class MaintenanceUpdate(MaintenanceBase):
  datasets_id: int | None = None
  parameters_id: int | None = None
  datasetparameters_id: int | None = None
  starttime: datetime | None = None
  endtime: datetime | None = None


class Maintenance(MaintenanceBase, table=True):
  __tablename__ = "maintenance"
  id: int | None = Field(default=None, primary_key=True)
  datasets_id: int
  parameters_id: int
  datasetparameters_id: int
  starttime: datetime | None = Field(default=None, sa_type=TIMESTAMP(timezone=True))
  endtime: datetime | None = Field(default=None, sa_type=TIMESTAMP(timezone=True))


class DatasetparametersBase(SQLModel):
  datasets_id: int
  parameters_id: int
  sensors_id: int | None = None
  axis: str
  parseparameter: str
  unit: str | None = None
  link: int | None = None
  detail: str | None = None


class Datasetparameters(DatasetparametersBase, table=True):
  __tablename__ = "datasetparameters"
  id: int | None = Field(default=None, primary_key=True)


class Parameters(SQLModel, table=True):
  __tablename__ = "parameters"
  id: int | None = Field(default=None, primary_key=True)
  name: str
  cfname: str
  description: str | None = None
  unit: str
  characteristic: str
  german: str | None = None
  french: str | None = None
  italian: str | None = None


class Lakes(SQLModel, table=True):
  __tablename__ = "lakes"
  id: int | None = Field(default=None, primary_key=True)
  name: str
  elevation: float | None = None
  depth: float | None = None
  morphology: bool | None = False


class Organisations(SQLModel, table=True):
  __tablename__ = "organisations"
  id: int | None = Field(default=None, primary_key=True)
  name: str
  link: str | None = None


class Persons(SQLModel, table=True):
  __tablename__ = "persons"
  id: int | None = Field(default=None, primary_key=True)
  name: str
  email: str
  organisations_id: int | None = Field(default=None, foreign_key="organisations.id")


class Projects(SQLModel, table=True):
  __tablename__ = "projects"
  id: int | None = Field(default=None, primary_key=True)
  name: str
  link: str | None = None


class Sensors(SQLModel, table=True):
  __tablename__ = "sensors"
  id: int | None = Field(default=None, primary_key=True)
  name: str
  manufacturer: str | None = None
  accuracy: str | None = None
  link: str | None = None


class Licenses(SQLModel, table=True):
  __tablename__ = "licenses"
  id: int | None = Field(default=None, primary_key=True)
  name: str
  description: str
  link: str
