from app.enums import JobType
from app.worker.context import JobContext, JobError
from app.worker.handlers.base import Handler, NoParams
from app.worker.handlers.fetch import FetchHandler, FetchResult

FETCH_PROGRESS = 0.5  # the part of a sync's progress that its fetch reports. The processing reports the rest.


class SyncResult(FetchResult):
  """For now only the fetch's counts. The processing adds its own."""


class SyncHandler(Handler[NoParams]):
  """
  Updates a dataset from its source: a fetch, then processing of the new, changed and removed files.
  For now only the fetch runs: the fetched files stay `new` in the index.
  If the fetch fails, the sync fails with the fetch's error.
  """

  type = JobType.sync
  params_model = NoParams

  def __init__(self) -> None:
    self.fetch = FetchHandler()

  async def run(self, context: JobContext, params: NoParams) -> SyncResult:
    if context.dataset_id is None:
      raise JobError("A sync job needs a dataset")

    with context.progress_range(0, FETCH_PROGRESS):
      fetched = await self.fetch.run(context, params)
    await context.progress(FETCH_PROGRESS)

    await context.log("Processing isn't implemented yet: the fetched files stay `new`")
    return SyncResult(**fetched.model_dump())
