from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel
from sqlmodel import col, select, update

from app.enums import FileStatus
from app.file_checks import FileCheck, check_file
from app.models import Dataset, File, Variable
from app.storage import raw_dir
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
    checks[path] = check_file(archive_path / path, variables)
    await context.progress(done / len(paths))

  failed = [path for path, check in checks.items() if not check.ok and (strict or not check.unsupported)]
  ignored = [path for path, check in checks.items() if not strict and check.unsupported]
  checked = Checked(strict=strict, checks=checks, failed=failed, ignored=ignored)

  await _log(context, checked)
  await _record(context, dataset.id, checked)
  return checked


async def mark_built(context: JobContext, dataset_id: int) -> None:
  async with context.session() as session:
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
