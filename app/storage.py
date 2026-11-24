import os
from pathlib import Path


def filesystem_root() -> Path:
  return Path(os.getenv("FILESYSTEM", "filesystem")).resolve()


def raw_dir(dataset_id: int) -> Path:
  """The dataset's archive."""
  return filesystem_root() / "raw" / str(dataset_id)


def zarr_dir(dataset_id: int) -> Path:
  """The dataset's Zarr stores, one folder per partition (year) with one store per job that wrote it: `{year}/{job_id}/`."""
  return filesystem_root() / "zarr" / str(dataset_id)


def is_safe_relative_path(path: str) -> bool:
  """
  Whether a path from a source stays inside the folder it's copied into.
  S3 keys can be anything, e.g. `../../etc/passwd`, so they're checked before they're used as local paths.
  """
  return "\\" not in path and all(part not in ("", ".", "..") for part in path.split("/"))
