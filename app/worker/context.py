"""
A context for a job handler, providing access to the job's information and ways to report progress and log messages.
"""

import asyncio
import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlmodel.ext.asyncio.session import AsyncSession

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
    self._progress_start: float = 0.0  # the part of the job's progress that `progress` reports, see `progress_range`
    self._progress_end: float = 1.0

  async def _update(self, **values: Any) -> None:
    async with self._lock:
      if self._conn is None:
        self._conn = await self._engine.connect()
      await update_job(self._conn, self.job_id, **values)

  def session(self) -> AsyncSession:
    return AsyncSession(self._engine, expire_on_commit=False)

  async def log(self, line: str) -> None:
    """Adds a line to the job's log."""
    logger.info("Job %s: %s", self.job_id, line)
    await self._update(log=line)

  async def progress(self, fraction: float) -> None:
    """Sets how much of the job is done, from 0 to 1. Inside `progress_range`, how much of that range is done."""
    fraction = min(max(fraction, 0.0), 1.0)
    await self._update(progress=self._progress_start + fraction * (self._progress_end - self._progress_start))

  @contextmanager
  def progress_range(self, start: float, end: float) -> Generator[None]:
    """
    Within the block, `progress` reports a part of the job: 0 to 1 becomes `start` to `end`.
    Lets a handler run another handler as one step, e.g. a sync runs a fetch first.
    """
    if not 0 <= start <= end <= 1:
      raise ValueError(f"Invalid progress range {start} to {end}")

    outer = (self._progress_start, self._progress_end)  # save the outer range to restore it later
    span = outer[1] - outer[0]
    self._progress_start, self._progress_end = outer[0] + start * span, outer[0] + end * span  # set the new range

    try:
      yield  # run the block
    finally:
      self._progress_start, self._progress_end = outer  # restore the outer range

  async def close(self) -> None:
    if self._conn is not None:
      await self._conn.close()
      self._conn = None
