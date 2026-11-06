from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Field, SQLModel

from app.sql import TIMESTAMPTZ


class CoverageBase(SQLModel):
  start: datetime = Field(sa_type=TIMESTAMPTZ)
  end: datetime = Field(sa_type=TIMESTAMPTZ)
  valid_count: int = Field(sa_type=sa.BigInteger)
