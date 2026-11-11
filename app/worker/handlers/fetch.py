from app.enums import JobType
from app.worker.context import JobContext, JobError
from app.worker.handlers.base import Handler, NoParams


class FetchHandler(Handler[NoParams]):
  """
  Makes a dataset's archive match its source: downloads new and changed files, drops removed ones, and updates the file index.

  TODO: implement logic
  """

  type = JobType.fetch
  params_model = NoParams

  async def run(self, context: JobContext, params: NoParams) -> None:
    if context.dataset_id is None:
      raise JobError("A fetch job needs a dataset")
    await context.log(f"Fetching dataset {context.dataset_id}")
    raise JobError("Fetching isn't implemented yet")
