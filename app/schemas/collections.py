from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class CollectionBase(SQLModel):
  slug: str = Field(unique=True, regex=r"^(?=.*[a-z])[a-z0-9]+(-[a-z0-9]+)*$")
  name: str = Field(min_length=1)
  description: str | None = None
  link: str | None = None
  image_url: str | None = None
  site_id: int | None = Field(default=None, foreign_key="sites.id", index=True)
  geometry: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
  properties: dict[str, Any] = Field(default_factory=dict, sa_type=JSONB, sa_column_kwargs={"server_default": sa.text("'{}'::jsonb")})
