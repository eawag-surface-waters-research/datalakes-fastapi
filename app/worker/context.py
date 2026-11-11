"""
A context for a job handler, providing access to the job's information and ways to report progress and log messages.
"""

import asyncio
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.enums import JobType
from app.models import Job
from app.worker.queue import update_job

logger = logging.getLogger(__name__)


class JobError(Exception):
  """A job failed for a reason its user can act on. The message becomes the job's `error`, so write it for them."""


class JobContext:
  def __init__(self, engine: AsyncEngine, job: Job):
    assert job.id is not None
    self.job_id: int = job.id
    self.type: JobType = job.type
    self.dataset_id: int | None = job.dataset_id
    self._engine = engine
    self._conn: AsyncConnection | None = None
    self._lock = asyncio.Lock()  # one statement at a time on the connection, even if the handler logs from parallel tasks

  async def _update(self, **values: Any) -> None:
    async with self._lock:
      if self._conn is None:
        self._conn = await self._engine.connect()
      await update_job(self._conn, self.job_id, **values)

  async def log(self, line: str) -> None:
    """Adds a line to the job's log."""
    logger.info("Job %s: %s", self.job_id, line)
    await self._update(log=line)

  async def progress(self, fraction: float) -> None:
    """Sets how much of the job is done, from 0 to 1."""
    await self._update(progress=min(max(fraction, 0.0), 1.0))

  async def close(self) -> None:
    if self._conn is not None:
      await self._conn.close()
      self._conn = None
