from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Field, SQLModel

from app.sql import TIMESTAMPTZ


class FileBase(SQLModel):
  path: str
  size: int = Field(sa_type=sa.BigInteger)
  modified_at: datetime = Field(sa_type=TIMESTAMPTZ)
  hash: str | None = None
