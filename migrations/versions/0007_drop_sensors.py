"""drop sensors: they aren't recorded or used

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-19 11:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
  op.drop_index(op.f("ix_variables_sensor_id"), table_name="variables")
  op.drop_constraint(op.f("fk_variables_sensor_id_sensors"), "variables", type_="foreignkey")
  op.drop_column("variables", "sensor_id")
  op.drop_table("sensors")


def downgrade() -> None:
  op.create_table(
    "sensors",
    sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column("manufacturer", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column("accuracy", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column("link", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column("id", sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint("id", name=op.f("pk_sensors")),
    sa.UniqueConstraint("name", name=op.f("uq_sensors_name")),
  )
  op.add_column("variables", sa.Column("sensor_id", sa.Integer(), nullable=True))
  op.create_foreign_key(op.f("fk_variables_sensor_id_sensors"), "variables", "sensors", ["sensor_id"], ["id"])
  op.create_index(op.f("ix_variables_sensor_id"), "variables", ["sensor_id"], unique=False)
