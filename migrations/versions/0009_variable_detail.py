"""add the detail of a variable, which tells apart variables of the same parameter, e.g. air temperature at 2m and at 4m

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-19 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
  op.add_column("variables", sa.Column("detail", sqlmodel.sql.sqltypes.AutoString(), nullable=True))


def downgrade() -> None:
  op.drop_column("variables", "detail")
