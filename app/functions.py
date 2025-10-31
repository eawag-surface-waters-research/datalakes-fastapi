from sqlmodel import SQLModel
from datetime import datetime

def convert_string_datetimes(obj: SQLModel, *keys: str) -> None:
    for key in keys:
        value = getattr(obj, key, None)
        if isinstance(value, str):
            try:
                setattr(obj, key, datetime.fromisoformat(value))
            except (ValueError, AttributeError):
                pass