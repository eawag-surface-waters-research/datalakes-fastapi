"""the partitions table: the Zarr store that currently holds each year of a dataset's data

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-24 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
  op.create_table(
    "partitions",
    sa.Column("id", sa.Integer(), nullable=False),
    sa.Column("dataset_id", sa.Integer(), nullable=False),
    sa.Column("year", sa.Integer(), nullable=False),
    sa.Column("path", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column("rows", sa.Integer(), nullable=False),
    sa.Column("time_start", sa.DateTime(timezone=True), nullable=False),
    sa.Column("time_end", sa.DateTime(timezone=True), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], name=op.f("fk_partitions_dataset_id_datasets"), ondelete="CASCADE"),
    sa.PrimaryKeyConstraint("id", name=op.f("pk_partitions")),
    sa.UniqueConstraint("dataset_id", "year", name=op.f("uq_partitions_dataset_id_year")),
  )


def downgrade() -> None:
  op.drop_table("partitions")
