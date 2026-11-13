from pydantic import Field, field_validator

from app.backends.base import Backend, BackendInfo, PublicUrl, RcloneRemote
from app.enums import SourceBackend
from app.errors import invalid


class S3Options(Backend):
  """Options for an S3 source. Only these are accepted."""

  info = BackendInfo(
    id=SourceBackend.s3,
    name="S3 bucket",
    description=(
      "A folder in a public bucket on Amazon S3 or an S3-compatible store. The bucket must allow anonymous reads, "
      "and the folder must hold the files directly, without subfolders."
    ),
  )

  bucket: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", description="Name of the bucket.", examples=["eawag-lexplore"])
  path: str = Field(
    default="",
    max_length=1024,
    description=(
      "Folder inside the bucket (the prefix), without a leading slash. Empty for the top level of the bucket. "
      "It must hold the files directly, without subfolders."
    ),
    examples=["thermistorchain/Level2"],
  )
  region: str | None = Field(
    default=None, pattern=r"^[a-z0-9-]{1,32}$", description="Region of the bucket, for Amazon S3.", examples=["eu-central-1"]
  )
  endpoint: PublicUrl | None = Field(
    default=None,
    description="URL of an S3-compatible store. Leave it out for Amazon S3. Must be a public HTTPS address.",
    examples=["https://s3.example.org"],
  )

  @field_validator("path")
  @classmethod
  def path_must_be_relative(cls, path: str) -> str:
    if path.startswith("/"):
      raise invalid("Must not start with a slash")
    if ".." in path.split("/"):
      raise invalid("Must not contain '..'")
    return path.rstrip("/")

  def rclone_remote(self) -> RcloneRemote:
    # env_auth=false: never read credentials from the environment.
    params = {"provider": "Other" if self.endpoint else "AWS", "env_auth": "false"}
    if self.region:
      params["region"] = self.region
    if self.endpoint:
      params["endpoint"] = self.endpoint
    return RcloneRemote(type="s3", params=params, root=f"{self.bucket}/{self.path}".rstrip("/"))

  def not_found_detail(self) -> str:
    return f"Bucket {self.bucket} does not exist"

  def not_public_detail(self) -> str:
    return f"Access denied to bucket {self.bucket}. Sources are read anonymously, so the bucket must allow public reads."

  def has_folders_hint(self) -> str:
    return "Set `path` to the folder that holds the files directly, without subfolders."
