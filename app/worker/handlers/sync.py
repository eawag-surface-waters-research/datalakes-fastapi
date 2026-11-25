from pydantic import Field

from app.enums import JobType
from app.partitions import SUPPORTED_FEATURE_TYPES, PartitionError, depth_variable
from app.worker.build import (
  FailedFile,
  affected_partitions,
  check_files,
  check_layouts_dont_overlap,
  load_variables,
  switch_partitions,
  write_partitions,
)
from app.worker.context import JobContext, JobError
from app.worker.handlers.base import Handler, NoParams
from app.worker.handlers.fetch import FetchHandler, FetchResult

FETCH_PROGRESS = 0.5  # the part of a sync's progress that its fetch reports
CHECK_PROGRESS = 0.6  # the end of the part the file checks report. Writing the data reports the rest.


class SyncResult(FetchResult):
  files_checked: int = Field(description="Files checked against the dataset's variables")
  files_ignored: int = Field(description="Files that aren't NetCDF, left out of the data")
  files_failed: list[FailedFile] = Field(description="Files that failed the checks, with their problems")
  years_written: list[int] = Field(default_factory=list, description="Years with a partition written to a new store")
  years_removed: list[int] = Field(default_factory=list, description="Years removed because none of their partitions has data any more")
  version: int = Field(description="The dataset's version after the job. It goes up when a partition is written or removed")


class SyncHandler(Handler[NoParams]):
  """
  Updates a dataset from its source: a fetch, then processing of the new, changed and removed files.
  The data is written to Zarr, one store per year (and per depth layout for a `timeSeriesProfile`), and only the partitions that the
  new, changed and removed files touch are written again.
  Switching to the new stores also recomputes the dataset's time and depth extents and increases its `version`.
  If the fetch fails, the sync fails with the fetch's error. If a file fails the checks, the sync fails with every file's problems,
  after the files that passed were written, unless the dataset has never been built.
  """

  type = JobType.sync
  params_model = NoParams

  def __init__(self) -> None:
    self.fetcher = FetchHandler()

  async def run(self, context: JobContext, params: NoParams) -> SyncResult:
    if context.dataset_id is None:
      raise JobError("A sync job needs a dataset")

    # 1. Fetch: make the archive match the source. New and changed files are copied and marked `new` in the index, removed files are deleted from the archive and the index
    with context.progress_range(0, FETCH_PROGRESS):
      fetched = await self.fetcher.fetch(context)
    await context.progress(FETCH_PROGRESS)

    # 2. Load the dataset's variables (the schema of its files). The new, changed and removed files, with their old time ranges, come from `fetched`.
    dataset, variables = await load_variables(context, context.dataset_id)
    feature_type = dataset.feature_type
    if feature_type is None:
      raise JobError(f"Dataset {context.dataset_id} has no feature_type")
    if feature_type not in SUPPORTED_FEATURE_TYPES:
      raise JobError(f"{feature_type} datasets can't be built yet")

    try:
      depth = depth_variable(variables, feature_type)  # a timeSeriesProfile's, whose values decide each file's depth layout
    except PartitionError as error:
      raise JobError(f"Dataset {context.dataset_id}'s variables don't fit a {feature_type}: {error}") from None

    # 3. Check the files to build against the variables and record the outcome in the file index:
    #  - Not built yet (strict): every archived file is checked, and one failing or unsupported file fails the job before anything is written.
    #  - Built: only the `new` files are checked. A failing file is marked `failed` and skipped, a file in another format is marked `ignored`, the others go on.
    with context.progress_range(FETCH_PROGRESS, CHECK_PROGRESS):
      checked = await check_files(context, dataset, variables, depth)
    result = SyncResult(
      **fetched.result.model_dump(),
      files_checked=len(checked.checks),
      files_ignored=len(checked.ignored),
      files_failed=checked.failed_files,
      version=dataset.version,
    )
    if checked.strict and checked.failed:
      raise JobError(checked.error(), result=result)
    try:
      await check_layouts_dont_overlap(context, context.dataset_id)
    except JobError as error:
      raise JobError(str(error), result=result) from None

    # 4. Find the affected Zarr partitions (year, and depth layout for a timeSeriesProfile): those of each new file, the old and new ones of a changed file, and the old ones of a removed file
    keys = await affected_partitions(context, context.dataset_id, checked, [*fetched.changed, *fetched.removed])

    # 5. Write those partitions as new stores, from all archived files in their time range. If one fails, nothing is switched.
    with context.progress_range(CHECK_PROGRESS, 1):
      try:
        written = await write_partitions(context, context.dataset_id, feature_type, variables, keys)
      except PartitionError as error:
        raise JobError(f"Nothing was written: {error}", result=result) from None

    # 6. In one transaction: switch to the new stores, mark the written files `ok`, recompute the time and depth extents and increase the `version`.
    switched = await switch_partitions(context, context.dataset_id, written, checked.passed)
    result.years_written, result.years_removed, result.version = switched.years_written, switched.years_removed, switched.version
    if switched.version != dataset.version:
      await context.log(f"Switched to the new data, the dataset's version is now {switched.version}")

    # 7. If a file failed, fail the job with the grouped problems, with the result of what was written
    if checked.failed:
      raise JobError(checked.error(), result=result)
    return result
