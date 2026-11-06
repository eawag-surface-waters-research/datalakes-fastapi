from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Field

from app.enums import IssueCategory
from app.schemas.issues import IssueBase
from app.sql import created_at_field, enum_type, updated_at_field


class Issue(IssueBase, table=True):
  """A problem with a dataset's data (has a state, masked by `mask=true`) or an event that explains it (no state, never masked)."""

  __tablename__ = "issues"  # type: ignore[assignment]
  __table_args__ = (sa.Index("ix_issues_dataset_id_start", "dataset_id", "start"),)
  id: int | None = Field(default=None, primary_key=True)
  dataset_id: int = Field(foreign_key="datasets.id", ondelete="CASCADE")
  category: IssueCategory = Field(
    default=IssueCategory.problem, sa_type=enum_type(IssueCategory, "issue_category"), sa_column_kwargs={"server_default": "problem"}
  )
  created_at: datetime | None = created_at_field()
  updated_at: datetime | None = updated_at_field()
