from sqlmodel import Field, SQLModel


class SyncTokenBase(SQLModel):
  token_hash: str


class CatalogueVersionBase(SQLModel):
  version: int = Field(default=1, sa_column_kwargs={"server_default": "1"})
