from collections.abc import AsyncGenerator

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

import app.models
from app.database import engine, get_session
from app.main import app

"""Each test will run in a transaction that is rolled back at the end of the test, so the database is empty for each test."""


@pytest.fixture(scope="session")
def anyio_backend():
  return "asyncio"


@pytest.fixture
async def session() -> AsyncGenerator[AsyncSession]:
  """A session on an empty copy of the schema that is thrown away after the test"""
  async with engine.connect() as conn:
    transaction = await conn.begin()
    await conn.execute(sa.text("CREATE SCHEMA test_api"))
    await conn.execute(sa.text("SET LOCAL search_path TO test_api"))
    await conn.run_sync(SQLModel.metadata.create_all, checkfirst=False)

    async with AsyncSession(conn, join_transaction_mode="create_savepoint", expire_on_commit=False) as session:
      yield session
    await transaction.rollback()


@pytest.fixture
async def client(session: AsyncSession) -> AsyncGenerator[AsyncClient]:
  app.dependency_overrides[get_session] = lambda: session
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
    yield client
  app.dependency_overrides.clear()
