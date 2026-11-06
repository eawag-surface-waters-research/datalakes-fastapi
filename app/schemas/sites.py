from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class SiteBase(SQLModel):
  name: str = Field(min_length=1)
  type: str = Field(foreign_key="site_types.name", index=True)
  geometry: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
  properties: dict[str, Any] = Field(default_factory=dict, sa_type=JSONB, sa_column_kwargs={"server_default": sa.text("'{}'::jsonb")})
