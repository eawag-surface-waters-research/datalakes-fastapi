from app.enums import JobType
from app.worker.context import JobContext, JobError
from app.worker.handlers.base import Handler, NoParams


class SyncHandler(Handler[NoParams]):
  """
  Updates a dataset from its source: a fetch, then processing of the new, changed and removed files.
  """

  type = JobType.sync
  params_model = NoParams

  async def run(self, context: JobContext, params: NoParams) -> None:
    if context.dataset_id is None:
      raise JobError("A sync job needs a dataset")
    await context.log("Nothing was fetched or processed: syncing isn't implemented yet")
