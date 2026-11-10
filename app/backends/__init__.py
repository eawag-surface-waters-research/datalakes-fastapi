"""
To add a backend:
1. Add its id to `SourceBackend` in `app/enums.py`. Also make an Alembic migration that adds it to the DB's enum.
2. Add a module here with a `Backend` subclass.
3. Add the class to `BACKENDS`.
"""

from typing import Annotated, Any, Literal, Union, cast

from pydantic import Field, TypeAdapter, create_model

from app.backends.base import Backend, BackendInfo, PublicUrl, RcloneRemote, SourceInputBase
from app.backends.s3 import S3Options
from app.backends.switchdrive import SwitchdriveOptions
from app.enums import SourceBackend
from app.schemas.sources import SourceType, SourceTypeOption

BACKENDS: list[type[Backend]] = [S3Options, SwitchdriveOptions]
BACKENDS_BY_ID: dict[SourceBackend, type[Backend]] = {backend.info.id: backend for backend in BACKENDS}


def _input_model(backend: type[Backend]) -> type[SourceInputBase]:
  """A `SourceInputBase` subclass for a backend."""
  name = backend.__name__.removesuffix("Options") + "SourceInput"
  backend_field = cast(Any, (Literal[backend.info.id], ...))
  return create_model(name, __base__=SourceInputBase, backend=backend_field, options=(backend, ...))


SOURCE_INPUT_MODELS: dict[SourceBackend, type[SourceInputBase]] = {backend.info.id: _input_model(backend) for backend in BACKENDS}

SourceInput = Annotated[Union[tuple(SOURCE_INPUT_MODELS.values())], Field(discriminator="backend")]  # noqa: UP007 (built at runtime)
_source_input_adapter: TypeAdapter[SourceInputBase] = TypeAdapter(cast(Any, SourceInput))


def parse_source(data: Any) -> SourceInputBase:
  """Validates a source"""
  return _source_input_adapter.validate_python(data)


def source_type(backend: type[Backend]) -> SourceType:
  """How `GET /source-types` shows a backend."""
  options = [
    SourceTypeOption(
      name=name,
      required=field.is_required(),
      description=field.description or "",
      example=field.examples[0] if field.examples else "",
    )
    for name, field in backend.model_fields.items()
  ]
  info = backend.info
  return SourceType(backend=info.id, name=info.name, description=info.description, path_description=info.path_description, options=options)


SOURCE_TYPES: list[SourceType] = [source_type(backend) for backend in BACKENDS]

__all__ = [
  "BACKENDS",
  "BACKENDS_BY_ID",
  "SOURCE_INPUT_MODELS",
  "SOURCE_TYPES",
  "Backend",
  "BackendInfo",
  "PublicUrl",
  "RcloneRemote",
  "S3Options",
  "SourceInput",
  "SourceInputBase",
  "SwitchdriveOptions",
  "parse_source",
  "source_type",
]
