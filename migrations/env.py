import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

import app.models  # noqa: F401  (registers the tables)
from app.database import DATABASE_URL

# this is the Alembic Config object, which provides access to the values within the .ini file in use.
config = context.config

if config.config_file_name is not None:
  fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def do_run_migrations(connection: Connection) -> None:
  context.configure(connection=connection, target_metadata=target_metadata, compare_type=True, compare_server_default=True)

  with context.begin_transaction():
    context.run_migrations()


async def run_async_migrations() -> None:
  connectable = create_async_engine(DATABASE_URL, poolclass=pool.NullPool)

  async with connectable.connect() as connection:
    await connection.run_sync(do_run_migrations)

  await connectable.dispose()


def run_migrations_online() -> None:
  """Run migrations in 'online' mode."""

  connection = config.attributes.get("connection")
  if connection is not None:
    # a caller (the tests) passed an open connection
    do_run_migrations(connection)
  else:
    asyncio.run(run_async_migrations())


run_migrations_online()
