from sqlmodel import Field

from app.schemas.sites import SiteBase


class Site(SiteBase, table=True):
  """A place where data is measured"""

  __tablename__ = "sites"  # type: ignore[assignment]
  id: int | None = Field(default=None, primary_key=True)
  # Bounding box of `geometry`, kept in sync by the API for `?bbox=` filters
  lon_min: float | None = None
  lat_min: float | None = None
  lon_max: float | None = None
  lat_max: float | None = None
