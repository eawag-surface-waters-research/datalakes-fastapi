import asyncio
import math

from pydantic import BaseModel, ConfigDict, Field

from app.enums import JobType
from app.worker.context import JobContext, JobError
from app.worker.handlers.base import Handler


class TestParams(BaseModel):
  model_config = ConfigDict(extra="forbid")

  seconds: float = Field(default=0, ge=0, description="How long the job waits.")
  fail: bool = Field(default=False, description="Make the job fail at the end.")


class TestResult(BaseModel):
  waited_seconds: float


class TestHandler(Handler[TestParams]):
  """Waits a while, reporting progress every second, to check that the worker runs jobs."""

  type = JobType.test
  params_model = TestParams

  async def run(self, context: JobContext, params: TestParams) -> TestResult:
    steps = max(1, math.ceil(params.seconds))
    await context.log(f"Waiting {params.seconds:g} s")
    for step in range(1, steps + 1):
      await asyncio.sleep(params.seconds / steps)
      await context.progress(step / steps)
    if params.fail:
      raise JobError("The test job failed, as asked by its `fail` parameter")
    await context.log("Done")
    return TestResult(waited_seconds=params.seconds)
