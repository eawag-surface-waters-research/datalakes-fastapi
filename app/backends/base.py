import ipaddress
import os
from abc import abstractmethod
from dataclasses import dataclass
from typing import Annotated, Any, ClassVar
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, GetCoreSchemaHandler, model_validator
from pydantic_core import core_schema

from app.enums import SourceBackend
from app.errors import invalid

INTERNAL_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home")


def private_endpoints_allowed() -> bool:
  """Whether `PublicUrl`s may be plain HTTP and in private networks. Only for tests and development against a local server."""
  return os.getenv("SOURCE_ALLOW_PRIVATE_ENDPOINTS", "false").lower() == "true"


def is_internal_host(host: str) -> bool:
  """Whether a host is a non-public IP address or an internal name."""
  try:
    return not ipaddress.ip_address(host).is_global
  except ValueError:
    host = host.lower().rstrip(".")
    return host == "localhost" or host.endswith(INTERNAL_HOST_SUFFIXES) or "." not in host


class PublicUrl(str):
  """
  A URL that a user gives and rclone connects to.
  It must be HTTPS, without a user name, password, query or fragment, and its host must not be in a private or internal network.
  """

  @classmethod
  def __get_pydantic_core_schema__(cls, source: Any, handler: GetCoreSchemaHandler) -> core_schema.CoreSchema:
    # validate max. length and then call `validate`
    return core_schema.no_info_after_validator_function(cls.validate, core_schema.str_schema(max_length=512))

  @classmethod
  def validate(cls, value: str) -> PublicUrl:
    try:
      url = urlsplit(value)
      host, _ = url.hostname, url.port  # the port raises ValueError if it isn't a number
    except ValueError:
      raise invalid("Not a valid URL") from None
    if url.username is not None or url.password is not None:
      raise invalid("Must not contain a user name or password. Sources are read anonymously")
    if url.query or url.fragment:
      raise invalid("Must not have a query or fragment")
    if private_endpoints_allowed():
      if url.scheme not in ("http", "https") or not host:
        raise invalid("Must be an HTTP or HTTPS URL")
      return cls(value)
    if url.scheme != "https" or not host:
      raise invalid("Must be an HTTPS URL, e.g. https://s3.example.org")
    if is_internal_host(host):
      raise invalid(f"{host} is not a public address")
    return cls(value)


@dataclass(frozen=True)
class BackendInfo:
  """How a backend is shown in `GET /source-types`."""

  id: SourceBackend
  name: str
  description: str


@dataclass(frozen=True)
class RcloneRemote:
  """An rclone remote built on the fly"""

  type: str  # an rclone backend, e.g. `s3` or `webdav`
  params: dict[str, str]
  root: str


class Backend(BaseModel):
  """
  A source backend, defined by its options. The fields are the only options a source of this backend accepts, anything else is rejected.
  """

  model_config = ConfigDict(extra="forbid")
  info: ClassVar[BackendInfo]

  @model_validator(mode="before")
  @classmethod
  def only_allowed_options(cls, data: Any) -> Any:
    if not isinstance(data, dict):
      return data
    allowed = list(cls.model_fields)
    unknown = [f"'{name}'" for name in data if name not in allowed]
    if unknown:
      names = f"option {unknown[0]}" if len(unknown) == 1 else f"options {', '.join(unknown)}"
      raise invalid(f"Unknown {names} for backend {cls.info.id}. Allowed: {', '.join(allowed)}", "unknown_option")
    return data

  @abstractmethod
  def rclone_remote(self) -> RcloneRemote:
    """The rclone remote that reads this source. Its root is the folder that holds the files."""

  def not_found_detail(self) -> str:
    """The message when the source, or the folder in it, doesn't exist."""
    return "The source's folder does not exist"

  def has_folders_hint(self) -> str:
    """What to do when the source has subfolders."""
    return "Share a folder that holds the files directly, without subfolders."

  def not_public_detail(self) -> str:
    """The message when the source can't be read anonymously."""
    return "The source can't be read anonymously. Sources are read without credentials, so it must be publicly readable."

  def public_urls(self) -> dict[str, PublicUrl]:
    """The options that are URLs rclone connects to, by option name."""
    return {name: value for name in type(self).model_fields if isinstance(value := getattr(self, name), PublicUrl)}


SourceInclude = Annotated[
  list[Annotated[str, Field(min_length=1, max_length=256)]],
  Field(
    max_length=50,
    description=(
      "Which files to sync, as [rclone filter patterns](https://rclone.org/filtering/), relative to the source's folder. Empty for all files."
    ),
    examples=[["*.nc"]],
  ),
]


class SourceInputBase(BaseModel):
  """A source as sent to set or test one."""

  model_config = ConfigDict(extra="forbid")

  backend: SourceBackend
  options: Backend
  include: SourceInclude = []
