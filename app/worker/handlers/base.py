from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, ValidationError

from app.enums import JobType
from app.errors import format_validation_errors
from app.worker.context import JobContext, JobError


class NoParams(BaseModel):
  """For job types that take no parameters."""

  model_config = ConfigDict(extra="forbid")


class Handler[ParamsModel: BaseModel](ABC):
  """
  Runs the jobs of one type.
  `type` is the job type it runs, and `params_model` is the Pydantic model that validates the job's parameters.
  """

  type: ClassVar[JobType]
  params_model: type[ParamsModel]

  @abstractmethod
  async def run(self, context: JobContext, params: ParamsModel) -> BaseModel | None:
    """
    Does the job's work and returns its `result`, or `None`.
    Raise `JobError` with a message for the user when the job fails for a reason they can act on.
    """

  def parse_params(self, params: dict[str, Any]) -> ParamsModel:
    try:
      return self.params_model.model_validate(params)
    except ValidationError as error:
      raise JobError(f"Invalid job parameters: {format_validation_errors(error.errors())}") from None

  async def __call__(self, context: JobContext, params: dict[str, Any]) -> dict[str, Any] | None:
    """Validates the params, runs the job, and returns its result as JSON."""
    result: BaseModel | None = await self.run(context, self.parse_params(params))
    return None if result is None else result.model_dump(mode="json")
