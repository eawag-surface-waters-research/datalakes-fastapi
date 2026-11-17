"""
Inserts dummy sites, collections, persons, organisations, licenses, parameters and sensors, so datasets and variables can reference them while
there are no endpoints to create them. Each table's rows are in `seed_data/<table>.json`, mostly from the old `default_data.sql`, with their IDs.

uv run python -m scripts.seed_dummy_data
"""

import asyncio
import json
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import engine
from app.models import Collection, License, Organisation, Parameter, Person, Sensor, Site, SiteType

DATA_DIR = Path(__file__).parent / "seed_data"

# In the order they can be inserted: sites need site types, collections need sites, persons need organisations
MODELS: list[type[SQLModel]] = [SiteType, Site, Collection, Organisation, Person, License, Parameter, Sensor]


def load(model: type[SQLModel]) -> list[dict]:
  return json.loads((DATA_DIR / f"{model.__tablename__}.json").read_text(encoding="utf-8"))


async def seed(session: AsyncSession) -> dict[str, int]:
  """Adds the dummy rows that are missing and returns the number of rows in each file. The caller commits."""
  counts = {}
  for model in MODELS:
    rows = load(model)
    table = model.__tablename__
    await session.exec(insert(model).values(rows).on_conflict_do_nothing())
    if "id" in model.model_fields:
      # The rows were inserted with fixed IDs, so move the sequence past them or the next insert through the API would collide
      await session.exec(
        text(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), (SELECT max(id) FROM {table}))")  # type: ignore[call-overload]
      )
    counts[table] = len(rows)
  return counts


async def main() -> None:
  async with AsyncSession(engine) as session:
    counts = await seed(session)
    await session.commit()
  await engine.dispose()
  print(f"Seeded {', '.join(f'{count} {table}' for table, count in counts.items())}")


if __name__ == "__main__":
  asyncio.run(main())
