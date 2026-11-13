from datetime import UTC, datetime

import pytest
from sqlalchemy import event
from sqlmodel import select

from app.database import engine
from app.enums import DatasetKind, DatasetStatus, FeatureType, JobStatus, JobType, SourceBackend, VariableRole
from app.models import Collection, CollectionDataset, Dataset, Job, License, Organisation, Parameter, Person, Site, SiteType, Source, Variable
from app.routes.datasets import REFERENCES

# What `expand=true` adds to a dataset without references
NO_REFERENCES = {"site": None, "person": None, "organisation": None, "license": None}


async def test_create_dataset_defaults_to_a_draft(client):
  response = await client.post("/datasets", json={"title": "Lake Geneva CTD casts at SHL2"})
  assert response.status_code == 201
  body = response.json()
  assert body["title"] == "Lake Geneva CTD casts at SHL2"
  assert body["status"] == "draft"
  assert body["kind"] == "data"
  assert body["version"] == 1
  assert body["downloads"] == 0
  assert body["time_start"] is None
  assert body["created_at"] is not None


async def test_create_dataset_with_all_fields(client):
  data = {
    "title": "LéXPLORE thermistor chain",
    "description": "Water temperature every 10 minutes.",
    "status": "published",
    "feature_type": "timeSeriesProfile",
    "origin": "measurement",
    "plot_config": {"colors": "Rainbow"},
    "update_interval": 3600,
    "latitude": 46.5004,
    "longitude": 6.6588,
  }
  response = await client.post("/datasets", json=data)
  assert response.status_code == 201
  assert response.json() | data == response.json()  # response should contain all the fields we sent, plus the defaults and auto-generated fields


async def test_create_link_dataset(client):
  data = {"title": "Lake Geneva 3D model", "kind": "link", "status": "published", "external_url": "https://www.alplakes.eawag.ch/geneva"}
  response = await client.post("/datasets", json=data)
  assert response.status_code == 201
  assert response.json()["kind"] == "link"
  assert response.json()["external_url"] == data["external_url"]


async def test_create_dataset_with_id(client):
  response = await client.post("/datasets", json={"id": 512, "title": "Lake Geneva CTD casts at SHL2"})
  assert response.status_code == 201
  assert response.json()["id"] == 512
  assert (await client.get("/datasets/512")).json()["title"] == "Lake Geneva CTD casts at SHL2"


async def test_create_dataset_without_id_after_one_with_id_gets_the_next_id(client):
  first = (await client.post("/datasets", json={"title": "First"})).json()["id"]
  await client.post("/datasets", json={"id": first + 10, "title": "Given ID"})
  response = await client.post("/datasets", json={"title": "Automatic ID"})
  assert response.status_code == 201
  assert response.json()["id"] == first + 11


async def test_create_dataset_with_a_lower_id_does_not_move_the_sequence_back(client):
  await client.post("/datasets", json={"id": 100, "title": "High ID"})
  await client.post("/datasets", json={"id": 5, "title": "Low ID"})
  response = await client.post("/datasets", json={"title": "Automatic ID"})
  assert response.json()["id"] == 101


async def test_create_dataset_does_not_reuse_the_id_of_a_deleted_dataset(client):
  await client.post("/datasets", json={"id": 100, "title": "Deleted"})
  await client.delete("/datasets/100")
  await client.post("/datasets", json={"id": 50, "title": "Lower ID"})
  response = await client.post("/datasets", json={"title": "Automatic ID"})
  assert response.json()["id"] == 101


async def test_create_dataset_with_existing_id_is_rejected(client):
  existing = (await client.post("/datasets", json={"title": "Existing"})).json()["id"]
  response = await client.post("/datasets", json={"id": existing, "title": "Duplicate"})
  assert response.status_code == 409
  assert response.json() == {"detail": f"Dataset {existing} already exists", "code": "already_exists"}
  assert (await client.get(f"/datasets/{existing}")).json()["title"] == "Existing"


async def test_create_dataset_with_null_id_gets_an_automatic_id(client):
  response = await client.post("/datasets", json={"id": None, "title": "Data"})
  assert response.status_code == 201
  assert isinstance(response.json()["id"], int)


@pytest.mark.parametrize(
  "data",
  [
    {"title": "Model", "kind": "link"},
    {"title": "Data", "kind": "data", "external_url": "https://example.org"},
  ],
)
async def test_external_url_is_required_for_link_datasets_only(client, data):
  response = await client.post("/datasets", json=data)
  assert response.status_code == 422
  assert response.json()["code"] == "validation_error"
  assert "external_url" in response.json()["detail"]


@pytest.mark.parametrize("field", list(REFERENCES))
async def test_create_dataset_with_unknown_reference_is_rejected(client, field):
  response = await client.post("/datasets", json={"title": "Data", field: 999})
  assert response.status_code == 422
  assert response.json()["code"] == "unknown_reference"
  assert response.json()["detail"] == f"{REFERENCES[field].name} 999 not found"


async def test_get_dataset(client):
  created = (await client.post("/datasets", json={"title": "Data"})).json()
  response = await client.get(f"/datasets/{created['id']}")
  assert response.status_code == 200
  assert response.json() == created | NO_REFERENCES  # expanded by default, but there's nothing to expand


@pytest.fixture
async def references(session) -> dict:
  """A site, organisation, person and license, and the objects `expand=true` should return for them."""
  session.add_all([SiteType(name="lake"), Organisation(id=1, name="Eawag", link="https://www.eawag.ch/")])
  await session.flush()
  session.add_all(
    [
      Site(
        id=1, name="Lake Geneva", type="lake", geometry={"type": "Point", "coordinates": [6.5, 46.45]}, properties={"max_depth": 309}, lon_min=6.5
      ),
      Person(id=3, name="James Runnalls", email="james.runnalls@eawag.ch", organisation_id=1),
      License(id=4, name="CC BY 4.0", link="https://creativecommons.org/licenses/by/4.0/"),
    ]
  )
  await session.flush()
  return {
    # The full objects, as their own endpoints return them
    "site": {
      "id": 1,
      "name": "Lake Geneva",
      "type": "lake",
      "geometry": {"type": "Point", "coordinates": [6.5, 46.45]},
      "properties": {"max_depth": 309},
    },
    "person": {"id": 3, "name": "James Runnalls", "email": "james.runnalls@eawag.ch", "organisation_id": 1},  # not expanded further
    "organisation": {"id": 1, "name": "Eawag", "link": "https://www.eawag.ch/"},
    "license": {"id": 4, "name": "CC BY 4.0", "description": None, "link": "https://creativecommons.org/licenses/by/4.0/"},
  }


async def test_get_dataset_expands_references(client, references):
  ids = {"site_id": 1, "person_id": 3, "organisation_id": 1, "license_id": 4}
  created = (await client.post("/datasets", json={"title": "Data", **ids})).json()
  assert "site" not in created  # write responses only have the IDs

  response = await client.get(f"/datasets/{created['id']}")
  assert response.status_code == 200
  assert response.json() == created | references  # the IDs stay, next to the objects
  assert response.json()["site_id"] == 1


async def test_get_dataset_without_expand(client, references):
  created = (await client.post("/datasets", json={"title": "Data", "site_id": 1, "license_id": 4})).json()
  response = await client.get(f"/datasets/{created['id']}", params={"expand": "false"})
  assert response.status_code == 200
  assert response.json() == created  # only the IDs, no `site`, `person`… keys at all


async def test_get_unknown_dataset(client):
  response = await client.get("/datasets/999")
  assert response.status_code == 404
  assert response.json() == {"detail": "Dataset 999 not found", "code": "not_found"}


async def test_list_datasets_leaves_out_drafts(client):
  assert (await client.get("/datasets")).json() == []
  published = (await client.post("/datasets", json={"title": "Published", "status": "published"})).json()
  draft = (await client.post("/datasets", json={"title": "Draft"})).json()

  assert (await client.get("/datasets")).json() == [published | NO_REFERENCES]
  assert (await client.get("/datasets", params={"status": "draft", "expand": "false"})).json() == [draft]


async def test_list_datasets_expands_references(client, references):
  with_references = {"site_id": 1, "person_id": 3, "organisation_id": 1, "license_id": 4}
  first = (await client.post("/datasets", json={"title": "A", "status": "published", **with_references})).json()
  second = (await client.post("/datasets", json={"title": "B", "status": "published", "site_id": 1})).json()
  third = (await client.post("/datasets", json={"title": "C", "status": "published"})).json()

  assert (await client.get("/datasets")).json() == [
    first | references,
    second | NO_REFERENCES | {"site": references["site"]},  # several datasets can share a site
    third | NO_REFERENCES,
  ]
  assert (await client.get("/datasets", params={"expand": "false"})).json() == [first, second, third]


async def test_list_datasets_expands_with_one_query_per_reference(client, references):
  """However many datasets are listed, expanding takes one query per kind of reference, not one per dataset."""
  for title in "ABCDEF":
    await client.post("/datasets", json={"title": title, "status": "published", "site_id": 1, "person_id": 3, "organisation_id": 1, "license_id": 4})

  statements = []

  def count(*args):
    statements.append(args[2])

  event.listen(engine.sync_engine, "before_cursor_execute", count)
  try:
    response = await client.get("/datasets")
  finally:
    event.remove(engine.sync_engine, "before_cursor_execute", count)
  assert response.status_code == 200 and len(response.json()) == 6
  assert len(statements) == 1 + len(REFERENCES)  # the datasets, then each kind of reference


async def test_list_datasets_sorting(client, session):
  session.add_all(
    [
      Dataset(title="b old", status=DatasetStatus.published, downloads=5, time_end=datetime(2020, 1, 1, tzinfo=UTC)),
      Dataset(title="A no data", status=DatasetStatus.published, downloads=1),
      Dataset(title="c new", status=DatasetStatus.published, downloads=9, time_end=datetime(2024, 1, 1, tzinfo=UTC)),
    ]
  )
  await session.flush()

  async def titles(sort: str) -> list[str]:
    return [dataset["title"] for dataset in (await client.get("/datasets", params={"sort": sort})).json()]

  assert await titles("title") == ["A no data", "b old", "c new"]  # ignoring case
  assert await titles("recent") == ["c new", "b old", "A no data"]  # datasets without data come last
  assert await titles("downloads") == ["c new", "b old", "A no data"]
  assert [dataset["title"] for dataset in (await client.get("/datasets")).json()] == await titles("title")  # the default


@pytest.fixture
async def catalogue(session):
  """Three published datasets that differ in everything the list can be filtered by: the Geneva one is in the collection."""
  session.add_all([SiteType(name="lake"), Parameter(id=5, name="Water temperature"), Parameter(id=6, name="Oxygen")])
  await session.flush()
  session.add_all([Site(id=1, name="Lake Geneva", type="lake"), Site(id=2, name="Lake Zurich", type="lake")])
  await session.flush()
  published = {"status": DatasetStatus.published}
  geneva = Dataset(
    title="Geneva thermistor chain",
    description="Water temperature every 10 minutes",
    feature_type=FeatureType.time_series_profile,
    site_id=1,
    latitude=46.5,
    longitude=6.66,
    time_start=datetime(2019, 3, 7, tzinfo=UTC),
    time_end=datetime(2024, 10, 29, tzinfo=UTC),
    **published,
  )
  zurich = Dataset(
    title="Zurich oxygen",
    description="100% saturation",
    feature_type=FeatureType.time_series,
    site_id=2,
    latitude=47.3,
    longitude=8.5,
    time_start=datetime(2021, 1, 1, tzinfo=UTC),
    time_end=datetime(2022, 12, 31, 12, tzinfo=UTC),
    **published,
  )
  model = Dataset(title="Alplakes model", kind=DatasetKind.link, external_url="https://www.alplakes.eawag.ch", **published)
  collection = Collection(slug="lexplore", name="LéXPLORE")
  session.add_all([geneva, zurich, model, collection])
  await session.flush()
  session.add_all(
    [
      Variable(dataset_id=geneva.id, name="temperature", source_name="t", role=VariableRole.data, dimensions=["time", "depth"], parameter_id=5),
      Variable(dataset_id=zurich.id, name="oxygen", source_name="o2", role=VariableRole.data, dimensions=["time"], parameter_id=6),
      CollectionDataset(collection_id=collection.id, dataset_id=geneva.id),
    ]
  )
  await session.flush()
  return collection


async def filtered_titles(client, **params) -> list[str]:
  response = await client.get("/datasets", params=params)
  assert response.status_code == 200
  return [dataset["title"] for dataset in response.json()]


async def test_list_datasets_by_text(client, catalogue):
  assert await filtered_titles(client, q="THERMISTOR") == ["Geneva thermistor chain"]  # case-insensitive
  assert await filtered_titles(client, q="10 minutes") == ["Geneva thermistor chain"]  # also searches the description
  assert await filtered_titles(client, q="%") == ["Zurich oxygen"]  # only a literal %, not any text


async def test_list_datasets_by_site_feature_type_and_parameter(client, catalogue):
  assert await filtered_titles(client, site_id=1) == ["Geneva thermistor chain"]
  assert await filtered_titles(client, feature_type="timeSeries") == ["Zurich oxygen"]
  assert await filtered_titles(client, parameter=6) == ["Zurich oxygen"]  # link datasets have no variables
  assert await filtered_titles(client, site_id=1, feature_type="timeSeries") == []  # filters combine with AND


async def test_list_datasets_by_collection(client, catalogue):
  assert await filtered_titles(client, collection="lexplore") == ["Geneva thermistor chain"]
  assert await filtered_titles(client, collection=str(catalogue.id)) == ["Geneva thermistor chain"]
  assert await filtered_titles(client, collection="unknown") == []
  assert await filtered_titles(client, collection="999") == []


async def test_list_datasets_by_time_range(client, catalogue):
  everything = ["Geneva thermistor chain", "Zurich oxygen"]  # the link dataset has no extents, so it is never in a range
  assert await filtered_titles(client, start="2022-06-01") == everything
  assert await filtered_titles(client, end="2020-01-01") == ["Geneva thermistor chain"]
  assert await filtered_titles(client, start="2025-01-01") == []
  # Dates cover the whole day, timestamps are exact. Zurich has data until 2022-12-31 12:00 UTC and from 2021-01-01 00:00 UTC.
  assert await filtered_titles(client, start="2022-12-31", end="2022-12-31") == everything
  assert await filtered_titles(client, start="2022-12-31T13:00:00Z", end="2023-01-01") == ["Geneva thermistor chain"]
  assert await filtered_titles(client, end="2020-12-31") == ["Geneva thermistor chain"]
  assert await filtered_titles(client, end="2021-01-01") == everything


async def test_list_datasets_by_bbox(client, catalogue):
  assert await filtered_titles(client, bbox="6,46,7,47") == ["Geneva thermistor chain"]
  assert await filtered_titles(client, bbox="8,47,9,48") == ["Zurich oxygen"]
  assert await filtered_titles(client, bbox="0,0,1,1") == []  # datasets without a location are never inside a box


async def create(client, **data) -> dict:
  response = await client.post("/datasets", json={"title": "Data", **data})
  assert response.status_code == 201
  return response.json()


async def test_update_dataset(client):
  created = await create(client, description="Old", origin="measurement")
  response = await client.patch(f"/datasets/{created['id']}", json={"status": "published", "description": None})
  assert response.status_code == 200
  body = response.json()
  assert body["status"] == "published"
  assert body["description"] is None  # `null` clears a field
  assert body["origin"] == "measurement"  # fields not sent stay as they are
  assert body["version"] == 2
  assert body["updated_at"] >= created["updated_at"]
  assert "site" not in body  # references are never expanded here
  assert (await client.get(f"/datasets/{created['id']}")).json()["status"] == "published"


async def test_update_without_changes_keeps_the_version(client):
  created = await create(client, origin="measurement")
  for data in [{}, {"title": "Data", "origin": "measurement"}]:
    response = await client.patch(f"/datasets/{created['id']}", json=data)
    assert response.status_code == 200
    assert response.json()["version"] == 1


async def test_each_change_increases_the_version(client):
  created = await create(client)
  await client.patch(f"/datasets/{created['id']}", json={"title": "First"})
  response = await client.patch(f"/datasets/{created['id']}", json={"title": "Second"})
  assert response.json()["version"] == 3


async def test_update_references(client, references):
  created = await create(client)
  response = await client.patch(f"/datasets/{created['id']}", json={"site_id": 1, "license_id": 4})
  assert response.status_code == 200
  assert response.json()["site_id"] == 1
  assert response.json()["license_id"] == 4


@pytest.mark.parametrize("field", list(REFERENCES))
async def test_update_with_unknown_reference_is_rejected(client, field):
  created = await create(client)
  response = await client.patch(f"/datasets/{created['id']}", json={field: 999})
  assert response.status_code == 422
  assert response.json() == {"detail": f"{REFERENCES[field].name} 999 not found", "code": "unknown_reference"}


async def test_update_link_dataset_url(client):
  created = await create(client, kind="link", external_url="https://www.alplakes.eawag.ch")
  response = await client.patch(f"/datasets/{created['id']}", json={"external_url": "https://www.alplakes.eawag.ch/geneva"})
  assert response.status_code == 200
  assert response.json()["external_url"] == "https://www.alplakes.eawag.ch/geneva"


@pytest.mark.parametrize("external_url", [None, ""])
async def test_link_dataset_cant_lose_its_url(client, external_url):
  created = await create(client, kind="link", external_url="https://www.alplakes.eawag.ch")
  response = await client.patch(f"/datasets/{created['id']}", json={"external_url": external_url})
  assert response.status_code == 422
  assert response.json() == {"detail": "external_url: A link dataset needs an external_url", "code": "validation_error"}


async def test_update_unknown_dataset(client):
  response = await client.patch("/datasets/999", json={"title": "New"})
  assert response.status_code == 404
  assert response.json() == {"detail": "Dataset 999 not found", "code": "not_found"}


async def test_delete_dataset_and_what_belongs_to_it(client, session, catalogue):
  geneva = (await session.exec(select(Dataset).where(Dataset.title == "Geneva thermistor chain"))).one()
  dataset_id = geneva.id
  session.add_all(
    [
      Source(dataset_id=dataset_id, backend=SourceBackend.s3, options={"bucket": "eawag-lexplore"}, last_tested_at=datetime.now(UTC)),
      Job(dataset_id=dataset_id, type=JobType.fetch, status=JobStatus.succeeded),  # jobs that ended don't block the delete
    ]
  )
  await session.commit()

  response = await client.delete(f"/datasets/{dataset_id}")
  assert response.status_code == 204
  assert response.content == b""
  assert (await client.get(f"/datasets/{dataset_id}")).status_code == 404
  for model in (Variable, Source, Job, CollectionDataset):
    assert (await session.exec(select(model).where(model.dataset_id == dataset_id))).all() == []  # type: ignore[attr-defined]
  assert await session.get(Collection, catalogue.id) is not None  # the collection itself stays
  assert len((await session.exec(select(Dataset))).all()) == 2  # the other datasets stay


async def test_delete_unknown_dataset(client):
  response = await client.delete("/datasets/999")
  assert response.status_code == 404
  assert response.json() == {"detail": "Dataset 999 not found", "code": "not_found"}


@pytest.mark.parametrize("status", [JobStatus.queued, JobStatus.running])
async def test_delete_dataset_while_a_job_is_in_progress(client, session, status):
  created = await create(client)
  job = Job(dataset_id=created["id"], type=JobType.sync, status=status)
  session.add(job)
  await session.commit()
  response = await client.delete(f"/datasets/{created['id']}")
  assert response.status_code == 409
  assert response.json() == {"detail": f"Dataset {created['id']} already has a job {status} (job {job.id})", "code": "job_in_progress"}
  assert (await client.get(f"/datasets/{created['id']}")).status_code == 200
