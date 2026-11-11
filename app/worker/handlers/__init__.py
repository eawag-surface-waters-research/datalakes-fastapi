"""
To add a job type:
1. Add it to `JobType` in `app/enums.py`, with an Alembic migration.
2. Add a module here with a `Handler` subclass.
3. Add the class to `HANDLERS`.
"""

from app.enums import JobType
from app.worker.handlers.base import Handler, NoParams
from app.worker.handlers.test import TestHandler

HANDLERS: list[type[Handler]] = [TestHandler]
HANDLERS_BY_TYPE: dict[JobType, Handler] = {handler.type: handler() for handler in HANDLERS}

__all__ = ["HANDLERS", "HANDLERS_BY_TYPE", "Handler", "NoParams", "TestHandler"]
