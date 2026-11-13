import asyncio
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import tempfile
from collections.abc import Awaitable, Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from app.backends.base import RcloneRemote, SourceInputBase, private_endpoints_allowed
from app.errors import ApiError, ErrorResponse
from app.schemas.sources import SourceEntry, SourceTestResult

logger = logging.getLogger(__name__)

TEST_TIMEOUT = 10.0  # seconds a source test may take in total
TEST_MAX_ENTRIES = 100  # files returned by a source test
LIST_TIMEOUT = 300.0  # seconds a full listing (for a fetch) may take
FOLDERS_SHOWN = 5  # subfolders named in messages

MAX_CONCURRENT_TESTS = 4  # max. concurrent source tests, to avoid overloading the server
_test_slots = asyncio.Semaphore(MAX_CONCURRENT_TESTS)


class SourceError(Exception):
  def __init__(self, error: ErrorResponse):
    super().__init__(error.detail)
    self.error = error


def quote(value: str) -> str:
  return "'" + value.replace("'", "''") + "'"


def connection_string(remote: RcloneRemote) -> str:
  """
  An rclone connection string, e.g. `:s3,provider='AWS',env_auth='false',region='eu-central-1':bucket/prefix`.
  """
  if not re.fullmatch(r"[a-z0-9]+", remote.type) or not all(re.fullmatch(r"[a-z0-9_]+", name) for name in remote.params):
    raise ValueError(f"Invalid rclone backend or parameter name in {remote}")
  return f":{remote.type}," + ",".join(f"{name}={quote(value)}" for name, value in remote.params.items()) + f":{remote.root}"


def build_remote(source: SourceInputBase) -> str:
  """The rclone connection string that reads a source."""
  return connection_string(source.options.rclone_remote())


def rclone_command(command: str, *flags: str, remote: str, destination: str | None = None) -> list[str]:
  """
  An rclone command line that ignores any config file, with short timeouts and no low-level retries.
  `destination` is a local path, for commands that copy.
  """
  rclone = shutil.which("rclone")
  if rclone is None:
    raise RuntimeError("rclone is not installed")
  arguments = [remote] if destination is None else [remote, destination]
  return [rclone, command, *flags, "--config", "", "--contimeout", "5s", "--timeout", "10s", "--low-level-retries", "1", "--", *arguments]


def rclone_env() -> dict[str, str]:
  """A minimal environment, so rclone can't pick up credentials or settings from the host."""
  return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/nonexistent"}


async def resolve(host: str) -> list[str]:
  """The IP addresses of a host name, or none if it doesn't resolve."""
  try:
    addresses = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
  except socket.gaierror:
    return []
  return [str(sockaddr[0]) for *_, sockaddr in addresses]


async def check_urls_resolve_publicly(source: SourceInputBase) -> None:
  """
  Rejects a source with a `PublicUrl` option whose host name resolves to a private or internal address, with a `SourceError`.
  """
  if private_endpoints_allowed():
    return
  for name, url in source.options.public_urls().items():
    host = urlsplit(url).hostname
    assert host  # checked by the validator
    for address in await resolve(host):
      if not ipaddress.ip_address(address).is_global:
        raise SourceError(ErrorResponse(code="validation_error", detail=f"{source.backend}.options.{name}: {host} is not a public address"))


@dataclass
class SourceListing:
  """
  What's directly in a source's folder: the files that its `include` patterns select, and every subfolder.
  Sources must not have subfolders, and the files in subfolders are never listed.
  """

  files: list[SourceEntry] = field(default_factory=list)
  folders: list[str] = field(default_factory=list)  # names of the first `FOLDERS_SHOWN` subfolders
  folder_count: int = 0

  def add(self, item: dict, max_files: int | None = None) -> bool:
    """Adds an item of `rclone lsjson`. Returns `False` if it's a file that wasn't added because there are already `max_files`."""
    if item.get("IsDir"):
      self.folder_count += 1
      if len(self.folders) < FOLDERS_SHOWN:
        self.folders.append(item["Path"])
      return True

    if max_files is not None and len(self.files) >= max_files:
      return False

    modified = item.get("ModTime")
    self.files.append(SourceEntry(path=item["Path"], size=item["Size"], modified_at=datetime.fromisoformat(modified) if modified else None))
    return True

  def describe_folders(self) -> str:
    """The subfolders for a message, e.g. `a subfolder (2024/)` or `7 subfolders (2020/, 2021/, 2022/, 2023/, 2024/ and 2 more)`."""
    names = ", ".join(f"{name}/" for name in self.folders)

    more = self.folder_count - len(self.folders)
    if more:
      names += f" and {more} more"

    return f"a subfolder ({names})" if self.folder_count == 1 else f"{self.folder_count} subfolders ({names})"


def parse_item(line: str) -> dict | None:
  """An item from a line of `rclone lsjson`, which prints one item per line"""
  line = line.strip().removesuffix(",")
  if not line.startswith("{"):
    return None
  return json.loads(line)


def has_folders_error(listing: SourceListing, source: SourceInputBase) -> ErrorResponse:
  detail = f"The source has {listing.describe_folders()}, but sources must be a single folder. {source.options.has_folders_hint()}"
  return ErrorResponse(code="source_has_folders", detail=detail)


ERROR_LINE_PREFIX_PATTERN = re.compile(r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2} (?:[A-Z]+\s*: )?")
INVALID_FILTER_PATTERN = re.compile(r'(?:Failed to load filters|failed to reload "filter" options): (?P<reason>.+)')


# What rclone prints for each kind of failure, whatever the backend
NOT_FOUND_MESSAGES = ("directory not found", "NoSuchBucket")
NOT_PUBLIC_MESSAGES = ("AccessDenied", "status code: 403", "401 Unauthorized", "403 Forbidden")
UNREACHABLE_MESSAGES = ("send request failed", "no such host", "connection refused", "dial tcp", "x509:", "tls:")


def classify_error(stderr: str, source: SourceInputBase) -> ErrorResponse:
  """Turns rclone's error output into an error a user can act on."""
  lines = [ERROR_LINE_PREFIX_PATTERN.sub("", line).strip() for line in stderr.splitlines() if line.strip()]
  last = lines[-1] if lines else "rclone failed without a message"

  if any(text in stderr for text in NOT_FOUND_MESSAGES):
    return ErrorResponse(code="source_not_found", detail=source.options.not_found_detail())
  if any(text in stderr for text in NOT_PUBLIC_MESSAGES):
    return ErrorResponse(code="source_not_public", detail=source.options.not_public_detail())
  if any(text in stderr for text in UNREACHABLE_MESSAGES):
    return ErrorResponse(code="source_unreachable", detail=f"Couldn't connect to the source: {last}")
  return ErrorResponse(code="source_error", detail=f"Couldn't list the source: {last}")


def rclone_error(stderr: str, source: SourceInputBase) -> ErrorResponse:
  if invalid_filter := INVALID_FILTER_PATTERN.search(stderr):
    return ErrorResponse(code="validation_error", detail=f"{source.backend}.include: {invalid_filter['reason']}")
  return classify_error(stderr, source)


def filter_flags(source: SourceInputBase) -> list[str]:
  """
  rclone filter rules that select the files matching the `include` patterns, and every folder.
  `--include` would hide folders whenever no pattern could match in them (e.g. `/*.nc`), but subfolders must always be listed to find them.
  """
  if not source.include:
    return []
  return ["--filter=+ */", *(f"--filter=+ {pattern}" for pattern in source.include), "--filter=- **"]


def lsjson_command(source: SourceInputBase, *flags: str) -> list[str]:
  return rclone_command("lsjson", *flags, *filter_flags(source), remote=build_remote(source))


async def probe_source(source: SourceInputBase) -> SourceTestResult:
  """
  Lists the files of a source anonymously with `rclone lsjson`, and returns the first ones.
  rclone applies the `include` patterns itself, so only the files a sync would fetch are listed.
  A source with subfolders fails with `source_has_folders`, but its files are still returned.
  Kills rclone after `TEST_TIMEOUT` seconds.
  """
  try:
    await check_urls_resolve_publicly(source)
  except SourceError as error:
    raise ApiError(422, error.error.code, error.error.detail) from None
  async with _test_slots:
    process = await asyncio.create_subprocess_exec(
      *lsjson_command(source),
      stdin=asyncio.subprocess.DEVNULL,
      stdout=asyncio.subprocess.PIPE,
      stderr=asyncio.subprocess.PIPE,
      env=rclone_env(),
      limit=1024 * 1024,
    )

    assert process.stdout and process.stderr  # to satisfy type checker
    stderr = asyncio.create_task(process.stderr.read())
    listing = SourceListing()
    truncated = False

    try:
      async with asyncio.timeout(TEST_TIMEOUT):
        # read every line, because a subfolder can come after the first files. rclone lists the whole folder before it prints anything anyway.
        while line := await process.stdout.readline():
          item = parse_item(line.decode())
          if item is not None and not listing.add(item, max_files=TEST_MAX_ENTRIES):
            truncated = True
        await process.wait()
        error_output = (await stderr).decode(errors="replace")
    except TimeoutError:
      detail = f"The source couldn't be listed within {TEST_TIMEOUT:g} s. A folder with very many files (about 100,000) takes longer to list."
      return SourceTestResult(ok=False, error=ErrorResponse(code="source_timeout", detail=detail), files=[], truncated=False)
    finally:
      if process.returncode is None:
        process.kill()
        await process.wait()
      stderr.cancel()
      with suppress(asyncio.CancelledError):
        await stderr

  if process.returncode != 0:
    error = rclone_error(error_output, source)
    if error.code == "validation_error":
      raise ApiError(422, error.code, error.detail)
    logger.info("Source test failed: %s", error_output.strip())
    return SourceTestResult(ok=False, error=error, files=[], truncated=False)

  listing.files.sort(key=lambda entry: entry.path)
  error = has_folders_error(listing, source) if listing.folder_count else None
  return SourceTestResult(ok=error is None, error=error, files=listing.files, truncated=truncated)


async def list_source(source: SourceInputBase, timeout: float = LIST_TIMEOUT) -> SourceListing:
  """
  Lists every file directly in the source's folder that its `include` patterns select, and its subfolders, but not the files in them.
  Uses the server's modification times.
  Raises `SourceError` if the source can't be listed within `timeout` seconds, or its config is invalid.
  """
  await check_urls_resolve_publicly(source)
  command = lsjson_command(source, "--use-server-modtime")
  process = await asyncio.create_subprocess_exec(
    *command, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=rclone_env()
  )
  try:
    async with asyncio.timeout(timeout):
      stdout, stderr = await process.communicate()
  except TimeoutError:
    raise SourceError(ErrorResponse(code="source_timeout", detail=f"The source couldn't be listed within {timeout:g} s"))
  finally:
    if process.returncode is None:  # timed out or job was cancelled
      process.kill()
      await process.wait()

  if process.returncode != 0:
    raise SourceError(rclone_error(stderr.decode(errors="replace"), source))

  listing = SourceListing()
  for item in json.loads(stdout):
    listing.add(item)
  return listing


async def copy_files(source: SourceInputBase, paths: Iterable[str], destination: Path, on_copied: Callable[[str], Awaitable[None]]) -> None:
  """
  Copies files of a source into a local folder, keeping their paths, and calls `on_copied` with each path once it's copied.
  A file that's already in `destination` with the same size and modification time isn't copied again.
  """
  await check_urls_resolve_publicly(source)

  destination.mkdir(parents=True, exist_ok=True)
  with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8") as files_from_file:
    files_from_file.write(
      "".join(f"{path}\n" for path in paths)
    )  # rclone's `--files-from` reads a file with one path per line, relative to the remote's root
    files_from_file.flush()

    command = rclone_command(
      "copy",
      "--files-from-raw",
      files_from_file.name,
      "--no-traverse",  # only look at the listed files in the destination
      "--use-server-modtime",
      "--use-json-log",
      "--verbose",  # logs each copied file
      "--stats",  # disables the stats summary at the end
      "0",
      remote=build_remote(source),
      destination=str(destination),
    )
    process = await asyncio.create_subprocess_exec(
      *command,
      stdin=asyncio.subprocess.DEVNULL,
      stdout=asyncio.subprocess.DEVNULL,
      stderr=asyncio.subprocess.PIPE,
      env=rclone_env(),
      limit=1024 * 1024,
    )

    assert process.stderr
    errors: list[str] = []
    try:
      while line := await process.stderr.readline():
        try:
          entry = json.loads(line)
        except ValueError:
          errors.append(line.decode(errors="replace"))
          continue

        if str(entry.get("msg", "")).startswith("Copied") and entry.get("object"):
          # report successful copies to the caller
          await on_copied(entry["object"])
        elif entry.get("level") in ("error", "critical"):
          # take errors from the structured json log instead of the unstructured stderr lines
          errors.append(f"{entry.get('object', '')}: {entry.get('msg', '')}".strip(": "))

      await process.wait()
    finally:
      if process.returncode is None:  # the job was cancelled, or `on_copied` failed
        process.kill()
        await process.wait()

  if process.returncode != 0:
    raise SourceError(rclone_error("\n".join(errors) or "rclone failed without a message", source))
