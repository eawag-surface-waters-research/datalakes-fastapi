from typing import Any

from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.enums import ExportFormat
from app.sql import enum_type


class ExportBase(SQLModel):
  format: ExportFormat = Field(sa_type=enum_type(ExportFormat, "export_format"))
  query: dict[str, Any] = Field(sa_type=JSONB)
