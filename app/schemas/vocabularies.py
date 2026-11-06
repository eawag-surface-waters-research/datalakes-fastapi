from sqlmodel import Field, SQLModel


class ParameterBase(SQLModel):
  name: str = Field(unique=True)
  cf_name: str | None = None
  description: str | None = None
  unit: str | None = None
  characteristic: str | None = None
  name_de: str | None = None
  name_fr: str | None = None
  name_it: str | None = None


class OrganisationBase(SQLModel):
  name: str = Field(unique=True)
  link: str | None = None


class PersonBase(SQLModel):
  name: str
  organisation_id: int | None = Field(default=None, foreign_key="organisations.id", index=True)
  email: str | None = None


class SensorBase(SQLModel):
  name: str = Field(unique=True)
  manufacturer: str | None = None
  accuracy: str | None = None
  link: str | None = None


class LicenseBase(SQLModel):
  name: str = Field(unique=True)
  description: str | None = None
  link: str | None = None


class SiteTypeBase(SQLModel):
  name: str
  description: str | None = None
