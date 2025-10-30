from sqlmodel import SQLModel, Field
from typing import Optional

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
