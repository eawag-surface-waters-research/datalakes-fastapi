from sqlmodel import Field, SQLModel

from app.schemas.collections import CollectionBase


class Collection(CollectionBase, table=True):
  """A group of datasets"""

  __tablename__ = "collections"  # type: ignore[assignment]
  id: int | None = Field(default=None, primary_key=True)


class CollectionDataset(SQLModel, table=True):
  __tablename__ = "collection_datasets"  # type: ignore[assignment]
  collection_id: int = Field(primary_key=True, foreign_key="collections.id", ondelete="CASCADE")
  dataset_id: int = Field(primary_key=True, foreign_key="datasets.id", ondelete="CASCADE", index=True)
