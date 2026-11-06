from sqlmodel import Field

from app.schemas.vocabularies import LicenseBase, OrganisationBase, ParameterBase, PersonBase, SensorBase, SiteTypeBase


class Parameter(ParameterBase, table=True):
  __tablename__ = "parameters"  # type: ignore[assignment]
  id: int | None = Field(default=None, primary_key=True)


class Organisation(OrganisationBase, table=True):
  __tablename__ = "organisations"  # type: ignore[assignment]
  id: int | None = Field(default=None, primary_key=True)


class Person(PersonBase, table=True):
  __tablename__ = "persons"  # type: ignore[assignment]
  id: int | None = Field(default=None, primary_key=True)


class Sensor(SensorBase, table=True):
  __tablename__ = "sensors"  # type: ignore[assignment]
  id: int | None = Field(default=None, primary_key=True)


class License(LicenseBase, table=True):
  __tablename__ = "licenses"  # type: ignore[assignment]
  id: int | None = Field(default=None, primary_key=True)


class SiteType(SiteTypeBase, table=True):
  """An entry of the `site_types` vocabulary, identified by its name."""

  __tablename__ = "site_types"  # type: ignore[assignment]
  name: str = Field(primary_key=True)
