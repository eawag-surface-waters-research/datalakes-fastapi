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

    # 1. Fetch: make the archive match the source. New and changed files are copied and marked `new` in the index, removed files are deleted from the archive and the index
    with context.progress_range(0, FETCH_PROGRESS):
      fetched = await self.fetch.run(context, params)
    await context.progress(FETCH_PROGRESS)

    # TODO: 2. Load the dataset's variables (the schema of its files) and the files marked `new`. The removed ones come from the fetch.

    # TODO: 3. Check each new or changed file against the variables: NetCDF, every `source_name` with the declared dimensions, matching units, CF time units.
    # Collect every problem of a file, then handle:
    #  - Not built yet (strict): one failing or unsupported file fails the job, marks the failing files `failed` and writes nothing.
    #  - Built: a failing file is marked `failed` and skipped, a file in another format is marked `ignored`, the others go on.

    # TODO: 4. Find the affected Zarr partitions: the time range of each new file, the old and new range of a changed file, and the old range of a removed file, so its data goes away.

    # TODO: 5. Write those partitions as new versions, from all archived files in their time range.

    # TODO: 6. In one transaction: switch to the new partition versions, record each file's time range and status, recompute the
    # dataset's extents  and increase its `version` if anything changed.

    # TODO: 7. Return the fetch's counts with the processing's. If a file failed, fail the job with the grouped problems. Maybe also send mail/alert
    await context.log("Processing isn't implemented yet: the fetched files stay `new`")
    return SyncResult(**fetched.model_dump())
