import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlmodel import SQLModel

import app.models  # noqa: F401  (registers the tables)
from app.database import engine


@pytest.fixture(scope="session")
def anyio_backend():
  return "asyncio"


def test_single_migration_head():
  script = ScriptDirectory.from_config(Config("alembic.ini"))
  assert len(script.get_heads()) == 1


@pytest.mark.anyio
async def test_migrations_match_models():
  """Runs all migrations on an empty Postgres schema and checks that they produce exactly the schema of the models."""

  def upgrade_and_compare(sync_conn):
    config = Config("alembic.ini")
    config.attributes["connection"] = sync_conn
    command.upgrade(config, "head")
    context = MigrationContext.configure(sync_conn, opts={"compare_type": True, "compare_server_default": True})
    return compare_metadata(context, SQLModel.metadata)

  async with engine.connect() as conn:
    transaction = await conn.begin()
    await conn.execute(sa.text("CREATE SCHEMA test_migrations"))
    await conn.execute(sa.text("SET LOCAL search_path TO test_migrations"))
    diff = await conn.run_sync(upgrade_and_compare)
    await transaction.rollback()
  assert diff == []
