import asyncio
import shutil
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import col, delete, select, update

from app.enums import FileStatus
from app.file_checks import FileCheck, check_file
from app.models import Dataset, File, Partition, Variable
from app.partitions import WrittenPartition, partition_bounds, partitions_between, write_partition
from app.storage import raw_dir, zarr_dir
from app.worker.context import JobContext, JobError

MAX_GROUPS = 10  # problems listed in a job's error. The rest are counted
MAX_WARNINGS = 20  # warnings written to a job's log. The rest are counted


class FailedFile(BaseModel):
  path: str
  error: str


@dataclass
class Checked:
  """The outcome of checking the files to build."""

  strict: bool  # whether the dataset has never been built
  checks: dict[str, FileCheck]  # by path, every checked file
  failed: list[str]  # paths of the files that failed, including the unsupported ones when strict
  ignored: list[str]  # paths of the unsupported files of a built dataset

  @property
  def passed(self) -> list[str]:
    return [path for path, check in self.checks.items() if check.ok]

  @property
  def failed_files(self) -> list[FailedFile]:
    return [FailedFile(path=path, error=file_error(self.checks[path])) for path in self.failed]

  def error(self) -> str:
    """The job's error: the failed files' problems, the same problem in several files grouped."""
    total = len(self.checks)
    if self.strict:
      header = f"Nothing was built: {len(self.failed)} of {total} files failed the checks"
    else:
      header = f"{len(self.failed)} of {total} new files failed the checks and were skipped"
    return "\n".join([header, *describe_problems([self.checks[path] for path in self.failed], total)])


async def load_variables(context: JobContext, dataset_id: int) -> tuple[Dataset, list[Variable]]:
  """The dataset and its variables. Fails the job if the dataset has no variables, as there is nothing to check its files against."""
  async with context.session() as session:
    dataset = await session.get(Dataset, dataset_id)
    if dataset is None:
      raise JobError(f"Dataset {dataset_id} no longer exists")
    variables = list((await session.exec(select(Variable).where(Variable.dataset_id == dataset_id).order_by(col(Variable.id)))).all())
  if not variables:
    raise JobError(f"Dataset {dataset_id} has no variables. Fetch, inspect and create the variables, then rebuild it.")
  return dataset, variables


async def check_files(context: JobContext, dataset: Dataset, variables: list[Variable]) -> Checked:
  """
  Checks the files to build and records the outcome in the file index: a failing file is marked `failed` with its problems, a file
  that isn't NetCDF `ignored` (built datasets only), and a passing file stays `new` with the time range it covers.
  Reports progress from 0 to 1.
  """
  assert dataset.id is not None

  # If this dataset has never been built, check every archived file. Otherwise only the new files, as the others have already been checked and built
  strict = dataset.built_at is None

  async with context.session() as session:
    query = select(File.path).where(File.dataset_id == dataset.id)
    if not strict:
      # only check all files in the first build, later only the new files
      query = query.where(File.status == FileStatus.new)
    paths = list((await session.exec(query.order_by(col(File.path)))).all())

  await context.log(f"Checking {len(paths)} {'archived' if strict else 'new'} files against {len(variables)} variables")
  archive_path = raw_dir(dataset.id)

  checks: dict[str, FileCheck] = {}
  for done, path in enumerate(paths, start=1):
    # in a thread, so the event loop stays free and the worker sees cancel requests and timeouts while a big file is checked
    checks[path] = await asyncio.to_thread(check_file, archive_path / path, variables)
    await context.progress(done / len(paths))

  failed = [path for path, check in checks.items() if not check.ok and (strict or not check.unsupported)]
  ignored = [path for path, check in checks.items() if not strict and check.unsupported]
  checked = Checked(strict=strict, checks=checks, failed=failed, ignored=ignored)

  await _log(context, checked)
  await _record(context, dataset.id, checked)
  return checked


async def affected_years(context: JobContext, dataset_id: int, checked: Checked, old_files: list[File]) -> list[int]:
  """
  The partitions to write: the years the files that passed the checks cover, and the years `old_files` covered before the fetch
  (changed and removed files). A dataset that has never been built writes every partition.
  """
  ranges = [(check.time_start, check.time_end) for check in checked.checks.values() if check.ok]
  ranges += [(file.time_start, file.time_end) for file in old_files]
  years = {year for start, end in ranges if start and end for year in partitions_between(start, end)}

  if checked.strict:
    # the dataset has never been built, so write every partition that has data in the archive, even if no new file covers it
    async with context.session() as session:
      years |= set((await session.exec(select(Partition.year).where(Partition.dataset_id == dataset_id))).all())

  return sorted(years)


async def write_partitions(context: JobContext, dataset_id: int, variables: list[Variable], years: list[int]) -> list[WrittenPartition]:
  """
  Writes each year to a new store, `{year}/{job_id}/` in the dataset's `zarr_dir`, from every archived file that is `new` or `ok`
  and overlaps the year. If writing fails, the stores this job wrote are deleted and the error is raised.
  Reports progress from 0 to 1.
  """
  async with context.session() as session:
    # the files that are `new` or `ok` and have a time range, sorted by path so the last file's values are kept when a time step is in several files
    query = select(File.path, File.time_start, File.time_end).where(
      File.dataset_id == dataset_id, col(File.status).in_([FileStatus.new, FileStatus.ok]), col(File.time_start).is_not(None)
    )
    files = list((await session.exec(query)).all())

  if years:
    await context.log(f"Writing {len(years)} partitions: {', '.join(map(str, years))}")

  archive = raw_dir(dataset_id)

  written: list[WrittenPartition] = []
  targets: list[Path] = []
  try:
    for done, year in enumerate(years, start=1):
      start, end = partition_bounds(year)
      paths = [archive / path for path, time_start, time_end in files if time_start < end and time_end >= start]
      target = zarr_dir(dataset_id) / str(year) / str(context.job_id)
      targets.append(target)

      # in a thread, so the event loop stays free while the files are read and the store is written
      partition = await asyncio.to_thread(write_partition, paths, variables, year, target)
      written.append(partition)

      for warning in partition.warnings:
        await context.log(f"Warning: {warning}")
      if partition.rows:
        await context.log(f"{year}: {partition.rows} time steps from {len(paths)} files")
      else:
        await context.log(f"{year}: no data any more, the partition is removed")
      await context.progress(done / len(years))
  except BaseException:
    # if writing fails, delete the stores this job wrote and raise the error
    for target in targets:
      shutil.rmtree(target, ignore_errors=True)
    raise
  return written


async def switch_partitions(context: JobContext, dataset_id: int, written: list[WrittenPartition], passed: list[str]) -> None:
  """
  In one transaction: points the partitions at the stores this job wrote (removing the ones without data), marks the files that
  passed the checks `ok`, and sets the dataset's `built_at` if any data was written.
  """
  async with context.session() as session:
    for partition in written:
      if not partition.rows:
        # no data in the year, so remove the partition if it exists
        await session.exec(delete(Partition).where(col(Partition.dataset_id) == dataset_id, col(Partition.year) == partition.year))
        continue

      values = {
        "path": f"{partition.year}/{context.job_id}",
        "rows": partition.rows,
        "time_start": partition.time_start,
        "time_end": partition.time_end,
      }

      statement = insert(Partition).values(dataset_id=dataset_id, year=partition.year, **values)
      # if the partition already exists, update it with the new values and set `updated_at` to now. The `dataset_id` and `year` are the unique constraint.
      statement = statement.on_conflict_do_update(index_elements=["dataset_id", "year"], set_={**values, "updated_at": sa.func.now()})

      await session.exec(statement)

    if passed:
      # mark the files that passed the checks `ok` and clear their error. The others stay `new` or `failed`
      await session.exec(update(File).where(col(File.dataset_id) == dataset_id, col(File.path).in_(passed)).values(status=FileStatus.ok, error=None))

    if any(partition.rows for partition in written):
      # set the dataset's `built_at` to now
      await session.exec(update(Dataset).where(col(Dataset.id) == dataset_id).values(built_at=datetime.now(UTC)))
    await session.commit()


def file_error(check: FileCheck) -> str:
  """A failed file's `error` in the index: its problems, one per line."""
  return "\n".join(problem.detail for problem in check.problems)


def describe_problems(checks: list[FileCheck], total: int) -> list[str]:
  """
  One line per problem, with the number of files that have it, most common first, e.g.
  `- 37 of 40 files: no variable 'oxygen' (e.g. L2_202410.nc)`.
  """
  files_by_problem: dict[str, list[str]] = defaultdict(list)
  for check in checks:
    for problem in check.problems:
      files_by_problem[problem.detail].append(problem.file)

  groups = sorted(files_by_problem.items(), key=lambda group: -len(group[1]))  # stable, so ties keep the order they were found in
  lines = [f"- {len(files)} of {total} files: {detail} ({'e.g. ' if len(files) > 1 else ''}{files[0]})" for detail, files in groups[:MAX_GROUPS]]
  if len(groups) > MAX_GROUPS:
    lines.append(f"- and {len(groups) - MAX_GROUPS} other problems, see the file index")
  return lines


async def _log(context: JobContext, checked: Checked) -> None:
  warnings = [warning for check in checked.checks.values() for warning in check.warnings]
  for warning in warnings[:MAX_WARNINGS]:
    await context.log(f"Warning: {warning}")
  if len(warnings) > MAX_WARNINGS:
    await context.log(f"… and {len(warnings) - MAX_WARNINGS} more warnings")

  if checked.ignored:
    await context.log(f"Ignored {len(checked.ignored)} files that aren't NetCDF, e.g. {checked.ignored[0]}")
  await context.log(f"{len(checked.passed)} files passed the checks, {len(checked.failed)} failed")


async def _record(context: JobContext, dataset_id: int, checked: Checked) -> None:
  """Updates the checked files in the index, in one transaction."""
  async with context.session() as session:
    for path, check in checked.checks.items():
      if path in checked.ignored:
        values = {"status": FileStatus.ignored, "error": file_error(check)}
      elif not check.ok:
        values = {"status": FileStatus.failed, "error": file_error(check)}
      else:
        # stays `new` until its data is written. Clears the error of a file that failed before
        values = {"status": FileStatus.new, "error": None}
      values |= {"time_start": check.time_start, "time_end": check.time_end}
      await session.exec(update(File).where(col(File.dataset_id) == dataset_id, col(File.path) == path).values(**values))
    await session.commit()
