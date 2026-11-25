from datetime import datetime
from typing import Any

from sqlmodel import Field, SQLModel

from app.enums import FeatureType

"""
The response of the data endpoint, for the API docs. The endpoint writes the JSON itself, so large responses don't go through pydantic.
"""


class DataQuery(SQLModel):
  """The query as it was answered, with defaults filled in."""

  variables: list[str]
  start: datetime | None = Field(description="Start of the time range. The dataset's first time if `start` wasn't given.")
  end: datetime | None = Field(description="End of the time range. The dataset's last time if `end` wasn't given.")
  depth_min: float | None
  depth_max: float | None
  mask: bool


class DataCoordinate(SQLModel):
  """The values of a coordinate."""

  unit: str | None = Field(
    description="Unit of the values. `UTC` for times, which are ISO 8601 strings in milliseconds, e.g. `2024-03-01T00:10:00.000Z`.",
    schema_extra={"examples": ["UTC"]},
  )
  dimensions: list[str] = Field(description="The dimensions the coordinate runs along.", schema_extra={"examples": [["time"]]})
  values: list[Any]


class DataVariable(SQLModel):
  """The values of a variable."""

  parameter: str | None = Field(description="Name of the parameter the variable measures.", schema_extra={"examples": ["Water temperature"]})
  unit: str | None = Field(schema_extra={"examples": ["degC"]})
  dimensions: list[str] = Field(description="Names of the coordinates the values run along, outermost first.", schema_extra={"examples": [["time"]]})
  flag_for: str | None = Field(description="For flag variables, the variable they flag.")
  values: list[Any] = Field(description="Values nested by dimension. Missing values are `null`.")


class DataResponse(SQLModel):
  """A dataset's values, described by themselves. Coordinates and variables are keyed by name."""

  dataset_id: int
  version: int = Field(description="The dataset's version. Also sent as the `ETag` header.")
  feature_type: FeatureType
  query: DataQuery
  coordinates: dict[str, DataCoordinate]
  variables: dict[str, DataVariable]
