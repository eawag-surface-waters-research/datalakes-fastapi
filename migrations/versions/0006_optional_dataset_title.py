"""a dataset's title is optional

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-19 10:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
  op.alter_column("datasets", "title", nullable=True)


def downgrade() -> None:
  op.execute("UPDATE datasets SET title = 'Untitled' WHERE title IS NULL")
  op.alter_column("datasets", "title", nullable=False)
