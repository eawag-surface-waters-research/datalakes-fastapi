import asyncio
import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import col, delete, select, update

from app.enums import FeatureType, FileStatus
from app.file_checks import FileCheck, check_file
from app.models import Dataset, DepthLayout, File, Partition, Variable
from app.partitions import WrittenPartition, partition_bounds, partitions_between, write_partition
from app.schemas.variables import VariableCommon
from app.storage import raw_dir, zarr_dir
from app.worker.context import JobContext, JobError

MAX_GROUPS = 10  # problems listed in a job's error. The rest are counted
MAX_WARNINGS = 20  # warnings written to a job's log. The rest are counted


class FailedFile(BaseModel):
  path: str
  error: str


@dataclass
class Switched:
  """What `switch_partitions` changed."""

  years_written: list[int]  # years with a partition pointed at a new store
  years_removed: list[int]  # years whose last partition was removed because it has no data any more
  version: int  # the dataset's version afterwards


@dataclass
class Checked:
  """The outcome of checking the files to build."""

  strict: bool  # whether the dataset has never been built
  checks: dict[str, FileCheck]  # by path, every checked file
  failed: list[str]  # paths of the files that failed, including the unsupported ones when strict
  ignored: list[str]  # paths of the unsupported files of a built dataset
  layouts: dict[str, int | None] = field(default_factory=dict)  # by path, the depth layout of each file that passed

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


async def check_files(context: JobContext, dataset: Dataset, variables: list[Variable], depth_variable: VariableCommon | None = None) -> Checked:
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
    checks[path] = await asyncio.to_thread(check_file, archive_path / path, variables, depth_variable)
    await context.progress(done / len(paths))

  failed = [path for path, check in checks.items() if not check.ok and (strict or not check.unsupported)]
  ignored = [path for path, check in checks.items() if not strict and check.unsupported]
  checked = Checked(strict=strict, checks=checks, failed=failed, ignored=ignored)

  await _log(context, checked)
  await _record(context, dataset.id, checked)
  return checked


async def check_layouts_dont_overlap(context: JobContext, dataset_id: int) -> None:
  """
  Fails the job if files with different depths (depth layouts) cover the same time..
  """
  async with context.session() as session:
    query = select(File.path, File.time_start, File.time_end, File.depth_layout).where(
      File.dataset_id == dataset_id, col(File.status).in_([FileStatus.new, FileStatus.ok]), col(File.depth_layout).is_not(None)
    )
    files = sorted((await session.exec(query)).all(), key=lambda file: file.time_start)

  # in time order, a file overlaps another layout if it starts before the file that ends last so far has ended
  latest = None
  for file in files:
    if latest is not None and file.time_start <= latest.time_end and file.depth_layout != latest.depth_layout:
      raise JobError(
        f"Nothing was written: {latest.path} (depth layout {latest.depth_layout}) and {file.path} (depth layout {file.depth_layout}) "
        f"have different depths but both cover {file.time_start.isoformat()} to {min(file.time_end, latest.time_end).isoformat()}. "
        "Fix the files so that only one depth layout covers each time."
      )
    if latest is None or file.time_end > latest.time_end:
      latest = file


type PartitionKey = tuple[int, int | None]  # (year, depth layout). The layout is `None` for a `timeSeries`


def partition_label(key: PartitionKey) -> str:
  year, layout = key
  return str(year) if layout is None else f"{year} (depth layout {layout})"


async def affected_partitions(context: JobContext, dataset_id: int, checked: Checked, old_files: list[File]) -> list[PartitionKey]:
  """
  The partitions to write: the years and depth layouts of the files that passed the checks, and the ones `old_files` had before
  the fetch (changed and removed files), so their old data goes away. A dataset that has never been built writes every partition.
  """
  ranges = [(check.time_start, check.time_end, checked.layouts.get(path)) for path, check in checked.checks.items() if check.ok]
  ranges += [(file.time_start, file.time_end, file.depth_layout) for file in old_files]
  keys: set[PartitionKey] = {(year, layout) for start, end, layout in ranges if start and end for year in partitions_between(start, end)}

  if checked.strict:
    # the dataset has never been built, so write every partition that has data in the archive, even if no new file covers it
    async with context.session() as session:
      keys |= set((await session.exec(select(Partition.year, Partition.layout).where(Partition.dataset_id == dataset_id))).all())

  return sorted(keys, key=lambda key: (key[0], key[1] or 0))


def store_path(key: PartitionKey, job_id: int) -> str:
  """Where a partition's store goes, relative to the dataset's `zarr_dir`: `{year}/{job_id}`, or `{year}/{layout}/{job_id}`."""
  year, layout = key
  return f"{year}/{job_id}" if layout is None else f"{year}/{layout}/{job_id}"


async def write_partitions(
  context: JobContext, dataset_id: int, feature_type: FeatureType, variables: list[Variable], keys: list[PartitionKey]
) -> list[WrittenPartition]:
  """
  Writes each partition to a new store, from every archived file that is `new` or `ok`, overlaps the year and
  has the partition's depth layout. If writing fails, the stores this job wrote are deleted and the error is raised.
  Reports progress from 0 to 1.
  """
  async with context.session() as session:
    # the files that are `new` or `ok` and have a time range
    query = select(File.path, File.time_start, File.time_end, File.depth_layout).where(
      File.dataset_id == dataset_id, col(File.status).in_([FileStatus.new, FileStatus.ok]), col(File.time_start).is_not(None)
    )
    files = list((await session.exec(query)).all())

  if keys:
    await context.log(f"Writing {len(keys)} partitions: {', '.join(map(partition_label, keys))}")

  archive = raw_dir(dataset_id)

  written: list[WrittenPartition] = []
  targets: list[Path] = []
  try:
    for done, key in enumerate(keys, start=1):
      year, layout = key
      start, end = partition_bounds(year)
      paths = [
        archive / path for path, time_start, time_end, depth_layout in files if time_start < end and time_end >= start and depth_layout == layout
      ]
      target = zarr_dir(dataset_id) / store_path(key, context.job_id)
      targets.append(target)

      # in a thread, so the event loop stays free while the files are read and the store is written
      partition = await asyncio.to_thread(write_partition, paths, variables, feature_type, year, target)
      partition.layout = layout
      written.append(partition)

      for warning in partition.warnings:
        await context.log(f"Warning: {warning}")
      if partition.rows:
        await context.log(f"{partition_label(key)}: {partition.rows} time steps from {len(paths)} files")
      else:
        await context.log(f"{partition_label(key)}: no data any more, the partition is removed")
      await context.progress(done / len(keys))
  except BaseException:
    # if writing fails, delete the stores this job wrote and raise the error
    for target in targets:
      shutil.rmtree(target, ignore_errors=True)
    raise
  return written


async def switch_partitions(context: JobContext, dataset_id: int, written: list[WrittenPartition], passed: list[str]) -> Switched:
  """
  In one transaction: points the partitions at the stores this job wrote (removing the ones without data), marks the files that
  passed the checks `ok`, and, if a partition changed, recomputes the dataset's time and depth extents from its partitions and increases its
  `version`. `built_at` is set if any data was written.
  """
  years_written: set[int] = set()
  removed_years: set[int] = set()
  changed = False  # whether a partition was written or removed
  async with context.session() as session:
    for partition in written:
      key = (partition.year, partition.layout)
      if not partition.rows:
        # no data in the partition any more, so remove it if it exists
        statement = delete(Partition).where(
          col(Partition.dataset_id) == dataset_id, col(Partition.year) == partition.year, col(Partition.layout).is_not_distinct_from(partition.layout)
        )
        if (await session.exec(statement)).rowcount:
          removed_years.add(partition.year)
          changed = True
        continue

      # insert the partition, or update it if it already exists
      values = {
        "path": store_path(key, context.job_id),
        "rows": partition.rows,
        "time_start": partition.time_start,
        "time_end": partition.time_end,
        "depth_min": partition.depth_min,
        "depth_max": partition.depth_max,
      }
      statement = insert(Partition).values(dataset_id=dataset_id, year=partition.year, layout=partition.layout, **values)
      statement = statement.on_conflict_do_update(index_elements=["dataset_id", "year", "layout"], set_={**values, "updated_at": sa.func.now()})
      await session.exec(statement)
      years_written.add(partition.year)
      changed = True

    # a year is only removed when none of its partitions (one per depth layout) is left
    if removed_years:
      remaining = select(Partition.year).where(Partition.dataset_id == dataset_id, col(Partition.year).in_(removed_years))
      removed_years -= set((await session.exec(remaining)).all())

    if passed:
      # mark the files that passed the checks `ok` and clear their error. The others stay `new` or `failed`
      await session.exec(update(File).where(col(File.dataset_id) == dataset_id, col(File.path).in_(passed)).values(status=FileStatus.ok, error=None))

    if changed:
      # the extent is recomputed from every partition, so it shrinks when data is removed, and is `null` when none is left
      extent = select(
        sa.func.min(Partition.time_start), sa.func.max(Partition.time_end), sa.func.min(Partition.depth_min), sa.func.max(Partition.depth_max)
      ).where(Partition.dataset_id == dataset_id)
      time_start, time_end, depth_min, depth_max = (await session.exec(extent)).one()
      values = {
        "time_start": time_start,
        "time_end": time_end,
        "depth_min": depth_min,
        "depth_max": depth_max,
        "version": col(Dataset.version) + 1,
      }

      if years_written:
        values["built_at"] = datetime.now(UTC)

      statement = update(Dataset).where(col(Dataset.id) == dataset_id).values(**values).returning(col(Dataset.version))
      version = (await session.exec(statement)).scalar_one()
    else:
      # no partition changed, so the version is not increased
      version = (await session.exec(select(Dataset.version).where(Dataset.id == dataset_id))).one()

    await session.commit()
  return Switched(years_written=sorted(years_written), years_removed=sorted(removed_years), version=version)


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
  """
  Updates the checked files in the index, in one transaction. A passing file with depths gets the depth layout with exactly the
  same depths, and a new layout is added for depths that haven't been seen yet.
  """
  new_layouts = []
  async with context.session() as session:
    layouts = {
      tuple(layout.depths): layout.number for layout in (await session.exec(select(DepthLayout).where(DepthLayout.dataset_id == dataset_id))).all()
    }
    for path, check in checked.checks.items():
      layout = None
      if path in checked.ignored:
        values = {"status": FileStatus.ignored, "error": file_error(check)}
      elif not check.ok:
        values = {"status": FileStatus.failed, "error": file_error(check)}
      else:
        # stays `new` until its data is written. Clears the error of a file that failed before
        values = {"status": FileStatus.new, "error": None}
        if check.depths is not None:
          layout = _find_layout(layouts, check.depths)
          if layout is None:
            layout = max(layouts.values(), default=0) + 1
            layouts[check.depths] = layout
            session.add(DepthLayout(dataset_id=dataset_id, number=layout, depths=list(check.depths)))
            new_layouts.append((layout, check.depths, path))

        checked.layouts[path] = layout
      values |= {"time_start": check.time_start, "time_end": check.time_end, "depth_layout": layout}
      await session.exec(update(File).where(col(File.dataset_id) == dataset_id, col(File.path) == path).values(**values))
    await session.commit()

  for number, depths, path in new_layouts:
    await context.log(f"New depth layout {number}: {_describe_depths(depths)}, first in {path}")


def _find_layout(layouts: dict[tuple[float, ...], int], depths: tuple[float, ...]) -> int | None:
  """The number of the layout with exactly these depths (NaN equal to NaN), or `None`."""
  if depths in layouts:
    # the depths are exactly the same as a known layout,
    return layouts[depths]

  for known, number in layouts.items():
    if len(known) == len(depths) and np.array_equal(
      known, depths, equal_nan=True
    ):  # the depths are the same as a known layout, but with NaN in different places
      return number
  return None


def _describe_depths(depths: tuple[float, ...]) -> str:
  values = np.asarray(depths)
  if not np.isfinite(values).any():
    return f"{len(depths)} depths"
  return f"{len(depths)} depths from {np.nanmin(values):g} to {np.nanmax(values):g}"
