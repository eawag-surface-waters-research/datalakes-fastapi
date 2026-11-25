from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from app.enums import DatasetKind, DatasetStatus, FeatureType, VariableRole
from app.models import Dataset, Parameter, Partition, Variable
from app.partitions import partitions_between, write_partition
from app.storage import zarr_dir

UNIX_UNITS = "seconds since 1970-01-01 00:00:00"
DEC_31 = 1_703_980_800  # 2023-12-31 00:00:00 UTC
JAN_3 = 1_704_240_000  # 2024-01-03 00:00:00 UTC


@pytest.fixture
def filesystem(tmp_path, monkeypatch) -> Path:
  monkeypatch.setenv("FILESYSTEM", str(tmp_path / "filesystem"))
  return tmp_path / "filesystem"


@dataclass
class TimeSeries:
  dataset: Dataset
  variables: dict[str, Variable]
  source: xr.Dataset  # the values of the source files, read directly with xarray


def netcdf(path: Path, start: int, steps: int, rng: np.random.Generator) -> Path:
  """A weather station file with a value every 10 minutes. Some temperatures are missing."""
  temp = rng.normal(5, 2, steps)
  temp[rng.random(steps) < 0.1] = np.nan
  ds = xr.Dataset(
    {
      "temp": ("time", temp, {"units": "degC"}),
      "ws": ("time", rng.gamma(2, 1.5, steps), {"units": "m s-1"}),
      "temp_qual": ("time", (rng.random(steps) < 0.2).astype("float64")),
    },
    coords={"time": ("time", start + 600 * np.arange(steps, dtype="float64"), {"units": UNIX_UNITS})},
  )
  ds.to_netcdf(path=path, engine="netcdf4", format="NETCDF4")
  return path


async def add_dataset(session, **values) -> Dataset:
  dataset = Dataset(title="Weather station", status=DatasetStatus.published, **values)
  session.add(dataset)
  await session.commit()
  await session.refresh(dataset)
  return dataset


async def add_variable(
  session, dataset: Dataset, name: str, source_name: str, role: VariableRole, dimensions: list[str] | None = None, **values
) -> Variable:
  variable = Variable(dataset_id=dataset.id, name=name, source_name=source_name, role=role, dimensions=dimensions or ["time"], **values)
  session.add(variable)
  await session.commit()
  await session.refresh(variable)
  return variable


async def build(
  session, dataset: Dataset, variables: list[Variable], layouts: dict[int | None, list[Path]], feature_type=FeatureType.time_series
) -> None:
  """
  Writes the files to one store per year and depth layout (`None` for a dataset without depth), and points the partitions at them,
  like a sync does.
  """
  partitions = []
  for layout, paths in layouts.items():
    for year in range(2023, 2025):
      store = f"{year}/1" if layout is None else f"{year}/{layout}/1"
      written = write_partition(paths, variables, feature_type, year, zarr_dir(dataset.id) / store)
      if written.rows:
        partitions.append(
          Partition(
            dataset_id=dataset.id,
            year=year,
            layout=layout,
            path=store,
            rows=written.rows,
            time_start=written.time_start,
            time_end=written.time_end,
            depth_min=written.depth_min,
            depth_max=written.depth_max,
          )
        )
  session.add_all(partitions)
  dataset.time_start, dataset.time_end = min(p.time_start for p in partitions), max(p.time_end for p in partitions)
  session.add(dataset)
  await session.commit()


@pytest.fixture
async def time_series(session, filesystem, tmp_path) -> TimeSeries:
  """A built weather station dataset with two files: Dec 31 2023 to Jan 1 2024, and Jan 3 2024. Two partitions, 2023 and 2024."""
  rng = np.random.default_rng(0)
  paths = [netcdf(tmp_path / "a.nc", DEC_31, 288, rng), netcdf(tmp_path / "b.nc", JAN_3, 144, rng)]
  assert partitions_between(datetime.fromtimestamp(DEC_31, UTC), datetime.fromtimestamp(JAN_3, UTC)) == [2023, 2024]

  parameter = Parameter(name="Air temperature")
  session.add(parameter)
  await session.commit()

  dataset = await add_dataset(session, feature_type=FeatureType.time_series)
  variables = {
    "time": await add_variable(session, dataset, "time", "time", VariableRole.time),
    "temperature": await add_variable(session, dataset, "temperature", "temp", VariableRole.data, unit="degC", parameter_id=parameter.id),
    "wind_speed": await add_variable(session, dataset, "wind_speed", "ws", VariableRole.data, unit="m s-1"),
    "temperature_qual": await add_variable(session, dataset, "temperature_qual", "temp_qual", VariableRole.flag, flag_for="temperature"),
  }
  await build(session, dataset, list(variables.values()), {None: paths})

  source = xr.concat([xr.open_dataset(path).load() for path in paths], dim="time")
  return TimeSeries(dataset=dataset, variables=variables, source=source)


def iso(time: np.datetime64) -> str:
  return np.datetime_as_string(time, unit="ms") + "Z"


def nulls(values: np.ndarray) -> list:
  """The values as the JSON has them: (nested) lists with `None` for NaN."""
  if values.ndim > 1:
    return [nulls(row) for row in values]
  return [None if np.isnan(value) else float(value) for value in values]


async def get_data(client, dataset_id: int, headers: dict[str, str] | None = None, **params):
  return await client.get(f"/datasets/{dataset_id}/data", params=params, headers=headers)


async def test_values_match_the_source_files(client, time_series):
  """The golden test: for random time windows, the values equal those read directly from the source files."""
  times = time_series.source.time.values
  second = np.timedelta64(1, "s")
  rng = np.random.default_rng(1)
  for _ in range(20):
    first, last = sorted(rng.integers(0, len(times), 2))
    # a window that starts or ends just after or before a time step leaves that time step out
    start_offset, end_offset = rng.choice([0, 1]), rng.choice([0, 1])
    start, end = times[first] + start_offset * second, times[last] - end_offset * second
    response = await get_data(client, time_series.dataset.id, start=iso(start), end=iso(end))
    assert response.status_code == 200

    expected = time_series.source.isel(time=slice(first + start_offset, last + 1 - end_offset))
    body = response.json()
    assert body["coordinates"]["time"]["values"] == [iso(time) for time in expected.time.values]
    assert body["variables"]["temperature"]["values"] == nulls(expected.temp.values)
    assert body["variables"]["wind_speed"]["values"] == nulls(expected.ws.values)
    assert body["variables"]["temperature_qual"]["values"] == nulls(expected.temp_qual.values)


async def test_whole_dataset_by_default(client, time_series):
  dataset = time_series.dataset
  response = await get_data(client, dataset.id)
  assert response.status_code == 200
  assert response.headers["content-type"] == "application/json"
  body = response.json()
  times = time_series.source.time.values

  assert body["coordinates"] == {"time": {"unit": "UTC", "dimensions": ["time"], "values": [iso(time) for time in times]}}
  assert {name: {key: value for key, value in variable.items() if key != "values"} for name, variable in body["variables"].items()} == {
    "temperature": {"parameter": "Air temperature", "unit": "degC", "dimensions": ["time"], "flag_for": None},
    "wind_speed": {"parameter": None, "unit": "m s-1", "dimensions": ["time"], "flag_for": None},
    "temperature_qual": {"parameter": None, "unit": None, "dimensions": ["time"], "flag_for": "temperature"},
  }
  assert body["variables"]["temperature"]["values"] == nulls(time_series.source.temp.values)
  assert {key: value for key, value in body.items() if key not in ["coordinates", "variables"]} == {
    "dataset_id": dataset.id,
    "version": dataset.version,
    "feature_type": "timeSeries",
    "query": {
      "variables": ["temperature", "wind_speed", "temperature_qual"],
      "start": "2023-12-31T00:00:00Z",
      "end": "2024-01-03T23:50:00Z",
      "depth_min": None,
      "depth_max": None,
      "mask": False,
    },
  }


async def test_reads_across_partitions(client, time_series):
  response = await get_data(client, time_series.dataset.id, variables="wind_speed", start="2023-12-31T23:50:00Z", end="2024-01-01T00:10:00Z")
  assert response.status_code == 200
  assert response.json()["coordinates"]["time"]["values"] == ["2023-12-31T23:50:00.000Z", "2024-01-01T00:00:00.000Z", "2024-01-01T00:10:00.000Z"]
  assert len(response.json()["variables"]["wind_speed"]["values"]) == 3


async def test_dates_cover_whole_days(client, time_series):
  response = await get_data(client, time_series.dataset.id, variables="wind_speed", start="2024-01-01", end="2024-01-01")
  assert response.status_code == 200
  body = response.json()
  times = body["coordinates"]["time"]["values"]
  assert (len(times), times[0], times[-1]) == (144, "2024-01-01T00:00:00.000Z", "2024-01-01T23:50:00.000Z")
  assert (body["query"]["start"], body["query"]["end"]) == ("2024-01-01T00:00:00Z", "2024-01-01T23:59:59.999999Z")


@pytest.mark.parametrize(("start", "end"), [("2024-01-02", "2024-01-02"), ("2030-01-01", None), (None, "2020-01-01")])
async def test_period_without_data_is_empty(client, time_series, start, end):
  params = {key: value for key, value in {"start": start, "end": end}.items() if value}
  response = await get_data(client, time_series.dataset.id, **params)
  assert response.status_code == 200
  body = response.json()
  assert body["coordinates"]["time"]["values"] == []
  assert {name: variable["values"] for name, variable in body["variables"].items()} == {"temperature": [], "wind_speed": [], "temperature_qual": []}


async def test_variables_in_the_order_requested(client, time_series):
  response = await get_data(client, time_series.dataset.id, variables="wind_speed, temperature,wind_speed", end="2023-12-31T00:00:00Z")
  assert response.status_code == 200
  assert list(response.json()["variables"]) == ["wind_speed", "temperature"]
  assert response.json()["query"]["variables"] == ["wind_speed", "temperature"]


@pytest.mark.parametrize(
  ("variables", "detail"),
  [
    ("salinity", "has no variable 'salinity'"),
    ("time", "has no variable 'time'"),  # coordinates are always returned, so they aren't variables
    ("temperature,salinity,oxygen", "has no variables 'salinity', 'oxygen'"),
  ],
)
async def test_unknown_variable(client, time_series, variables, detail):
  dataset_id = time_series.dataset.id
  response = await get_data(client, dataset_id, variables=variables)
  assert response.status_code == 400
  assert response.json() == {
    "detail": f"Dataset {dataset_id} {detail}. Variables: temperature, wind_speed, temperature_qual",
    "code": "unknown_variable",
  }


async def test_variables_without_names(client, time_series):
  response = await get_data(client, time_series.dataset.id, variables=" , ")
  assert response.status_code == 422
  assert response.json()["code"] == "validation_error"


async def test_invalid_times(client, time_series):
  response = await get_data(client, time_series.dataset.id, start="2024-01-03", end="2024-01-01")
  assert response.status_code == 400
  assert response.json() == {"detail": "start (2024-01-03) is after end (2024-01-01)", "code": "invalid_time_range"}

  response = await get_data(client, time_series.dataset.id, start="yesterday")
  assert response.status_code == 422
  assert response.json()["code"] == "validation_error"


async def test_dataset_without_data(client, session):
  response = await get_data(client, 999)
  assert response.status_code == 404
  assert response.json() == {"detail": "Dataset 999 not found", "code": "not_found"}

  link = await add_dataset(session, kind=DatasetKind.link, external_url="https://www.alplakes.eawag.ch/geneva")
  response = await get_data(client, link.id)
  assert response.status_code == 404
  assert response.json() == {
    "detail": f"Dataset {link.id} is a link dataset. Its data is at https://www.alplakes.eawag.ch/geneva",
    "code": "link_dataset",
  }

  unbuilt = await add_dataset(session, feature_type=FeatureType.time_series)
  response = await get_data(client, unbuilt.id)
  assert response.status_code == 404
  assert response.json() == {"detail": f"Dataset {unbuilt.id} has no data yet. Sync it first.", "code": "no_data"}


async def test_profiles_not_yet(client, session):
  dataset = await add_dataset(
    session, feature_type=FeatureType.profile, time_start=datetime(2024, 1, 1, tzinfo=UTC), time_end=datetime(2024, 2, 1, tzinfo=UTC)
  )
  response = await get_data(client, dataset.id)
  assert response.status_code == 501
  assert response.json()["code"] == "not_implemented"


async def test_etag_is_the_version(client, session, time_series):
  dataset = time_series.dataset
  response = await get_data(client, dataset.id, variables="temperature")
  etag = f'"{dataset.id}-v{dataset.version}"'
  assert response.headers["etag"] == etag

  for sent in [etag, f"W/{etag}", etag.strip('"'), f'"{dataset.id}-v0", {etag}', "*"]:
    response = await get_data(client, dataset.id, headers={"If-None-Match": sent}, variables="temperature")
    assert response.status_code == 304, sent
    assert response.headers["etag"] == etag
    assert response.content == b""

  for sent in [f'"{dataset.id}-v0"', f'"{dataset.id + 1}-v{dataset.version}"', ""]:
    response = await get_data(client, dataset.id, headers={"If-None-Match": sent}, variables="temperature")
    assert response.status_code == 200, sent

  # a new version of the data
  dataset.version += 1
  session.add(dataset)
  await session.commit()
  response = await get_data(client, dataset.id, headers={"If-None-Match": etag}, variables="temperature")
  assert response.status_code == 200
  assert response.headers["etag"] == f'"{dataset.id}-v{dataset.version}"'
  assert response.json()["version"] == dataset.version


async def test_website_can_read_the_etag(client, time_series):
  """The website runs on another origin, so the ETag must be exposed for its code to read it and send it back."""
  response = await get_data(client, time_series.dataset.id, headers={"Origin": "http://localhost:3000"}, variables="temperature")
  assert response.status_code == 200
  assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
  assert response.headers["access-control-expose-headers"] == "ETag"


async def test_invalid_request_with_matching_etag(client, time_series):
  """A request that makes no sense is refused, even if the data didn't change."""
  dataset = time_series.dataset
  response = await get_data(client, dataset.id, headers={"If-None-Match": f'"{dataset.id}-v{dataset.version}"'}, variables="salinity")
  assert response.status_code == 400


# A thermistor chain (timeSeriesProfile) that was redeployed with other depths: two depth layouts


LAYOUT_1 = [0.5, 1.0, 2.0]
LAYOUT_2 = [0.5, 1.5, 2.0, 3.0]
ALL_DEPTHS = [0.5, 1.0, 1.5, 2.0, 3.0]


def profile_netcdf(path: Path, start: int, steps: int, depths: list[float], rng: np.random.Generator, depth_first: bool = False) -> Path:
  """A thermistor chain file with a value every 10 minutes at each depth, and a surface temperature. Some temperatures are missing."""
  temp = rng.normal(8, 3, (steps, len(depths)))
  temp[rng.random(temp.shape) < 0.1] = np.nan
  dims, values = (("depth", "time"), temp.T) if depth_first else (("time", "depth"), temp)
  ds = xr.Dataset(
    {
      "temp": (dims, values, {"units": "degC"}),
      "surfacetemp": ("time", rng.normal(9, 3, steps), {"units": "degC"}),
    },
    coords={
      "time": ("time", start + 600 * np.arange(steps, dtype="float64"), {"units": UNIX_UNITS}),
      "depth": ("depth", np.asarray(depths), {"units": "m"}),
    },
  )
  ds.to_netcdf(path=path, engine="netcdf4", format="NETCDF4")
  return path


@pytest.fixture
async def thermistor_chain(session, filesystem, tmp_path) -> TimeSeries:
  """
  Layout 1 from Dec 31 2023 to Jan 1 2024, with the temperature stored as (depth, time). Layout 2 on Jan 3 2024, with other depths.
  Three partitions: 2023 and 2024 of layout 1, 2024 of layout 2.
  """
  rng = np.random.default_rng(2)
  a = profile_netcdf(tmp_path / "a.nc", DEC_31, 288, LAYOUT_1, rng, depth_first=True)
  b = profile_netcdf(tmp_path / "b.nc", JAN_3, 144, LAYOUT_2, rng)

  dataset = await add_dataset(session, feature_type=FeatureType.time_series_profile)
  variables = {
    "time": await add_variable(session, dataset, "time", "time", VariableRole.time),
    "depth": await add_variable(session, dataset, "depth", "depth", VariableRole.coordinate, unit="m", dimensions=["depth"]),
    "temperature": await add_variable(session, dataset, "temperature", "temp", VariableRole.data, unit="degC", dimensions=["time", "depth"]),
    "surface_temperature": await add_variable(session, dataset, "surface_temperature", "surfacetemp", VariableRole.data, unit="degC"),
  }
  await build(session, dataset, list(variables.values()), {1: [a], 2: [b]}, FeatureType.time_series_profile)

  # the files joined on all their depths, NaN where a file has no such depth
  files = [xr.open_dataset(path).load().transpose("time", "depth") for path in [a, b]]
  source = xr.concat(files, dim="time", join="outer")
  return TimeSeries(dataset=dataset, variables=variables, source=source)


async def test_profile_values_match_the_source_files(client, thermistor_chain):
  """The golden test for a timeSeriesProfile, with random time windows and depth ranges, across the change of depth layout."""
  source = thermistor_chain.source
  times = source.time.values
  rng = np.random.default_rng(3)
  for _ in range(20):
    first, last = sorted(rng.integers(0, len(times), 2))
    depth_min, depth_max = sorted(rng.choice([0.0, 0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0], 2))
    params = {"start": iso(times[first]), "end": iso(times[last]), "depth_min": depth_min, "depth_max": depth_max}
    response = await get_data(client, thermistor_chain.dataset.id, **params)
    assert response.status_code == 200, params
    body = response.json()

    # the depths of the layouts the window touches: layout 1 has the first 288 time steps, layout 2 the rest
    depths = sorted(set(LAYOUT_1 if first < 288 else []) | set(LAYOUT_2 if last >= 288 else []))
    depths = [depth for depth in depths if depth_min <= depth <= depth_max]
    expected = source.isel(time=slice(first, last + 1)).sel(depth=depths)

    assert body["coordinates"]["time"]["values"] == [iso(time) for time in expected.time.values], params
    assert body["coordinates"]["depth"]["values"] == expected.depth.values.tolist(), params
    assert body["variables"]["temperature"]["values"] == nulls(expected.temp.values), params
    assert body["variables"]["surface_temperature"]["values"] == nulls(expected.surfacetemp.values), params


async def test_profile_shape(client, thermistor_chain):
  response = await get_data(client, thermistor_chain.dataset.id)
  assert response.status_code == 200
  body = response.json()
  assert body["feature_type"] == "timeSeriesProfile"
  assert body["coordinates"]["depth"] == {"unit": "m", "dimensions": ["depth"], "values": ALL_DEPTHS}
  assert len(body["coordinates"]["time"]["values"]) == 288 + 144
  assert {name: (variable["dimensions"], variable["unit"]) for name, variable in body["variables"].items()} == {
    "temperature": (["time", "depth"], "degC"),
    "surface_temperature": (["time"], "degC"),
  }
  assert (body["query"]["depth_min"], body["query"]["depth_max"]) == (None, None)

  # a depth that a layout doesn't have is null at its time steps: 1.0 m in layout 2, 3.0 m in layout 1
  temperature = np.array(body["variables"]["temperature"]["values"], dtype="float64")
  assert np.isnan(temperature[288:, ALL_DEPTHS.index(1.0)]).all()
  assert np.isnan(temperature[:288, ALL_DEPTHS.index(3.0)]).all()


async def test_one_layout_has_only_its_depths(client, thermistor_chain):
  response = await get_data(client, thermistor_chain.dataset.id, start="2024-01-03", variables="temperature")
  assert response.status_code == 200
  body = response.json()
  assert body["coordinates"]["depth"]["values"] == LAYOUT_2
  assert len(body["variables"]["temperature"]["values"][0]) == len(LAYOUT_2)


async def test_depth_range(client, thermistor_chain):
  response = await get_data(client, thermistor_chain.dataset.id, depth_min=1, depth_max=2, end="2023-12-31T00:00:00Z")
  assert response.status_code == 200
  body = response.json()
  assert body["coordinates"]["depth"]["values"] == [1.0, 2.0]  # 1.5 m is only in layout 2, which starts later
  assert (body["query"]["depth_min"], body["query"]["depth_max"]) == (1, 2)
  expected = thermistor_chain.source.isel(time=[0]).sel(depth=[1.0, 2.0]).temp.values
  assert body["variables"]["temperature"]["values"] == nulls(expected)


async def test_depth_range_without_depths(client, thermistor_chain):
  """Variables with depth have no values left, the others keep theirs."""
  response = await get_data(client, thermistor_chain.dataset.id, depth_min=10, end="2023-12-31T00:10:00Z")
  assert response.status_code == 200
  body = response.json()
  assert body["coordinates"]["depth"]["values"] == []
  assert body["variables"]["temperature"]["values"] == [[], []]
  assert len(body["variables"]["surface_temperature"]["values"]) == 2


async def test_depth_is_a_coordinate(client, thermistor_chain):
  response = await get_data(client, thermistor_chain.dataset.id, variables="depth")
  assert response.status_code == 400
  assert response.json()["code"] == "unknown_variable"


async def test_invalid_depth_ranges(client, time_series, thermistor_chain):
  response = await get_data(client, time_series.dataset.id, depth_max=10)
  assert response.status_code == 400
  assert response.json() == {
    "detail": f"Dataset {time_series.dataset.id} has no depth coordinate, so depth_min and depth_max can't be used",
    "code": "no_depth",
  }

  response = await get_data(client, thermistor_chain.dataset.id, depth_min=10, depth_max=2.5)
  assert response.status_code == 400
  assert response.json() == {"detail": "depth_min (10) is deeper than depth_max (2.5)", "code": "invalid_depth_range"}

  response = await get_data(client, thermistor_chain.dataset.id, depth_min=-1)
  assert response.status_code == 422
  assert response.json()["code"] == "validation_error"
