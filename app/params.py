import re
from datetime import UTC, date, datetime, time
from typing import Annotated

from fastapi import Query

from app.errors import ApiError

"""
Query parameters shared by several endpoints
"""

Start = Annotated[
  str | None,
  Query(
    description="Start of the time range, inclusive. A date (`2024-03-01`, meaning midnight UTC) or a full ISO 8601 timestamp.",
    examples=["2024-03-01", "2024-03-01T00:00:00Z"],
  ),
]
End = Annotated[
  str | None,
  Query(
    description="End of the time range, inclusive. A date (`2024-03-31`, meaning the whole day in UTC) or a full ISO 8601 timestamp. Must not be before `start`.",
    examples=["2024-03-31", "2024-03-31T23:59:59Z"],
  ),
]
Bbox = Annotated[
  str | None,
  Query(
    description="Only results inside this box, given as `min_lon,min_lat,max_lon,max_lat` in WGS 84 degrees.",
    pattern=r"^-?[0-9]+(\.[0-9]+)?(,-?[0-9]+(\.[0-9]+)?){3}$",
    examples=["6.1,46.2,7.0,46.6"],
  ),
]
Expand = Annotated[
  bool,
  Query(
    description="Return each referenced object next to its ID, e.g. the whole site in `site` next to `site_id`. "
    "With `false`, only the IDs are returned.",
  ),
]

DATE: re.Pattern[str] = re.compile(r"\d{4}-\d{2}-\d{2}")


def parse_time(value: str, name: str, *, end: bool = False) -> datetime:
  """A date is midnight UTC, or for `end` the last moment of that day. A timestamp without a time zone is UTC."""
  try:
    if DATE.fullmatch(value):
      return datetime.combine(date.fromisoformat(value), time.max if end else time.min, UTC)
    parsed = datetime.fromisoformat(value)
  except ValueError:
    raise ApiError(422, "validation_error", f"{name}: Input should be a date (2024-03-01) or an ISO 8601 timestamp (2024-03-01T00:00:00Z)") from None
  return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_time_range(start: str | None, end: str | None) -> tuple[datetime | None, datetime | None]:
  start_time = parse_time(start, "start") if start else None
  end_time = parse_time(end, "end", end=True) if end else None
  if start_time and end_time and start_time > end_time:
    raise ApiError(400, "invalid_time_range", f"start ({start}) is after end ({end})")
  return start_time, end_time


def parse_bbox(value: str) -> tuple[float, float, float, float]:
  min_lon, min_lat, max_lon, max_lat = (float(part) for part in value.split(","))
  if not (-180 <= min_lon <= max_lon <= 180 and -90 <= min_lat <= max_lat <= 90):
    raise ApiError(
      400, "invalid_bbox", f"bbox ({value}) must be min_lon,min_lat,max_lon,max_lat, with longitudes within +-180 and latitudes within +-90"
    )
  return min_lon, min_lat, max_lon, max_lat
