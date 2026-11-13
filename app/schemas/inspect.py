from pydantic import BaseModel, Field


class InspectRequest(BaseModel):
  """The file to inspect."""

  file: str = Field(
    min_length=1,
    description="Path of a file in the dataset's archive, as listed by `GET /datasets/{dataset_id}/files`. Only NetCDF files (`.nc`) are supported.",
    examples=["L2_ThermistorChain_202406.nc"],
  )
