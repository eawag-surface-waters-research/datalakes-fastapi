from sqlmodel import SQLModel, Field

class Parameters(SQLModel, table=True):
    __tablename__ = "parameters"
    id: int | None = Field(default=None, primary_key=True)
    name: str
    cfname: str
    description: str
    unit: str
    characteristic: str
    german: str
    french: str
    italian: str

class Lakes(SQLModel, table=True):
    __tablename__ = "lakes"
    id: int | None = Field(default=None, primary_key=True)
    name: str
    elevation: float
    depth: float
    morphology: bool

class Organisations(SQLModel, table=True):
    __tablename__ = "organisations"
    id: int | None = Field(default=None, primary_key=True)
    name: str
    link: str


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
    link: str


class Sensors(SQLModel, table=True):
    __tablename__ = "sensors"
    id: int | None = Field(default=None, primary_key=True)
    name: str
    manufacturer: str
    accuracy: str
    link: str


class Licenses(SQLModel, table=True):
    __tablename__ = "licenses"
    id: int | None = Field(default=None, primary_key=True)
    name: str
    description: str
    link: str
