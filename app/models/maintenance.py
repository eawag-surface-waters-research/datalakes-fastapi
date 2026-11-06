from datetime import datetime

from sqlmodel import Field

from app.schemas.maintenance import CatalogueVersionBase, SyncTokenBase
from app.sql import created_at_field


class SyncToken(SyncTokenBase, table=True):
  """The token an external system uses to trigger syncs of one dataset. Only its hash is stored."""

  __tablename__ = "sync_tokens"  # type: ignore[assignment]
  dataset_id: int = Field(primary_key=True, foreign_key="datasets.id", ondelete="CASCADE")
  created_at: datetime | None = created_at_field()


class CatalogueVersion(CatalogueVersionBase, table=True):
  """A single-row counter, bumped on any metadata change and used in the cache keys of listings."""

  __tablename__ = "catalogue_version"  # type: ignore[assignment]
  id: int = Field(default=1, primary_key=True)
