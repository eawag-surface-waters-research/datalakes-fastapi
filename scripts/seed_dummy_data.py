"""Inserts dummy sites, persons, organisations and licenses, so datasets can reference them while there are no endpoints to create them.

The rows come from the old `db/default_data.sql` (the `lakes` table became `sites`). Safe to run more than once: rows that exist are left alone.

    uv run python -m scripts.seed_dummy_data
"""

import asyncio

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import engine
from app.models import License, Organisation, Person, Site, SiteType

SITE_TYPES = [{"name": "lake", "description": "A body of standing water"}]

# The old `lakes` table had `elevation`, `depth` and `morphology` columns; they go into the site's properties
SITES = [{"id": 1, "name": "Lake Geneva", "type": "lake", "properties": {"elevation": 372.0, "max_depth": 309.0, "morphology": True}}]

ORGANISATIONS = [
  {"id": 1, "name": "Eawag", "link": "https://www.eawag.ch/"},
  {"id": 2, "name": "EPFL", "link": "https://www.epfl.ch/en/"},
]

PERSONS = [{"id": 1, "name": "James Runnalls", "email": "james.runnalls@eawag.ch", "organisation_id": 1}]

LICENSES = [
  {
    "id": 1,
    "name": "GNU AGPLv3",
    "description": (
      "Permissions of this strongest copyleft license are conditioned on making available complete source code of licensed works and "
      "modifications, which include larger works using a licensed work, under the same license. Copyright and license notices must be "
      "preserved. Contributors provide an express grant of patent rights. When a modified version is used to provide a service over a "
      "network, the complete source code of the modified version must be made available."
    ),
    "link": "https://choosealicense.com/licenses/agpl-3.0/",
  }
]

# In the order they can be inserted: persons need organisations, sites need site types
TABLES = [(SiteType, SITE_TYPES), (Site, SITES), (Organisation, ORGANISATIONS), (Person, PERSONS), (License, LICENSES)]


async def seed(session: AsyncSession) -> None:
  """Adds the dummy rows that are missing. The caller commits."""
  for model, rows in TABLES:
    await session.exec(insert(model).values(rows).on_conflict_do_nothing())  # type: ignore[call-overload]
    table = model.__tablename__
    if "id" in model.model_fields:
      # The rows were inserted with fixed IDs, so move the sequence past them or the next insert through the API would collide
      await session.exec(
        text(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), (SELECT max(id) FROM {table}))")  # type: ignore[call-overload]
      )


async def main() -> None:
  async with AsyncSession(engine) as session:
    await seed(session)
    await session.commit()
  await engine.dispose()
  print(f"Seeded {', '.join(f'{len(rows)} {model.__tablename__}' for model, rows in TABLES)}")


if __name__ == "__main__":
  asyncio.run(main())
