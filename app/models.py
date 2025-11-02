from sqlmodel import SQLModel, Field, Column
from sqlalchemy.types import TIMESTAMP, JSON
from typing import Optional
from datetime import datetime

class DatasetsBase(SQLModel):
    title: Optional[str] = None
    description: Optional[str] = None
    owner: Optional[str] = None
    origin: Optional[str] = None
    mapplot: Optional[str] = None
    mapplotfunction: Optional[str] = None
    datasource: Optional[str] = None
    datasourcelink: Optional[str] = None
    citation: Optional[str] = None
    downloads: Optional[int] = None
    fileconnect: Optional[str] = None
    liveconnect: Optional[str] = None
    renku: Optional[int] = None
    prefile: Optional[str] = None
    prescript: Optional[str] = None
    mindepth: Optional[float] = None
    maxdepth: Optional[float] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    licenses_id: Optional[int] = None
    organisations_id: Optional[int] = None
    repositories_id: Optional[int] = None
    lakes_id: Optional[int] = None
    persons_id: Optional[int] = None
    projects_id: Optional[int] = None
    embargo: Optional[int] = None
    password: Optional[str] = None
    accompanyingdata: Optional[str] = None
    dataportal: Optional[str] = None
    monitor: Optional[int] = None

class Datasets(DatasetsBase, table=True):
    __tablename__ = "datasets"
    id: Optional[int] = Field(default=None, primary_key=True)
    plotproperties: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    mindatetime: Optional[datetime] = Field(default=None, sa_type=TIMESTAMP(timezone=True))
    maxdatetime: Optional[datetime] = Field(default=None, sa_type=TIMESTAMP(timezone=True))

class DatasetsCreate(DatasetsBase):
    id: Optional[int] = None
    plotproperties: Optional[dict] = None
    mindatetime: Optional[datetime] = None
    maxdatetime: Optional[datetime] = None

class DatasetsUpdate(DatasetsBase):
    plotproperties: Optional[dict] = None
    mindatetime: Optional[datetime] = None
    maxdatetime: Optional[datetime] = None

class Repositories(SQLModel, table=True):
    __tablename__ = "repositories"
    id: int | None = Field(default=None, primary_key=True)
    ssh: str
    branch: str

class DatasetparametersBase(SQLModel):
    datasets_id: int
    parameters_id: int
    sensors_id: Optional[int] = None
    axis: str
    parseparameter: str
    unit: Optional[str] = None
    link: Optional[int] = None
    detail: Optional[str] = None

class Datasetparameters(DatasetparametersBase, table=True):
    __tablename__ = "datasetparameters"
    id: int | None = Field(default=None, primary_key=True)

class Parameters(SQLModel, table=True):
    __tablename__ = "parameters"
    id: int | None = Field(default=None, primary_key=True)
    name: str
    cfname: str
    description: Optional[str] = None
    unit: str
    characteristic: str
    german: Optional[str] = None
    french: Optional[str] = None
    italian: Optional[str] = None

class Lakes(SQLModel, table=True):
    __tablename__ = "lakes"
    id: int | None = Field(default=None, primary_key=True)
    name: str
    elevation: Optional[float] = None
    depth: Optional[float] = None
    morphology: Optional[bool] = False

class Organisations(SQLModel, table=True):
    __tablename__ = "organisations"
    id: int | None = Field(default=None, primary_key=True)
    name: str
    link: Optional[str] = None


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
    link: Optional[str] = None


class Sensors(SQLModel, table=True):
    __tablename__ = "sensors"
    id: int | None = Field(default=None, primary_key=True)
    name: str
    manufacturer: Optional[str] = None
    accuracy: Optional[str] = None
    link: Optional[str] = None


class Licenses(SQLModel, table=True):
    __tablename__ = "licenses"
    id: int | None = Field(default=None, primary_key=True)
    name: str
    description: str
    link: str
