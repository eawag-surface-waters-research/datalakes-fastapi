import sqlalchemy as sa
from sqlmodel import Field

from app.schemas.variables import VariableBase


class Variable(VariableBase, table=True):
  """A variable of a dataset"""

  __tablename__ = "variables"  # type: ignore[assignment]
  __table_args__ = (
    sa.UniqueConstraint("dataset_id", "name"),
    sa.UniqueConstraint("dataset_id", "legacy_axis"),
  )
  id: int | None = Field(default=None, primary_key=True)
  dataset_id: int = Field(foreign_key="datasets.id", ondelete="CASCADE")
  # The old axis (`x`, `y`, `z1`…), set only by the migration so old iframe links keep working (=> read-only)
  legacy_axis: str | None = None
