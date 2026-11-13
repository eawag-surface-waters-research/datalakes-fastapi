"""source path moves into the S3 options: sources are a single folder, and only S3 has a path

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-13 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
  # A SWITCHdrive source's path is dropped: the source then reads the whole share.
  op.execute("UPDATE sources SET options = options || jsonb_build_object('path', path) WHERE backend = 's3'")
  op.drop_column("sources", "path")


def downgrade() -> None:
  op.add_column("sources", sa.Column("path", sqlmodel.sql.sqltypes.AutoString(), server_default="", nullable=False))
  op.execute("UPDATE sources SET path = COALESCE(options->>'path', ''), options = options - 'path' WHERE backend = 's3'")
