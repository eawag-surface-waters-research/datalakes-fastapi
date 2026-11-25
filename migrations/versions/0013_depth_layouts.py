"""depth layouts: the depth axes of a timeSeriesProfile's files, and one partition per year and layout

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-24 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
  op.create_table(
    "depth_layouts",
    sa.Column("id", sa.Integer(), nullable=False),
    sa.Column("dataset_id", sa.Integer(), nullable=False),
    sa.Column("number", sa.Integer(), nullable=False),
    sa.Column("depths", postgresql.ARRAY(sa.Float()), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], name=op.f("fk_depth_layouts_dataset_id_datasets"), ondelete="CASCADE"),
    sa.PrimaryKeyConstraint("id", name=op.f("pk_depth_layouts")),
    sa.UniqueConstraint("dataset_id", "number", name=op.f("uq_depth_layouts_dataset_id_number")),
  )
  op.add_column("partitions", sa.Column("layout", sa.Integer(), nullable=True))
  op.drop_constraint(op.f("uq_partitions_dataset_id_year"), "partitions", type_="unique")
  op.create_unique_constraint(
    op.f("uq_partitions_dataset_id_year_layout"), "partitions", ["dataset_id", "year", "layout"], postgresql_nulls_not_distinct=True
  )


def downgrade() -> None:
  op.execute("DELETE FROM partitions WHERE layout IS NOT NULL")
  op.drop_constraint(op.f("uq_partitions_dataset_id_year_layout"), "partitions", type_="unique")
  op.create_unique_constraint(op.f("uq_partitions_dataset_id_year"), "partitions", ["dataset_id", "year"])
  op.drop_column("partitions", "layout")
  op.drop_table("depth_layouts")
