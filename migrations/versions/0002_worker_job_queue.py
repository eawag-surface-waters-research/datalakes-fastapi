"""worker job queue: cancel requests, restart count and the test job type

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-10 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
  op.add_column("jobs", sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True))
  op.add_column("jobs", sa.Column("restarts", sa.Integer(), server_default="0", nullable=False))
  op.drop_constraint(op.f("ck_jobs_job_type"), "jobs", type_="check")
  op.create_check_constraint(op.f("ck_jobs_job_type"), "jobs", "type IN ('fetch', 'sync', 'rebuild', 'export', 'test')")


def downgrade() -> None:
  op.execute("DELETE FROM jobs WHERE type = 'test'")
  op.drop_constraint(op.f("ck_jobs_job_type"), "jobs", type_="check")
  op.create_check_constraint(op.f("ck_jobs_job_type"), "jobs", "type IN ('fetch', 'sync', 'rebuild', 'export')")
  op.drop_column("jobs", "restarts")
  op.drop_column("jobs", "cancel_requested_at")
