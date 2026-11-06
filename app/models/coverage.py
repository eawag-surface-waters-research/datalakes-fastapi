import sqlalchemy as sa
from sqlmodel import Field

from app.schemas.coverage import CoverageBase


class Coverage(CoverageBase, table=True):
  """A period in which a variable has valid data."""

  __tablename__ = "coverage"  # type: ignore[assignment]
  __table_args__ = (sa.Index("ix_coverage_dataset_id_variable_id_start", "dataset_id", "variable_id", "start"),)
  id: int | None = Field(default=None, primary_key=True)
  dataset_id: int = Field(foreign_key="datasets.id", ondelete="CASCADE")
  variable_id: int = Field(foreign_key="variables.id", ondelete="CASCADE", index=True)
