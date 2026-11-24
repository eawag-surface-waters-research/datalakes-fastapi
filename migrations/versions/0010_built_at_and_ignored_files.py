"""when a dataset's data was last built, and the `ignored` status for files that aren't NetCDF

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-20 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
  op.add_column("datasets", sa.Column("built_at", sa.DateTime(timezone=True), nullable=True))
  op.drop_constraint(op.f("ck_files_file_status"), "files", type_="check")
  op.create_check_constraint(op.f("ck_files_file_status"), "files", "status IN ('new', 'ok', 'failed', 'ignored')")


def downgrade() -> None:
  op.execute("UPDATE files SET status = 'new', error = NULL WHERE status = 'ignored'")
  op.drop_constraint(op.f("ck_files_file_status"), "files", type_="check")
  op.create_check_constraint(op.f("ck_files_file_status"), "files", "status IN ('new', 'ok', 'failed')")
  op.drop_column("datasets", "built_at")
