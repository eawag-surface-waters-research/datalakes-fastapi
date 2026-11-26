from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.enums import FeatureType
from app.schemas.variables import VariableCommon
from app.schemas.vocabularies import ParameterRead


class InspectRequest(BaseModel):
  """The file to inspect."""

  file: str | None = Field(
    default=None,
    min_length=1,
    description="Path of a file in the dataset's archive, as listed by `GET /datasets/{dataset_id}/files`. Only NetCDF files (`.nc`) are supported. "
    "Leave it out to inspect the first file of that list.",
    examples=["L2_ThermistorChain_202406.nc"],
  )


class SampleSummary(BaseModel):
  """What the sample of a variable's values looks like. Only part of a large variable is read, so this can miss rare values."""

  count: int = Field(description="Number of values read.")
  missing: int = Field(description="Values that are NaN, a declared fill value, or a placeholder found by inspect.")
  min: float | None = Field(description="Smallest valid value, or `null` if there is none or the values aren't numeric.")
  max: float | None = Field(description="Largest valid value, or `null` if there is none or the values aren't numeric.")


class ParameterCandidate(BaseModel):
  """A parameter the variable may measure."""

  parameter: ParameterRead = Field(description="The parameter from the `parameters` vocabulary. Send its `id` as the variable's `parameter_id`.")
  score: float = Field(
    description="How well it matches: up to 100 for the names, plus up to 50 if other published datasets chose it for a variable of the same name.",
    examples=[95.0],
  )


class InspectedVariable(VariableCommon):
  """
  A suggested variable, with what was found in the file.
  To create it, send it with `parameter_id` set, e.g. to the `parameter.id` of the first of the `parameter_candidates`.
  """

  parameter_candidates: list[ParameterCandidate] = Field(
    default_factory=list,
    description="Up to 5 parameters the variable may measure, best first. Empty for flags and if the vocabulary is empty. "
    "Even the first may be a weak match, so check it.",
  )

  shape: list[int] = Field(description="Size of each dimension in the inspected file.")
  attributes: dict[str, Any] = Field(description="The variable's attributes in the file, e.g. `units`, `long_name` and `standard_name`.")
  sample: SampleSummary


class TimeRange(BaseModel):
  """The time covered by the file, decoded from the time variable's CF units."""

  start: datetime
  end: datetime
  count: int = Field(description="Number of time values in the file.")
  units: str = Field(examples=["seconds since 1970-01-01 00:00:00"])
  calendar: str | None = Field(description="The time variable's `calendar`, or `null` for the default (`standard`).")


class Location(BaseModel):
  latitude: float
  longitude: float


class ScalarVariable(BaseModel):
  """A variable without a dimension, e.g. the depth of a mooring sensor. It can't become a variable, but tells where or when the data is from."""

  source_name: str
  value: Any
  unit: str | None
  axis: str | None = Field(description="`time`, `vertical`, `latitude` or `longitude` if the variable describes one, otherwise `null`.")


class InspectResult(BaseModel):
  """The response of inspect. Nothing is saved: the suggestions are meant to be reviewed before the variables are created."""

  file: str = Field(description="The file inspected.")
  format: Literal["netcdf3", "netcdf4"] = Field(description="File format. `netcdf4` also covers HDF5.")
  dimensions: dict[str, int] = Field(description="The file's dimensions and their sizes.")
  feature_type: FeatureType | None = Field(description="The suggested feature type, or `null` if it couldn't be guessed.")
  feature_type_reason: str = Field(description="Why this feature type was suggested.")
  time_range: TimeRange | None = Field(description="The time covered by the file, or `null` if it has no time variable with CF units.")
  location: Location | None = Field(
    description="Where the data is from, when the file gives it as scalar variables or global attributes instead of as variables along a dimension."
  )
  variables: list[InspectedVariable] = Field(
    description="Suggested variables, time and coordinates first. `parameter_candidates` are guesses from the variable's names and the history."
  )
  scalars: list[ScalarVariable] = Field(description="Variables without a dimension. They aren't suggested as variables.")
  global_attributes: dict[str, Any] = Field(description="The file's global attributes.")
  warnings: list[str] = Field(description="Things to check before saving, e.g. missing time units, placeholders for missing values or odd units.")
