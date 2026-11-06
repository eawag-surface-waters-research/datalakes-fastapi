from datetime import UTC, datetime

import pytest

from app.enums import DatasetKind, DatasetStatus, FeatureType, VariableRole
from app.models import Collection, CollectionDataset, Dataset, Parameter, Site, SiteType, Variable


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


# TODO: Re-enable when other resources can be created
# @pytest.mark.parametrize("field", list(REFERENCES))
# async def test_create_dataset_with_unknown_reference_is_rejected(client, field):
#   response = await client.post("/datasets", json={"title": "Data", field: 999})
#   assert response.status_code == 422
#   assert response.json()["code"] == "unknown_reference"


async def test_get_dataset(client):
  created = (await client.post("/datasets", json={"title": "Data"})).json()
  response = await client.get(f"/datasets/{created['id']}")
  assert response.status_code == 200
  assert response.json() == created


async def test_get_unknown_dataset(client):
  response = await client.get("/datasets/999")
  assert response.status_code == 404
  assert response.json() == {"detail": "Dataset 999 not found", "code": "not_found"}


async def test_list_datasets_leaves_out_drafts(client):
  assert (await client.get("/datasets")).json() == []
  published = (await client.post("/datasets", json={"title": "Published", "status": "published"})).json()
  draft = (await client.post("/datasets", json={"title": "Draft"})).json()

  assert (await client.get("/datasets")).json() == [published]
  assert (await client.get("/datasets", params={"status": "draft"})).json() == [draft]


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
