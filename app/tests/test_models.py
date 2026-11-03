import pytest
import sqlalchemy as sa
from sqlmodel import SQLModel

import app.models  # noqa: F401  (registers the tables)
from app.database import engine


@pytest.fixture(scope="session")
def anyio_backend():
  return "asyncio"


@pytest.mark.anyio
async def test_models_create_in_postgres():
  """Creates every table in an empty Postgres schema, inside a transaction that is rolled back."""
  async with engine.connect() as conn:
    transaction = await conn.begin()
    await conn.execute(sa.text("CREATE SCHEMA test_models"))
    await conn.execute(sa.text("SET LOCAL search_path TO test_models"))
    await conn.run_sync(SQLModel.metadata.create_all, checkfirst=False)
    tables = await conn.run_sync(lambda sync_conn: sa.inspect(sync_conn).get_table_names(schema="test_models"))
    await transaction.rollback()
  assert set(tables) == set(SQLModel.metadata.tables)
