from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Field, SQLModel

from app.enums import FileStatus
from app.sql import TIMESTAMPTZ


class FileBase(SQLModel):
  path: str
  size: int = Field(sa_type=sa.BigInteger)
  modified_at: datetime = Field(sa_type=TIMESTAMPTZ)


class FileRead(SQLModel):
  """
  A file in the dataset's archive, copied from its source.
  The archive mirrors the source, so a file that is removed from the source is removed from the archive and this index at the next fetch or sync.
  """

  id: int = Field(schema_extra={"examples": [20411]})
  dataset_id: int = Field(schema_extra={"examples": [123]})
  path: str = Field(description="Name of the file in the source's folder.", schema_extra={"examples": ["L2_ThermistorChain_202409.nc"]})
  size: int = Field(description="Size in bytes.")
  modified_at: datetime = Field(description="When the file last changed in the source.")
  time_start: datetime | None = Field(description="Time of the file's first value. `null` until the file is processed.")
  time_end: datetime | None = Field(description="Time of the file's last value. `null` until the file is processed.")
  depth_layout: int | None = Field(
    ge=1,
    description="For datasets with a depth, the depth layout the file belongs to (1, 2, …). Files with exactly the same depths share a "
    "layout, and a new one starts when a file has depths not seen before, e.g. when a thermistor chain is redeployed with its sensors "
    "moved. Depths that come back get their old layout. `null` until the file is processed, and for datasets without a depth.",
  )
  status: FileStatus = Field(
    description="- `new`: copied from the source (new or changed there), not processed yet.\n"
    "- `ok`: processed into the dataset's data.\n"
    "- `failed`: couldn't be processed, as explained in `error`. It's tried again when the file changes in the source.\n"
    "- `ignored`: not a NetCDF file, so it's left out of the data. Only once the dataset has been built: before that, such a file fails the build."
  )
  error: str | None = Field(description="Why processing failed, or `null`.")
