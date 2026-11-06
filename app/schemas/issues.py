from datetime import datetime

from sqlmodel import Field, SQLModel

from app.enums import IssueState
from app.sql import TIMESTAMPTZ, enum_type


class IssueBase(SQLModel):
  variable_id: int | None = Field(default=None, foreign_key="variables.id", ondelete="CASCADE", index=True)
  start: datetime = Field(sa_type=TIMESTAMPTZ)
  end: datetime | None = Field(default=None, sa_type=TIMESTAMPTZ)
  depth_min: float | None = None
  depth_max: float | None = None
  type: str
  description: str | None = None
  reporter: str | None = None
  state: IssueState | None = Field(default=None, sa_type=enum_type(IssueState, "issue_state"))
