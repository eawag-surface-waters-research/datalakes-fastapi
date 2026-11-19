"""drop the update and sync intervals of datasets: syncs are always triggered by an external system, never on a schedule

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-19 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
  op.drop_column("datasets", "update_interval")
  op.drop_column("datasets", "sync_interval")


def downgrade() -> None:
  op.add_column("datasets", sa.Column("update_interval", sa.Integer(), nullable=True))
  op.add_column("datasets", sa.Column("sync_interval", sa.Integer(), nullable=True))
