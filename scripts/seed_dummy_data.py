"""
Copies the sites, collections, persons, organisations, licenses, parameters and sensors of the old Datalakes database into the new one, with
their IDs preserved.
Lakes become sites of type `lake`, projects become collections.

The old database is read from `--source`, else `OLD_DATABASE_URL`, else a `datalakes_old` database next to the API's.
To restore a dump of the old database into the dev Postgres:
  docker exec datalakes_postgres createdb -U datalakes datalakes_old
  docker exec -i datalakes_postgres psql -U datalakes -d datalakes_old -q -v ON_ERROR_STOP=1 < path/to/datalakes_db_dump

uv run python -m scripts.seed_dummy_data [--source postgresql://user:password@host:5432/datalakes_old]
uv run python -m scripts.seed_dummy_data --export-parameters  # writes seed_data/parameters.json for scripts/inspect_netcdf.py instead
"""

import argparse
import asyncio
import json
import os
import re
import unicodedata
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import make_url, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import DATABASE_URL, engine
from app.models import Collection, License, Organisation, Parameter, Person, Sensor, Site, SiteType

PARAMETERS_FILE = Path(__file__).parent / "seed_data" / "parameters.json"

# In the order they can be inserted: sites need site types, collections need sites, persons need organisations
MODELS: list[type[SQLModel]] = [SiteType, Site, Collection, Organisation, Person, License, Parameter, Sensor]

# The old tables and the columns that are read
OLD_COLUMNS = {
  "lakes": "id, name, elevation, depth, morphology",
  "projects": "id, name, link",
  "organisations": "id, name, link",
  "persons": "id, name, organisations_id, email",
  "licenses": "id, name, description, link",
  "parameters": "id, name, cfname, unit, characteristic, description, german, french, italian",
  "sensors": "id, name, manufacturer, accuracy, link",
}

SITE_TYPES = [{"name": "lake", "description": "A body of standing water"}]

# Values that replace the old ones, by new table and ID
OVERRIDES: dict[str, dict[int, dict[str, Any]]] = {
  "collections": {2: {"link": "https://lexplore.info/", "site_id": 1}},  # LéXPLORE is on Lake Geneva
}


def default_source_url() -> str:
  url = os.getenv("OLD_DATABASE_URL")
  return url or make_url(DATABASE_URL).set(database="datalakes_old").render_as_string(hide_password=False)


def async_url(url: str) -> str:
  """Lets the source URL be a plain `postgresql://` one."""
  parsed = make_url(url)
  if parsed.drivername == "postgresql":
    parsed = parsed.set(drivername="postgresql+asyncpg")
  return parsed.render_as_string(hide_password=False)


async def read_old(conn: AsyncConnection) -> dict[str, list[dict[str, Any]]]:
  """The rows of the old tables, ordered by ID. Raises ValueError if a table is missing, e.g. when pointed at the new database."""
  missing = [table for table in OLD_COLUMNS if (await conn.execute(text("SELECT to_regclass(:table)"), {"table": table})).scalar() is None]
  if missing:
    raise ValueError(f"Not the old Datalakes database, it has no table {', '.join(missing)}")
  old = {}
  for table, columns in OLD_COLUMNS.items():
    result = await conn.execute(text(f"SELECT {columns} FROM {table} ORDER BY id"))
    old[table] = [dict(row) for row in result.mappings()]
  return old


def clean(value: Any) -> Any:
  """Tidies a text copied from the old database: Wikipedia formula markup, non-breaking spaces, repeated or trailing whitespace."""
  if isinstance(value, Decimal):
    return float(value)
  if not isinstance(value, str):
    return value
  value = re.sub(r"\{\\displaystyle (?:[^{}]|\{[^{}]*\})*\}", "", value)  # `{\displaystyle P_{0}}P_{{0}}` is the formula, then its text
  value = re.sub(r"_\{\{([^{}]*)\}\}", r"\1", value)
  value = re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()
  return value or None


def slugify(name: str) -> str:
  ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
  return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")


def one_per_name(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[int, int]]:
  """
  Names are unique in the new vocabularies, so of rows with the same name only the one with the most values is kept, or the lowest ID.
  Returns the kept rows and the IDs of the others mapped to the kept one.
  """
  kept: dict[str, dict[str, Any]] = {}
  for row in rows:
    other = kept.get(row["name"])
    if other is None or sum(v is not None for v in row.values()) > sum(v is not None for v in other.values()):
      kept[row["name"]] = row
  replaced = {row["id"]: kept[row["name"]]["id"] for row in rows if kept[row["name"]] is not row}
  return [row for row in rows if row["id"] not in replaced], replaced


def convert(old: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
  """The rows of the new tables, by table name, from the rows of the old ones."""
  old = {table: [{column: clean(value) for column, value in row.items()} for row in rows] for table, rows in old.items()}

  sites = []
  for lake in old["lakes"]:
    properties = {"elevation": lake["elevation"], "max_depth": lake["depth"], "morphology": lake["morphology"]}
    sites.append({"id": lake["id"], "name": lake["name"], "type": "lake", "properties": {k: v for k, v in properties.items() if v is not None}})

  collections, slugs = [], set()
  for project in old["projects"]:
    slug = slugify(project["name"])
    if slug in slugs:
      slug = f"{slug}-{project['id']}"
    slugs.add(slug)
    collection = {"id": project["id"], "slug": slug, "name": project["name"], "description": None, "link": project["link"], "image_url": None}
    collections.append(collection | {"site_id": None, "geometry": None, "properties": {}})

  organisations, replaced_organisations = one_per_name(old["organisations"])
  persons = [
    {
      "id": p["id"],
      "name": p["name"],
      "email": p["email"],
      "organisation_id": replaced_organisations.get(p["organisations_id"], p["organisations_id"]),
    }
    for p in old["persons"]
  ]
  parameters = [
    {"id": p["id"], "name": p["name"], "cf_name": p["cfname"], "unit": p["unit"], "characteristic": p["characteristic"]}
    | {"description": p["description"], "name_de": p["german"], "name_fr": p["french"], "name_it": p["italian"]}
    for p in one_per_name(old["parameters"])[0]
  ]
  new = {
    "site_types": [dict(row) for row in SITE_TYPES],
    "sites": sites,
    "collections": collections,
    "organisations": organisations,
    "persons": persons,
    "licenses": one_per_name(old["licenses"])[0],
    "parameters": parameters,
    "sensors": one_per_name(old["sensors"])[0],
  }
  for table, by_id in OVERRIDES.items():
    for row in new[table]:
      row.update(by_id.get(row["id"], {}))
  return new


async def seed(session: AsyncSession, new: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
  """Adds the rows that are missing and returns the number of rows of each table in `new`. The caller commits."""
  counts = {}
  for model in MODELS:
    table = model.__tablename__
    rows = new[table]
    if rows:
      await session.exec(insert(model).values(rows).on_conflict_do_nothing())
    if "id" in model.model_fields:
      # The rows were inserted with fixed IDs, so move the sequence past them or the next insert through the API would collide
      sql = f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), coalesce(max(id), 1), max(id) IS NOT NULL) FROM {table}"
      await session.exec(text(sql))  # type: ignore[call-overload]
    counts[table] = len(rows)
  return counts


def write_parameters(parameters: list[dict[str, Any]], path: Path = PARAMETERS_FILE) -> None:
  path.write_text(json.dumps(parameters, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


async def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("--source", default=default_source_url(), help="URL of the old Datalakes database")
  parser.add_argument("--export-parameters", action="store_true", help=f"write the parameters to {PARAMETERS_FILE} instead of seeding")
  args = parser.parse_args()

  source = create_async_engine(async_url(args.source), poolclass=NullPool)
  try:
    async with source.connect() as conn:
      old = await read_old(conn)
  except ValueError as error:
    parser.exit(1, f"{error}\n")
  finally:
    await source.dispose()
  new = convert(old)

  if args.export_parameters:
    write_parameters(new["parameters"])
    print(f"Wrote {len(new['parameters'])} parameters to {PARAMETERS_FILE}")
    return
  async with AsyncSession(engine) as session:
    counts = await seed(session, new)
    await session.commit()
  await engine.dispose()
  print(f"Seeded {', '.join(f'{count} {table}' for table, count in counts.items())}")


if __name__ == "__main__":
  asyncio.run(main())
