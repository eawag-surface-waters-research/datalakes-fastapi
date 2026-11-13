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
  return connection_string(source.options.rclone_remote(source.path))


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


def entry_from_item(item: dict) -> SourceEntry:
  """A file from an item of `rclone lsjson`."""
  modified = item.get("ModTime")
  return SourceEntry(path=item["Path"], size=item["Size"], modified_at=datetime.fromisoformat(modified) if modified else None)


def parse_entry(line: str) -> SourceEntry | None:
  """A file from a line of `rclone lsjson`, which prints one item per line"""
  line = line.strip().removesuffix(",")
  if not line.startswith("{"):
    return None
  return entry_from_item(json.loads(line))


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
    return ErrorResponse(code="source_not_found", detail=source.options.not_found_detail(source.path))
  if any(text in stderr for text in NOT_PUBLIC_MESSAGES):
    return ErrorResponse(code="source_not_public", detail=source.options.not_public_detail())
  if any(text in stderr for text in UNREACHABLE_MESSAGES):
    return ErrorResponse(code="source_unreachable", detail=f"Couldn't connect to the source: {last}")
  return ErrorResponse(code="source_error", detail=f"Couldn't list the source: {last}")


def rclone_error(stderr: str, source: SourceInputBase) -> ErrorResponse:
  if invalid_filter := INVALID_FILTER_PATTERN.search(stderr):
    return ErrorResponse(code="validation_error", detail=f"{source.backend}.include: {invalid_filter['reason']}")
  return classify_error(stderr, source)


def include_flags(source: SourceInputBase) -> list[str]:
  return [f"--include={pattern}" for pattern in source.include]


async def probe_source(source: SourceInputBase) -> SourceTestResult:
  """
  Lists the files of a source anonymously with `rclone lsjson`, including subfolders, and returns the first ones.
  rclone applies the `include` patterns itself, so only the files a sync would fetch are listed.
  Stops rclone as soon as it has listed one entry more than are returned, and kills it after `TEST_TIMEOUT` seconds.
  """
  try:
    await check_urls_resolve_publicly(source)
  except SourceError as error:
    raise ApiError(422, error.error.code, error.error.detail) from None
  async with _test_slots:
    process = await asyncio.create_subprocess_exec(
      *rclone_command("lsjson", "--recursive", "--files-only", *include_flags(source), remote=build_remote(source)),
      stdin=asyncio.subprocess.DEVNULL,
      stdout=asyncio.subprocess.PIPE,
      stderr=asyncio.subprocess.PIPE,
      env=rclone_env(),
      limit=1024 * 1024,
    )

    assert process.stdout and process.stderr  # to satisfy type checker
    stderr = asyncio.create_task(process.stderr.read())
    entries: list[SourceEntry] = []
    truncated = False

    try:
      async with asyncio.timeout(TEST_TIMEOUT):
        while line := await process.stdout.readline():
          entry = parse_entry(line.decode())
          if entry is None:
            continue
          if len(entries) == TEST_MAX_ENTRIES:
            truncated = True
            break
          entries.append(entry)
        if truncated:
          process.kill()
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

  if process.returncode != 0 and not truncated:
    error = rclone_error(error_output, source)
    if error.code == "validation_error":
      raise ApiError(422, error.code, error.detail)
    logger.info("Source test failed: %s", error_output.strip())
    return SourceTestResult(ok=False, error=error, files=[], truncated=False)
  entries.sort(key=lambda entry: entry.path)
  return SourceTestResult(ok=True, error=None, files=entries, truncated=truncated)


async def list_source(source: SourceInputBase, timeout: float = LIST_TIMEOUT) -> list[SourceEntry]:
  """
  Lists every file of a source that its `include` patterns select, including subfolders.
  Uses the server's modification times.
  Raises `SourceError` if the source can't be listed within `timeout` seconds, or its config is invalid.
  """
  await check_urls_resolve_publicly(source)
  command = rclone_command("lsjson", "--recursive", "--files-only", "--use-server-modtime", *include_flags(source), remote=build_remote(source))
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

  return [entry_from_item(item) for item in json.loads(stdout)]


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
