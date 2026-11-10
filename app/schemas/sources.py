import ipaddress
import os
import re
from datetime import datetime
from typing import Annotated, Any, ClassVar, Literal, LiteralString
from urllib.parse import urlsplit

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from pydantic import Field as PydanticField
from pydantic_core import PydanticCustomError
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlmodel import Field, SQLModel

from app.enums import SourceBackend
from app.errors import ErrorResponse
from app.sql import enum_type

SWITCHDRIVE_SHARE_URL_PATTERN = re.compile(r"^https://drive\.switch\.ch/index\.php/s/(?P<token>[A-Za-z0-9]+)/?$")
INTERNAL_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home")


def private_endpoints_allowed() -> bool:
  """Whether S3 endpoints may be plain HTTP and in private networks. Used for tests/development against a local S3 server."""
  return os.getenv("SOURCE_ALLOW_PRIVATE_ENDPOINTS", "false").lower() == "true"


def invalid(detail: str, error_type: LiteralString = "value_error") -> PydanticCustomError:
  """A validation error with the message as it is, without pydantic's `Value error, ` prefix."""
  return PydanticCustomError(error_type, "{detail}", {"detail": detail})


def is_internal_host(host: str) -> bool:
  """Whether a host is a non-public IP address or an internal name."""
  try:
    return not ipaddress.ip_address(host).is_global
  except ValueError:
    host = host.lower().rstrip(".")
    return host == "localhost" or host.endswith(INTERNAL_HOST_SUFFIXES) or "." not in host


class SourceBase(SQLModel):
  backend: SourceBackend = Field(sa_type=enum_type(SourceBackend, "source_backend"))
  options: dict[str, Any] = Field(sa_type=JSONB)
  path: str = Field(default="", sa_column_kwargs={"server_default": ""})
  include: list[str] = Field(default_factory=list, sa_type=ARRAY(sa.String), sa_column_kwargs={"server_default": "{}"})


class SourceOptions(BaseModel):
  """
  Options of one backend. The declared fields are the allowlist: any other option is rejected.
  """

  model_config = ConfigDict(extra="forbid")
  backend: ClassVar[SourceBackend]

  @model_validator(mode="before")
  @classmethod
  def only_allowed_options(cls, data: Any) -> Any:
    if not isinstance(data, dict):
      return data
    allowed = list(cls.model_fields)
    unknown = [f"'{name}'" for name in data if name not in allowed]
    if unknown:
      names = f"option {unknown[0]}" if len(unknown) == 1 else f"options {', '.join(unknown)}"
      raise invalid(f"Unknown {names} for backend {cls.backend}. Allowed: {', '.join(allowed)}", "unknown_option")
    return data


class S3Options(SourceOptions):
  backend = SourceBackend.s3

  bucket: str = PydanticField(pattern=r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", examples=["eawag-lexplore"])
  region: str | None = PydanticField(
    default=None, pattern=r"^[a-z0-9-]{1,32}$", description="Region of the bucket, for Amazon S3.", examples=["eu-central-1"]
  )
  endpoint: str | None = PydanticField(
    default=None,
    max_length=512,
    description="URL of an S3-compatible store. Leave it out for Amazon S3. Addresses in private or internal networks are rejected.",
    examples=["https://s3.example.org"],
  )

  @field_validator("endpoint")
  @classmethod
  def endpoint_must_be_public(cls, endpoint: str | None) -> str | None:
    if endpoint is None:
      return None
    try:
      url = urlsplit(endpoint)
      host, _ = url.hostname, url.port
    except ValueError:
      raise invalid("Not a valid URL") from None
    if url.username is not None or url.password is not None:
      raise invalid("Must not contain a user name or password. Sources are read anonymously")
    if url.query or url.fragment:
      raise invalid("Must not have a query or fragment")
    if private_endpoints_allowed():
      if url.scheme not in ("http", "https") or not host:
        raise invalid("Must be an HTTP or HTTPS URL")
      return endpoint
    if url.scheme != "https" or not host:
      raise invalid("Must be an HTTPS URL, e.g. https://s3.example.org")
    if is_internal_host(host):
      raise invalid(f"{host} is not a public address")
    return endpoint


class SwitchdriveOptions(SourceOptions):
  backend = SourceBackend.switchdrive

  share_url: str = PydanticField(
    description="The share's public link. Password-protected shares are rejected.",
    examples=["https://drive.switch.ch/index.php/s/aB3dE5fG7hJ9kL1"],
  )

  @field_validator("share_url")
  @classmethod
  def must_be_a_share_link(cls, share_url: str) -> str:
    if not SWITCHDRIVE_SHARE_URL_PATTERN.match(share_url):
      raise invalid("Must be a SWITCHdrive public link, e.g. https://drive.switch.ch/index.php/s/aB3dE5fG7hJ9kL1")
    return share_url

  @property
  def share_token(self) -> str:
    match = SWITCHDRIVE_SHARE_URL_PATTERN.match(self.share_url)
    assert match  # checked by the validator
    return match["token"]


SourcePath = Annotated[
  str,
  PydanticField(
    max_length=1024,
    description="Folder inside the bucket or share, without a leading slash. Empty for the whole bucket or share. For S3, this is the prefix.",
    examples=["thermistorchain/Level2"],
  ),
]
SourceInclude = Annotated[
  list[Annotated[str, PydanticField(min_length=1, max_length=256)]],
  PydanticField(
    max_length=50,
    description=("Which files to sync, as [rclone filter patterns](https://rclone.org/filtering/), relative to `path`. Empty for all files."),
    examples=[["*.nc"]],
  ),
]


class SourceInputBase(BaseModel):
  model_config = ConfigDict(extra="forbid")

  path: SourcePath = ""
  include: SourceInclude = []

  @field_validator("path")
  @classmethod
  def path_must_be_relative(cls, path: str) -> str:
    if path.startswith("/"):
      raise invalid("Must not start with a slash")
    if ".." in path.split("/"):
      raise invalid("Must not contain '..'")
    return path.rstrip("/")


class S3SourceInput(SourceInputBase):
  backend: Literal[SourceBackend.s3]
  options: S3Options


class SwitchdriveSourceInput(SourceInputBase):
  backend: Literal[SourceBackend.switchdrive]
  options: SwitchdriveOptions


SourceInput = Annotated[S3SourceInput | SwitchdriveSourceInput, PydanticField(discriminator="backend")]


class SourceEntry(BaseModel):
  """A file in a source."""

  path: str = PydanticField(description="Path relative to the source's `path`, with its folders.", examples=["2024/L2_ThermistorChain_202406.nc"])
  size: int = PydanticField(description="Size in bytes.")
  modified_at: datetime | None = PydanticField(description="When the file last changed in the source. `null` when the backend doesn't report it.")


class SourceTestResult(BaseModel):
  """The result of testing a source."""

  ok: bool = PydanticField(description="Whether the source could be listed anonymously.")
  error: ErrorResponse | None = PydanticField(description=("Why the source couldn't be read, or `null`."))
  files: list[SourceEntry] = PydanticField(
    description=(
      "The first files the source's `include` patterns select, up to 100, including those in subfolders. These are the files a sync would fetch."
    )
  )
  truncated: bool = PydanticField(description="`true` when there are more than 100 files.")


class SourceTypeOption(BaseModel):
  name: str
  type: Literal["string", "integer", "boolean"]
  required: bool
  description: str
  example: Any


class SourceType(BaseModel):
  """A backend a source can use."""

  backend: SourceBackend
  name: str
  description: str
  path_description: str = Field(description="What `path` means for this backend.")
  options: list[SourceTypeOption] = Field(description="The options accepted in a source's `options`.")
