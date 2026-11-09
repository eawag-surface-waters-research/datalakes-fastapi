from collections.abc import Sequence
from dataclasses import dataclass

from sqlmodel import SQLModel, col, select

from app.database import SessionDep
from app.errors import ApiError

"""
Helpers for endpoints that return objects with references to other tables with the `expand` query parameter.
"""


@dataclass(frozen=True)
class Reference:
  name: str  # used in error messages, e.g. "Site"
  model: type[SQLModel]  # the table it points to
  read_model: type[SQLModel]  # how the row is returned when expanded


def expanded_name(field: str) -> str:
  """`site_id` is expanded into `site`"""
  return field.removesuffix("_id")


async def check_references(session: SessionDep, data: SQLModel, references: dict[str, Reference]) -> None:
  """Rejects data that points to a row that doesn't exist, with 422 `unknown_reference`."""
  for field, reference in references.items():
    reference_id = getattr(data, field)
    if reference_id is not None and await session.get(reference.model, reference_id) is None:
      raise ApiError(422, "unknown_reference", f"{reference.name} {reference_id} not found")


async def expand_references(session: SessionDep, items: Sequence[SQLModel], references: dict[str, Reference]) -> None:
  """
  Sets the referenced object next to each reference of each item, or `None` if the reference is empty.
  Uses one query per reference field.
  """
  for field, reference in references.items():
    ids = {getattr(item, field) for item in items} - {None}  # get all unique IDs, ignoring `None`
    rows = []
    if ids:
      query = select(reference.model).where(col(reference.model.id).in_(ids))  # type: ignore[attr-defined]
      rows = (await session.exec(query)).all()
    by_id = {row.id: reference.read_model.model_validate(row) for row in rows}  # type: ignore[attr-defined]
    for item in items:
      setattr(item, expanded_name(field), by_id.get(getattr(item, field)))  # set the expanded reference on each item
