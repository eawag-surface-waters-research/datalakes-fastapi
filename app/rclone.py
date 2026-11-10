import asyncio
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
from contextlib import suppress
from datetime import datetime
from urllib.parse import urlsplit

from app.errors import ApiError, ErrorResponse
from app.schemas.sources import S3SourceInput, SourceEntry, SourceTestResult, SwitchdriveSourceInput, private_endpoints_allowed

logger = logging.getLogger(__name__)

SWITCHDRIVE_WEBDAV_URL = "https://drive.switch.ch/public.php/webdav/"

TEST_TIMEOUT = 10.0  # seconds a source test may take in total
TEST_MAX_ENTRIES = 100  # files returned by a source test

MAX_CONCURRENT_TESTS = 4  # max. concurrent source tests, to avoid overloading the server
_test_slots = asyncio.Semaphore(MAX_CONCURRENT_TESTS)


def quote(value: str) -> str:
  return "'" + value.replace("'", "''") + "'"


def build_remote(source: S3SourceInput | SwitchdriveSourceInput) -> str:
  """
  The rclone connection string of a source, e.g. `:s3,provider='AWS',env_auth='false',region='eu-central-1':bucket/prefix`.

  Every value is quoted, so a value can't add options of its own.
  """
  if isinstance(source, S3SourceInput):
    options = source.options
    params = {"provider": "Other" if options.endpoint else "AWS", "env_auth": "false"}
    if options.region:
      params["region"] = options.region
    if options.endpoint:
      params["endpoint"] = options.endpoint
    backend, root = "s3", f"{options.bucket}/{source.path}".rstrip("/")
  elif isinstance(source, SwitchdriveSourceInput):
    params = {"url": SWITCHDRIVE_WEBDAV_URL, "vendor": "owncloud", "user": source.options.share_token}
    backend, root = "webdav", source.path
  else:
    raise TypeError(f"Unknown source type {type(source)}")
  return f":{backend}," + ",".join(f"{name}={quote(value)}" for name, value in params.items()) + f":{root}"


def rclone_command(command: str, *flags: str, remote: str) -> list[str]:
  """
  An rclone command line that ignores any config file, with short timeouts and no retries
  """
  rclone = shutil.which("rclone")
  if rclone is None:
    raise RuntimeError("rclone is not installed")
  return [rclone, command, *flags, "--config", "", "--contimeout", "5s", "--timeout", "10s", "--low-level-retries", "1", "--", remote]


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


async def check_endpoint_resolves_publicly(source: S3SourceInput | SwitchdriveSourceInput) -> None:
  """
  Rejects an S3 endpoint whose host name resolves to a private or internal address.
  """
  if not isinstance(source, S3SourceInput) or not source.options.endpoint or private_endpoints_allowed():
    return
  host = urlsplit(source.options.endpoint).hostname
  assert host  # checked by the validator
  for address in await resolve(host):
    if not ipaddress.ip_address(address).is_global:
      raise ApiError(422, "validation_error", f"{source.backend}.options.endpoint: {host} is not a public address")


def parse_entry(line: str) -> SourceEntry | None:
  line = line.strip().removesuffix(",")
  if not line.startswith("{"):
    return None
  item = json.loads(line)
  modified = item.get("ModTime")
  return SourceEntry(path=item["Path"], size=item["Size"], modified_at=datetime.fromisoformat(modified) if modified else None)


ERROR_LINE_PREFIX_PATTERN = re.compile(r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2} (?:[A-Z]+\s*: )?")
INVALID_FILTER_PATTERN = re.compile(r"Failed to load filters: (?P<reason>.+)")


def classify_error(stderr: str, source: S3SourceInput | SwitchdriveSourceInput) -> ErrorResponse:
  """Turns rclone's error output into an error a user can act on."""
  lines = [ERROR_LINE_PREFIX_PATTERN.sub("", line).strip() for line in stderr.splitlines() if line.strip()]
  last = lines[-1] if lines else "rclone failed without a message"
  bucket = source.options.bucket if isinstance(source, S3SourceInput) else None

  if "directory not found" in stderr or "NoSuchBucket" in stderr:
    if bucket:
      return ErrorResponse(code="source_not_found", detail=f"Bucket {bucket} does not exist")
    return ErrorResponse(code="source_not_found", detail=f"Folder {source.path or '/'} does not exist in the SWITCHdrive share")
  if "AccessDenied" in stderr or "status code: 403" in stderr or "401 Unauthorized" in stderr or "403 Forbidden" in stderr:
    if bucket:
      detail = f"Access denied to bucket {bucket}. Sources are read anonymously, so the bucket must allow public reads."
    else:
      detail = "The SWITCHdrive share can't be read anonymously. Check that the link is right and that the share has no password."
    return ErrorResponse(code="source_not_public", detail=detail)
  if any(text in stderr for text in ("send request failed", "no such host", "connection refused", "dial tcp", "x509:", "tls:")):
    return ErrorResponse(code="source_unreachable", detail=f"Couldn't connect to the source: {last}")
  return ErrorResponse(code="source_error", detail=f"Couldn't list the source: {last}")


async def probe_source(source: S3SourceInput | SwitchdriveSourceInput) -> SourceTestResult:
  """
  Lists the files of a source anonymously with `rclone lsjson`, including subfolders, and returns the first ones.
  rclone applies the `include` patterns itself, so only the files a sync would fetch are listed.
  Stops rclone as soon as it has listed one entry more than are returned, and kills it after `TEST_TIMEOUT` seconds.
  """
  await check_endpoint_resolves_publicly(source)
  async with _test_slots:
    process = await asyncio.create_subprocess_exec(
      *rclone_command("lsjson", "--recursive", "--files-only", *(f"--include={pattern}" for pattern in source.include), remote=build_remote(source)),
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
    if invalid_filter := INVALID_FILTER_PATTERN.search(error_output):
      raise ApiError(422, "validation_error", f"{source.backend}.include: {invalid_filter['reason']}")
    logger.info("Source test failed: %s", error_output.strip())
    return SourceTestResult(ok=False, error=classify_error(error_output, source), files=[], truncated=False)
  entries.sort(key=lambda entry: entry.path)
  return SourceTestResult(ok=True, error=None, files=entries, truncated=truncated)
