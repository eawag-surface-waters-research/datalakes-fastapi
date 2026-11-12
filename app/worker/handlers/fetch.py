import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import col, delete, select, update

from app.backends import SourceInputBase, parse_source
from app.enums import FileStatus, JobType
from app.errors import format_validation_errors
from app.models import File, Source
from app.rclone import SourceError, copy_files, list_source
from app.schemas import SourceEntry
from app.storage import is_safe_relative_path, raw_dir
from app.worker.context import JobContext, JobError
from app.worker.handlers.base import Handler, NoParams

DEFAULT_MODIFICATION_TIME = datetime(1970, 1, 1, tzinfo=UTC)  # the modification time of a file whose source doesn't report one
PROGRESS_INTERVAL = 1.0  # min. seconds between progress updates while copying


class FetchResult(BaseModel):
  files_new: int = Field(description="Files that weren't in the archive, copied")
  files_changed: int = Field(description="Files whose size or modification time changed in the source, copied again")
  files_removed: int = Field(description="Files no longer in the source, deleted from the archive")
  files_unchanged: int = Field(description="Files already in the archive as they are in the source")
  bytes_copied: int


@dataclass
class Changes:
  """How the source differs from the file index."""

  new: list[SourceEntry]
  changed: list[SourceEntry]
  removed: list[str]
  unchanged: int

  @property
  def to_upsert(self) -> list[SourceEntry]:
    return self.new + self.changed


def compare(listing: list[SourceEntry], indexed: dict[str, File]) -> Changes:
  """Compares the source's files with the index by path, size and modification time."""
  new, changed, unchanged = [], [], 0

  for entry in listing:
    file = indexed.get(entry.path)
    if file is None:
      # a file isn't in the index yet, so it's new
      new.append(entry)
    elif file.size != entry.size or file.modified_at != (entry.modified_at or DEFAULT_MODIFICATION_TIME):
      # a file is in the index but its size or modification time changed, so it's changed
      changed.append(entry)
    else:
      # a file is in the index and its size and modification time are the same, so it's unchanged
      unchanged += 1

  listed = {entry.path for entry in listing}
  removed = sorted(path for path in indexed if path not in listed)
  return Changes(new=new, changed=changed, removed=removed, unchanged=unchanged)


def delete_from_archive(archive: Path, paths: list[str]) -> None:
  """Deletes files from the archive, and the folders they leave empty (also the parent folders)"""
  for path in paths:
    file = archive / path
    file.unlink(missing_ok=True)
    folder = file.parent
    while folder != archive and folder.is_dir() and not any(folder.iterdir()):
      folder.rmdir()
      folder = folder.parent


class FetchHandler(Handler[NoParams]):
  """
  Makes a dataset's archive match its source: copies new and changed files into `raw/{dataset_id}/`, deletes removed ones, and updates the file index.
  A file is changed when its size or modification time differs from the index.

  If the source can't be listed, or has no files while the archive has some, the job fails and nothing is removed.
  """

  type = JobType.fetch
  params_model = NoParams

  async def run(self, context: JobContext, params: NoParams) -> FetchResult:
    dataset_id = context.dataset_id
    if dataset_id is None:
      raise JobError("A fetch job needs a dataset")

    source, indexed = await self._load(context, dataset_id)

    await context.log("Listing the source")
    try:
      listing = await list_source(source)
    except SourceError as error:
      raise JobError(f"Couldn't list the source, nothing was changed: {error.error.detail}") from None

    safe: list[SourceEntry] = []
    for entry in listing:
      if is_safe_relative_path(entry.path):
        safe.append(entry)
      else:
        await context.log(f"Skipped {entry.path!r}: its path was considered unsafe")
    listing = safe

    if not listing and indexed:
      raise JobError(
        f"The source has no files, but the archive has {len(indexed)} files. This usually means the source moved or was emptied by mistake, so nothing was removed."
      )

    changes = compare(listing, indexed)
    await context.log(
      f"{len(changes.new)} new, {len(changes.changed)} changed, {len(changes.removed)} removed and {changes.unchanged} unchanged files"
    )
    await context.progress(0.05)

    archive_path = raw_dir(dataset_id)
    bytes_copied = await self._copy(context, source, changes.to_upsert, archive_path)
    await self._update_index(context, dataset_id, changes)
    delete_from_archive(archive_path, changes.removed)  # after the index, so the index never lists a file that isn't in the archive

    await context.log(f"Done: copied {len(changes.to_upsert)} files ({bytes_copied} bytes), removed {len(changes.removed)}")
    return FetchResult(
      files_new=len(changes.new),
      files_changed=len(changes.changed),
      files_removed=len(changes.removed),
      files_unchanged=changes.unchanged,
      bytes_copied=bytes_copied,
    )

  async def _load(self, context: JobContext, dataset_id: int) -> tuple[SourceInputBase, dict[str, File]]:
    """The dataset's source and its file index by path."""
    async with context.session() as session:
      stored = (await session.exec(select(Source).where(Source.dataset_id == dataset_id))).first()
      if stored is None:
        raise JobError(f"Dataset {dataset_id} has no source")

      files = (await session.exec(select(File).where(File.dataset_id == dataset_id))).all()
    try:
      source = parse_source(stored.model_dump(include={"backend", "options", "path", "include"}))
    except ValidationError as error:
      raise JobError(f"The dataset's source is no longer valid, set it again: {format_validation_errors(error.errors())}")

    return source, {file.path: file for file in files}

  async def _copy(self, context: JobContext, source: SourceInputBase, entries: list[SourceEntry], archive: Path) -> int:
    """Copies the files from the source into the archive and returns the total bytes copied."""
    if not entries:
      return 0

    file_sizes = {entry.path: entry.size for entry in entries}
    total_size = sum(file_sizes.values())
    copied_bytes = 0
    last_reported_at = time.monotonic()

    async def on_copied(path: str) -> None:
      nonlocal copied_bytes, last_reported_at
      copied_bytes += file_sizes.get(path, 0)
      if time.monotonic() - last_reported_at >= PROGRESS_INTERVAL:
        last_reported_at = time.monotonic()
        await context.progress(
          0.05 + 0.9 * copied_bytes / max(total_size, 1)
        )  # the first and last 5% of the progress are for listing and updating the index

    await context.log(f"Copying {len(entries)} files ({total_size} bytes)")
    try:
      await copy_files(source, file_sizes, archive, on_copied)
    except SourceError as error:
      raise JobError(f"Couldn't copy the files, the index wasn't changed: {error.error.detail}") from None

    await context.progress(0.95)
    return total_size

  async def _update_index(self, context: JobContext, dataset_id: int, changes: Changes) -> None:
    """
    In one transaction: adds the new files, marks the changed ones `new` with their new size and time, deletes the removed ones, and records that the source was read.
    """
    async with context.session() as session:
      upsert_rows = [
        {
          "dataset_id": dataset_id,
          "path": entry.path,
          "size": entry.size,
          "modified_at": entry.modified_at or DEFAULT_MODIFICATION_TIME,
          "status": FileStatus.new,
        }
        for entry in changes.to_upsert
      ]

      if upsert_rows:
        upsert_statement = insert(File).values(upsert_rows)
        excluded = upsert_statement.excluded
        await session.exec(  # insert the new files, and update the changed ones with their new size and modification time, marking them `new` again
          upsert_statement.on_conflict_do_update(
            index_elements=["dataset_id", "path"],
            set_={"size": excluded.size, "modified_at": excluded.modified_at, "status": FileStatus.new, "error": None},
          )
        )

      if changes.removed:
        # delete the files that are no longer in the source
        await session.exec(delete(File).where(col(File.dataset_id) == dataset_id, col(File.path).in_(changes.removed)))

      # record that the source was read
      await session.exec(update(Source).where(col(Source.dataset_id) == dataset_id).values(last_tested_at=datetime.now(UTC)))
      await session.commit()
