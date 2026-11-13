import os
import random
import socket
import uuid
from collections.abc import AsyncGenerator
from urllib.parse import urlsplit

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

import app.models
from app.api import app
from app.database import DATABASE_URL, engine, get_session
from app.worker import Worker

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


@pytest.fixture
async def worker_engine() -> AsyncGenerator[AsyncEngine]:
  """
  The worker uses several connections at once, so these tests can't run inside one rolled-back transaction like the API tests.
  Each test gets its own random committed schema instead, dropped at the end.
  """
  schema = f"test_worker_{uuid.uuid4().hex[:12]}"
  async with engine.begin() as conn:
    await conn.execute(sa.text(f"CREATE SCHEMA {schema}"))
  test_engine = create_async_engine(DATABASE_URL, poolclass=NullPool, connect_args={"server_settings": {"search_path": schema}})

  try:
    async with test_engine.begin() as conn:
      await conn.run_sync(SQLModel.metadata.create_all)
    yield test_engine
  finally:
    await test_engine.dispose()
    async with engine.begin() as conn:
      await conn.execute(sa.text(f"DROP SCHEMA {schema} CASCADE"))


@pytest.fixture(autouse=True)
def lock_class(monkeypatch) -> int:
  """Advisory locks are shared by the whole database, so each test uses its own, to never clash with a worker running on the dev database."""
  value = random.randint(1000, 2**31 - 1)
  monkeypatch.setattr("app.worker.queue.JOB_LOCK_CLASS", value)
  return value


@pytest.fixture(autouse=True)
def no_private_endpoints(monkeypatch) -> None:
  monkeypatch.setenv("SOURCE_ALLOW_PRIVATE_ENDPOINTS", "false")


@pytest.fixture
async def make_worker(worker_engine):
  workers: list[Worker] = []

  def make(**options) -> Worker:
    worker = Worker(worker_engine, poll_interval=0.05, **options)
    workers.append(worker)
    return worker

  yield make
  for worker in workers:
    worker.stop()


TEST_S3_ENDPOINT = os.getenv("TEST_S3_ENDPOINT", "http://localhost:8333")


@pytest.fixture
def s3_endpoint(monkeypatch) -> str:
  """The local test S3 server. It's in a private network, so private endpoints are allowed for these tests."""
  url = urlsplit(TEST_S3_ENDPOINT)
  try:
    socket.create_connection((url.hostname, url.port), timeout=1).close()
  except OSError:
    pytest.skip(f"The test S3 server isn't running at {TEST_S3_ENDPOINT}")
  monkeypatch.setenv("SOURCE_ALLOW_PRIVATE_ENDPOINTS", "true")
  return TEST_S3_ENDPOINT
