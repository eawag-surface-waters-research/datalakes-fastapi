import shutil
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.enums import JobType
from app.models import Dataset
from app.storage import raw_dir
from app.worker.context import JobContext, JobError
from app.worker.handlers.base import Handler


class CleanupParams(BaseModel):
  model_config = ConfigDict(extra="forbid")

  dataset_id: int = Field(ge=1, description="The deleted dataset whose files are deleted.")


class CleanupResult(BaseModel):
  files_deleted: int = Field(description="Number of files deleted from the dataset's archive")


def delete_folder(folder: Path) -> int:
  """Deletes the folder and everything in it. Returns how many files it held (0 if it didn't exist)"""
  if not folder.exists():
    return 0

  files = sum(1 for path in folder.rglob("*") if not path.is_dir())
  shutil.rmtree(folder)
  return files


class CleanupHandler(Handler[CleanupParams]):
  """
  Deletes the local files of a deleted dataset: its archive in `raw/{dataset_id}/`.
  """

  type = JobType.cleanup
  params_model = CleanupParams

  async def run(self, context: JobContext, params: CleanupParams) -> CleanupResult:
    async with context.session() as session:
      if await session.get(Dataset, params.dataset_id) is not None:
        # a new dataset was created with the same ID before this job ran: its files must not be deleted
        raise JobError(f"Dataset {params.dataset_id} exists, so its files weren't deleted")

    folder = raw_dir(params.dataset_id)

    await context.log(f"Deleting {folder}")
    files = delete_folder(folder)
    await context.log(f"Deleted {files} files")

    return CleanupResult(files_deleted=files)
