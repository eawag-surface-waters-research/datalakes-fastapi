"""cleanup job type: deletes a deleted dataset's files

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-13 10:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
  op.drop_constraint(op.f("ck_jobs_job_type"), "jobs", type_="check")
  op.create_check_constraint(op.f("ck_jobs_job_type"), "jobs", "type IN ('fetch', 'sync', 'rebuild', 'export', 'cleanup', 'test')")


def downgrade() -> None:
  op.execute("DELETE FROM jobs WHERE type = 'cleanup'")
  op.drop_constraint(op.f("ck_jobs_job_type"), "jobs", type_="check")
  op.create_check_constraint(op.f("ck_jobs_job_type"), "jobs", "type IN ('fetch', 'sync', 'rebuild', 'export', 'test')")
