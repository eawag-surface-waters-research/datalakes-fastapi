"""
Queues a test job that waits a while, to check that the worker works.

uv run python -m scripts.queue_test_job --seconds 30 [--fail]
"""

import argparse
import asyncio

from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import engine
from app.enums import JobType
from app.worker import enqueue
from app.worker.handlers.test import TestParams


async def queue_job(params: TestParams) -> None:
  async with AsyncSession(engine, expire_on_commit=False) as session:
    job = await enqueue(session, JobType.test, params=params.model_dump())
  await engine.dispose()
  print(f"Queued test job {job.id}. Follow it at /jobs/{job.id}")


if __name__ == "__main__":
  parser = argparse.ArgumentParser(prog="python -m scripts.queue_test_job", description=__doc__.splitlines()[0])
  parser.add_argument("--seconds", type=float, default=30, help="How long the job waits.")
  parser.add_argument("--fail", action="store_true", help="Make the job fail at the end.")
  args = parser.parse_args()
  asyncio.run(queue_job(TestParams(seconds=args.seconds, fail=args.fail)))
