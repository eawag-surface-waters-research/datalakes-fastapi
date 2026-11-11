import asyncio
import os
import socket
from datetime import UTC, datetime
from urllib.parse import urlsplit

import pytest
from sqlmodel import select

from app import rclone
from app.backends import BACKENDS
from app.enums import DatasetKind, JobStatus, JobType, SourceBackend
from app.models import Dataset, Job, Source
from app.routes import sources as sources_routes
from app.schemas import SourceTestResult


async def test_list_source_types(client):
  response = await client.get("/source-types")
  assert response.status_code == 200
  types = {source_type["backend"]: source_type for source_type in response.json()}
  assert set(types) == {"s3", "switchdrive"}
  required = {backend: [option["name"] for option in source_type["options"] if option["required"]] for backend, source_type in types.items()}
  assert required == {"s3": ["bucket"], "switchdrive": ["share_url"]}


S3_SOURCE = {"backend": "s3", "options": {"bucket": "eawag-lexplore", "region": "eu-central-1"}, "path": "thermistorchain/Level2"}
SWITCHDRIVE_SOURCE = {"backend": "switchdrive", "options": {"share_url": "https://drive.switch.ch/index.php/s/aB3dE5fG7hJ9kL1"}}


@pytest.fixture
def no_rclone(monkeypatch):
  """
  Fails the test if the endpoint starts rclone. For tests of requests that must be rejected before the source is contacted.
  The code starts rclone with `asyncio.create_subprocess_exec`, which is replaced here for the test by a function that raises an exception, which fails the test if it is called.
  """

  async def fail(*args, **kwargs):
    raise AssertionError("rclone must not run: the request should have been rejected before the source is contacted")

  monkeypatch.setattr(asyncio, "create_subprocess_exec", fail)


def with_options(source: dict, **options) -> dict:
  return {**source, "options": {**source["options"], **options}}


async def test_source_types_list_exactly_the_accepted_options(client):
  """The options shown to clients are the allowlist that's enforced."""
  response = await client.get("/source-types")
  listed = {source_type["backend"]: [option["name"] for option in source_type["options"]] for source_type in response.json()}
  assert listed == {backend.info.id: list(backend.model_fields) for backend in BACKENDS}


@pytest.mark.parametrize(
  "option",
  [
    "session_token",
    "Bucket",  # names are case-sensitive
  ],
)
async def test_option_not_on_the_allowlist_is_rejected(client, no_rclone, option):
  response = await client.post("/sources/test", json=with_options(S3_SOURCE, **{option: "x"}))
  assert response.status_code == 422
  assert response.json() == {"detail": f"Unknown option '{option}' for backend s3. Allowed: bucket, region, endpoint", "code": "unknown_option"}


@pytest.mark.parametrize(
  "endpoint",
  [
    "http://s3.example.org",  # not HTTPS
    "https://127.0.0.1",  # localhost IP
    "https://10.0.0.5:9000",  # private IP
    "https://169.254.169.254",  # AWS metadata service
    "https://[::1]",  # localhost IPv6
    "https://localhost",
    "https://minio:9000",  # a name without a domain
    "https://s3.internal",  # a name without a public suffix
    "https://s3.example.org:port",  # a non-numeric port
    "ftp://s3.example.org",  # not HTTPS
    "https://AKIA:secret@s3.example.org",  # credentials in the URL
    "https://AKIA@s3.example.org",  # credentials in the URL
    "https://s3.example.org/?X-Amz-Credential=AKIA",  # credentials in the query string
    "https://s3.example.org/#x",  # fragment in the URL
  ],
)
async def test_non_public_endpoint_is_rejected(client, no_rclone, endpoint):
  response = await client.post("/sources/test", json=with_options(S3_SOURCE, endpoint=endpoint))
  assert response.status_code == 422
  body = response.json()
  assert body["code"] == "validation_error"
  assert body["detail"].startswith("s3.options.endpoint: ")


async def test_endpoint_that_resolves_to_a_private_address_is_rejected(client, no_rclone, monkeypatch):
  async def resolve(host):
    return ["93.184.215.14", "10.1.2.3"]

  monkeypatch.setattr(rclone, "resolve", resolve)
  response = await client.post("/sources/test", json=with_options(S3_SOURCE, endpoint="https://s3.example.org"))
  assert response.status_code == 422
  assert response.json() == {"detail": "s3.options.endpoint: s3.example.org is not a public address", "code": "validation_error"}


@pytest.mark.parametrize(
  ("change", "field"),
  [
    ({"path": "/thermistorchain"}, "s3.path"),  # leading slash
    ({"path": "thermistorchain/../other"}, "s3.path"),  # path traversal
    ({"options": {"bucket": "Not_A_Bucket"}}, "s3.options.bucket"),  # invalid bucket name
    ({"options": {"bucket": "eawag-lexplore", "region": "eu central"}}, "s3.options.region"),  # space in region
    ({"options": {}}, "s3.options.bucket"),  # missing bucket
  ],
)
async def test_invalid_source_is_rejected(client, no_rclone, change, field):
  response = await client.post("/sources/test", json={**S3_SOURCE, **change})
  assert response.status_code == 422
  body = response.json()
  assert body["code"] == "validation_error"
  assert body["detail"].startswith(f"{field}: ")


@pytest.mark.parametrize(
  "share_url",
  [
    "https://example.org/index.php/s/aB3dE5fG7hJ9kL1",  # invalid switchdrive domain
    "https://drive.switch.ch/index.php/s/",  # missing share ID
    "https://drive.switch.ch/index.php/apps/files/?dir=/Level2",  # not a public link
  ],
)
async def test_invalid_share_link_is_rejected(client, no_rclone, share_url):
  response = await client.post("/sources/test", json=with_options(SWITCHDRIVE_SOURCE, share_url=share_url))
  assert response.status_code == 422
  assert response.json()["detail"].startswith("switchdrive.options.share_url: Must be a SWITCHdrive public link")


async def test_unknown_backend_is_rejected(client, no_rclone):
  response = await client.post("/sources/test", json={"backend": "gdrive", "options": {}})
  assert response.status_code == 422
  assert response.json()["code"] == "validation_error"


TEST_S3_ENDPOINT = os.getenv("TEST_S3_ENDPOINT", "http://localhost:8333")


@pytest.fixture
def s3_endpoint(monkeypatch) -> str:
  """The local test S3 server. It's in a private network, so private endpoints are allowed for these tests."""
  url = urlsplit(TEST_S3_ENDPOINT)
  try:
    socket.create_connection((url.hostname, url.port), timeout=1).close()
  except OSError:
    pytest.skip(f"The test S3 server isn't running at {TEST_S3_ENDPOINT}")
  monkeypatch.setenv("SOURCE_ALLOW_PRIVATE_ENDPOINTS", "true")
  return TEST_S3_ENDPOINT


def local_s3(endpoint: str, bucket: str, path: str = "", include: list[str] | None = None) -> dict:
  return {"backend": "s3", "options": {"bucket": bucket, "endpoint": endpoint}, "path": path, "include": include or []}


async def test_public_bucket_is_listed(client, s3_endpoint):
  response = await client.post("/sources/test", json=local_s3(s3_endpoint, "public", include=["*.nc"]))
  assert response.status_code == 200
  body = response.json()
  assert body["ok"] is True
  assert body["error"] is None
  assert body["truncated"] is False
  files = {entry["path"]: entry for entry in body["files"]}
  assert list(files) == ["L2/2024/L2_202406.nc", "L2/2025/L2_202501.nc", "L2/L2_latest.nc"]
  assert set(files["L2/2024/L2_202406.nc"]) == {"path", "size", "modified_at"}
  assert files["L2/2024/L2_202406.nc"]["size"] == 19
  assert files["L2/2024/L2_202406.nc"]["modified_at"] is not None


async def test_without_patterns_every_file_is_listed(client, s3_endpoint):
  response = await client.post("/sources/test", json=local_s3(s3_endpoint, "public"))
  assert [entry["path"] for entry in response.json()["files"]] == ["L2/2024/L2_202406.nc", "L2/2025/L2_202501.nc", "L2/L2_latest.nc", "README.md"]


@pytest.mark.parametrize(
  ("include", "expected"),
  [
    (["/L2/2024/*.nc", "/README.md"], ["L2/2024/L2_202406.nc", "README.md"]),  # several patterns
    (["2024/*"], []),  # only at the top level: rclone doesn't look inside L2
    (["**/2024/*"], ["L2/2024/L2_202406.nc"]),
    (["{{L2_\\d+\\.nc}}"], ["L2/2024/L2_202406.nc", "L2/2025/L2_202501.nc"]),  # a regular expression
  ],
)
async def test_include_patterns_are_applied_by_rclone(client, s3_endpoint, include, expected):
  response = await client.post("/sources/test", json=local_s3(s3_endpoint, "public", include=include))
  assert response.json()["ok"] is True
  assert [entry["path"] for entry in response.json()["files"]] == expected


@pytest.mark.parametrize(
  ("pattern", "detail"),
  [
    ("[abc", "s3.include: mismatched '[' and ']' in glob \"[abc\""),
    ("{a,b", "s3.include: mismatched '{' and '}' in glob \"{a,b\""),
    ("{{(}}", 's3.include: bad glob pattern "{{(}}"'),
  ],
)
async def test_invalid_pattern_is_rejected(client, pattern, detail):
  response = await client.post("/sources/test", json={**S3_SOURCE, "include": [pattern]})
  assert response.status_code == 422
  body = response.json()
  assert body["code"] == "validation_error"
  assert body["detail"].startswith(detail)


async def test_missing_prefix_is_empty_but_ok(client, s3_endpoint):
  response = await client.post("/sources/test", json=local_s3(s3_endpoint, "public", path="no/such/prefix"))
  assert response.json() == {"ok": True, "error": None, "files": [], "truncated": False}


async def test_missing_bucket_is_reported(client, s3_endpoint):
  response = await client.post("/sources/test", json=local_s3(s3_endpoint, "missing"))
  assert response.status_code == 200
  assert response.json() == {
    "ok": False,
    "error": {"detail": "Bucket missing does not exist", "code": "source_not_found"},
    "files": [],
    "truncated": False,
  }


async def test_private_bucket_is_reported(client, s3_endpoint):
  response = await client.post("/sources/test", json=local_s3(s3_endpoint, "private"))
  assert response.status_code == 200
  assert response.json() == {
    "ok": False,
    "error": {
      "detail": "Access denied to bucket private. Sources are read anonymously, so the bucket must allow public reads.",
      "code": "source_not_public",
    },
    "files": [],
    "truncated": False,
  }


async def test_long_listing_is_truncated(client, s3_endpoint, monkeypatch):
  monkeypatch.setattr(rclone, "TEST_MAX_ENTRIES", 3)
  response = await client.post("/sources/test", json=local_s3(s3_endpoint, "public"))
  body = response.json()
  assert body["ok"] is True
  assert body["truncated"] is True
  assert len(body["files"]) == 3


async def test_unreachable_endpoint_is_reported(client, monkeypatch):
  def free_port() -> int:
    with socket.socket() as sock:
      sock.bind(("127.0.0.1", 0))
      return sock.getsockname()[1]

  monkeypatch.setenv("SOURCE_ALLOW_PRIVATE_ENDPOINTS", "true")
  response = await client.post("/sources/test", json=local_s3(f"http://127.0.0.1:{free_port()}", "public"))
  body = response.json()
  assert body["ok"] is False
  assert body["error"]["code"] == "source_unreachable"
  assert body["error"]["detail"].startswith("Couldn't connect to the source: ")


async def add_dataset(session, kind=DatasetKind.data) -> Dataset:
  dataset = Dataset(title="Thermistor chain", kind=kind, external_url="https://example.org" if kind == DatasetKind.link else None)
  session.add(dataset)
  await session.commit()
  await session.refresh(dataset)
  return dataset


async def stored_source(session, dataset_id) -> Source | None:
  return (await session.exec(select(Source).where(Source.dataset_id == dataset_id).execution_options(populate_existing=True))).first()


async def test_set_source(client, session, s3_endpoint):
  dataset = await add_dataset(session)
  response = await client.put(f"/datasets/{dataset.id}/source", json=local_s3(s3_endpoint, "public", path="L2/", include=["*.nc"]))
  assert response.status_code == 201
  body = response.json()
  assert body["dataset_id"] == dataset.id
  assert body["backend"] == "s3"
  assert body["options"] == {"bucket": "public", "endpoint": s3_endpoint, "region": None}
  assert body["path"] == "L2"
  assert body["include"] == ["*.nc"]
  assert body["last_tested_at"] is not None
  assert body["last_synced_at"] is None
  assert set(body) == {"dataset_id", "backend", "options", "path", "include", "last_tested_at", "last_synced_at", "created_at", "updated_at"}

  await session.refresh(dataset)
  assert dataset.version == 2


async def test_replace_source(client, session, s3_endpoint):
  dataset = await add_dataset(session)
  first = (await client.put(f"/datasets/{dataset.id}/source", json=local_s3(s3_endpoint, "public"))).json()
  response = await client.put(f"/datasets/{dataset.id}/source", json=local_s3(s3_endpoint, "public", path="L2/2024"))
  assert response.status_code == 200
  body = response.json()
  assert body["path"] == "L2/2024"
  assert body["created_at"] == first["created_at"]
  assert body["last_tested_at"] > first["last_tested_at"]

  assert len((await session.exec(select(Source).where(Source.dataset_id == dataset.id))).all()) == 1
  await session.refresh(dataset)
  assert dataset.version == 3


@pytest.mark.parametrize(
  ("bucket", "error"),
  [
    (
      "private",
      {
        "detail": "Access denied to bucket private. Sources are read anonymously, so the bucket must allow public reads.",
        "code": "source_not_public",
      },
    ),
    ("missing", {"detail": "Bucket missing does not exist", "code": "source_not_found"}),
  ],
)
async def test_source_that_cant_be_read_is_not_saved(client, session, s3_endpoint, bucket, error):
  dataset = await add_dataset(session)
  response = await client.put(f"/datasets/{dataset.id}/source", json=local_s3(s3_endpoint, bucket))
  assert response.status_code == 422
  assert response.json() == error
  assert await stored_source(session, dataset.id) is None
  await session.refresh(dataset)
  assert dataset.version == 1


async def test_failed_test_keeps_the_current_source(client, session, s3_endpoint):
  dataset = await add_dataset(session)
  await client.put(f"/datasets/{dataset.id}/source", json=local_s3(s3_endpoint, "public"))
  response = await client.put(f"/datasets/{dataset.id}/source", json=local_s3(s3_endpoint, "private"))
  assert response.status_code == 422
  source = await stored_source(session, dataset.id)
  assert source is not None and source.options["bucket"] == "public"


async def test_set_source_of_unknown_dataset(client, no_rclone):
  response = await client.put("/datasets/999/source", json=S3_SOURCE)
  assert response.status_code == 404
  assert response.json() == {"detail": "Dataset 999 not found", "code": "not_found"}


async def test_link_dataset_cant_have_a_source(client, session, no_rclone):
  dataset = await add_dataset(session, kind=DatasetKind.link)
  response = await client.put(f"/datasets/{dataset.id}/source", json=S3_SOURCE)
  assert response.status_code == 422
  assert response.json() == {"detail": f"Dataset {dataset.id} is a link dataset and can't have a source", "code": "link_dataset"}


async def add_job(session, dataset_id, status=JobStatus.running) -> Job:
  job = Job(type=JobType.fetch, status=status, dataset_id=dataset_id)
  session.add(job)
  await session.commit()
  await session.refresh(job)
  return job


async def test_set_source_while_a_job_is_in_progress(client, session, no_rclone):
  dataset = await add_dataset(session)
  job = await add_job(session, dataset.id)
  response = await client.put(f"/datasets/{dataset.id}/source", json=S3_SOURCE)
  assert response.status_code == 409
  assert response.json() == {"detail": f"Dataset {dataset.id} already has a job running (job {job.id})", "code": "job_in_progress"}


async def test_job_queued_during_the_test_blocks_the_source(client, session, monkeypatch):
  dataset = await add_dataset(session)
  queued: list[Job] = []

  async def probe_while_a_job_is_queued(source):
    queued.append(await add_job(session, dataset.id, status=JobStatus.queued))
    return SourceTestResult(ok=True, error=None, files=[], truncated=False)

  monkeypatch.setattr(sources_routes, "probe_source", probe_while_a_job_is_queued)
  response = await client.put(f"/datasets/{dataset.id}/source", json=S3_SOURCE)
  assert response.status_code == 409
  assert response.json()["detail"] == f"Dataset {dataset.id} already has a job queued (job {queued[0].id})"
  assert await stored_source(session, dataset.id) is None


async def add_source(session, dataset_id) -> Source:
  source = Source(
    dataset_id=dataset_id,
    backend=SourceBackend.s3,
    options={"bucket": "eawag-lexplore"},
    path="L2",
    include=["*.nc"],
    last_tested_at=datetime.now(UTC),
  )
  session.add(source)
  await session.commit()
  await session.refresh(source)
  return source


async def test_get_source(client, session):
  dataset = await add_dataset(session)
  await add_source(session, dataset.id)
  response = await client.get(f"/datasets/{dataset.id}/source")
  assert response.status_code == 200
  body = response.json()
  assert body["dataset_id"] == dataset.id
  assert body["backend"] == "s3"
  assert body["options"] == {"bucket": "eawag-lexplore"}
  assert body["path"] == "L2"
  assert body["include"] == ["*.nc"]
  assert body["last_synced_at"] is None


async def test_get_source_of_dataset_without_one(client, session):
  dataset = await add_dataset(session)
  response = await client.get(f"/datasets/{dataset.id}/source")
  assert response.status_code == 404
  assert response.json() == {"detail": f"Dataset {dataset.id} has no source", "code": "no_source"}


async def test_get_source_of_link_dataset(client, session):
  dataset = await add_dataset(session, kind=DatasetKind.link)
  response = await client.get(f"/datasets/{dataset.id}/source")
  assert response.status_code == 404
  assert response.json()["code"] == "no_source"


async def test_get_source_of_unknown_dataset(client):
  response = await client.get("/datasets/999/source")
  assert response.status_code == 404
  assert response.json() == {"detail": "Dataset 999 not found", "code": "not_found"}


async def test_delete_source(client, session):
  dataset = await add_dataset(session)
  await add_source(session, dataset.id)
  ended = await add_job(session, dataset.id, status=JobStatus.succeeded)  # jobs that ended don't block it, and are kept
  response = await client.delete(f"/datasets/{dataset.id}/source")
  assert response.status_code == 204
  assert response.content == b""
  assert await stored_source(session, dataset.id) is None
  await session.refresh(dataset)
  assert dataset.version == 2
  assert await session.get(Job, ended.id) is not None

  # it's gone, so a second delete is a 404
  response = await client.delete(f"/datasets/{dataset.id}/source")
  assert response.status_code == 404
  assert response.json()["code"] == "no_source"


async def test_delete_source_of_unknown_dataset(client):
  response = await client.delete("/datasets/999/source")
  assert response.status_code == 404
  assert response.json() == {"detail": "Dataset 999 not found", "code": "not_found"}


async def test_delete_source_while_a_job_is_in_progress(client, session):
  dataset = await add_dataset(session)
  await add_source(session, dataset.id)
  job = await add_job(session, dataset.id, status=JobStatus.queued)
  response = await client.delete(f"/datasets/{dataset.id}/source")
  assert response.status_code == 409
  assert response.json() == {"detail": f"Dataset {dataset.id} already has a job queued (job {job.id})", "code": "job_in_progress"}
  assert await stored_source(session, dataset.id) is not None
  await session.refresh(dataset)
  assert dataset.version == 1
