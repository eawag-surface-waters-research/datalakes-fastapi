"""the depth range of a partition, for timeSeriesProfile datasets

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-24 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
  op.add_column("partitions", sa.Column("depth_min", sa.Float(), nullable=True))
  op.add_column("partitions", sa.Column("depth_max", sa.Float(), nullable=True))


def downgrade() -> None:
  op.drop_column("partitions", "depth_max")
  op.drop_column("partitions", "depth_min")
