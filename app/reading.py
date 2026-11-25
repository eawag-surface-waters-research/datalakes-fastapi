"""
Reads the data of a dataset from its Zarr stores.

Because the read operations are CPU-bound, they run in a thread pool. When all its threads are busy, the calls wait for a free one.
"""

import asyncio
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import numpy as np
import xarray as xr

MAX_CONCURRENT_READS = 4
READ_POOL = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_READS, thread_name_prefix="data-read")  # thread pool for running CPU-bound read operations


async def in_read_pool[T](fn: Callable[..., T], *args) -> T:
  """Runs `fn(*args)` in the read pool. When all its threads are busy, the call waits for a free one."""
  return await asyncio.get_running_loop().run_in_executor(READ_POOL, partial(fn, *args))


@dataclass
class Data:
  times: np.ndarray  # datetime64[ns] in UTC, sorted
  depths: np.ndarray | None  # the depths that occur in the stores and the depth range, sorted. `None` for a dataset without depth
  values: dict[str, np.ndarray]  # variable name -> its values on (time,) or (time, depth), NaN where missing


@dataclass
class _Part:
  """What one store has in the requested time range."""

  times: np.ndarray
  depths: np.ndarray | None
  values: dict[str, np.ndarray]


def read_data(
  stores: Sequence[Path],
  time_name: str,
  names: dict[str, bool],
  start: datetime | None,
  end: datetime | None,
  depth_name: str | None = None,
  depth_min: float | None = None,
  depth_max: float | None = None,
) -> Data:
  """
  Reads the variables `names` (name -> whether it has the depth dimension) from `stores`, between `start` and `end`, both included.
  `None` means no limit.

  For a dataset with depth, `depth_name` is its depth coordinate. Stores with different depths (one per depth layout) are joined on all
  the depths they have, and a value is NaN where a store has no such depth. Only the depths between `depth_min` and `depth_max`, both
  included, are returned.
  """
  lower, upper = _datetime64(start), _datetime64(end)
  parts: list[_Part] = []
  for store in stores:
    with xr.open_zarr(store, consolidated=False, chunks=None) as ds:
      # the time steps of a store are sorted and unique, so the requested ones are one slice
      store_times = ds[time_name].values
      first = 0 if lower is None else int(np.searchsorted(store_times, lower, side="left"))  # the first time step that is >= lower
      last = len(store_times) if upper is None else int(np.searchsorted(store_times, upper, side="right"))  # the first time step that is > upper
      if first == last:
        continue

      depths = None if depth_name is None else ds[depth_name].values
      values = {}
      for name, has_depth in names.items():
        if name in ds:
          values[name] = ds[name].isel({time_name: slice(first, last)}).values
        else:
          # the variable is missing in this store, so fill with NaN
          values[name] = np.full((last - first, len(depths)) if has_depth and depths is not None else last - first, np.nan)
      parts.append(_Part(times=store_times[first:last], depths=depths, values=values))

  depths = None
  if depth_name is not None:
    depths = _depth_axis([part.depths for part in parts if part.depths is not None], depth_min, depth_max)
    parts = [_on_depths(part, depths) for part in parts]

  times = np.concatenate([part.times for part in parts]) if parts else np.array([], dtype="datetime64[ns]")
  values = {}
  for name, has_depth in names.items():
    empty = np.empty((0, len(depths)) if has_depth and depths is not None else 0)
    values[name] = np.concatenate([part.values[name] for part in parts]) if parts else empty

  return Data(times=times, depths=depths, values=values)


def _depth_axis(store_depths: list[np.ndarray], depth_min: float | None, depth_max: float | None) -> np.ndarray:
  """All depths of the stores, sorted, within the depth range."""
  depths = np.unique(np.concatenate(store_depths)) if store_depths else np.array([], dtype="float64")
  depths = depths[np.isfinite(depths)]
  if depth_min is not None:
    depths = depths[depths >= depth_min]
  if depth_max is not None:
    depths = depths[depths <= depth_max]
  return depths


def _on_depths(part: _Part, depths: np.ndarray) -> _Part:
  """The part with its variables on `depths`, NaN at the depths the store doesn't have."""
  assert part.depths is not None
  # where each of the store's depths is on the axis, and which of them are on it at all (not filtered out)
  position = np.searchsorted(depths, part.depths).clip(max=max(len(depths) - 1, 0))
  on_axis = (position < len(depths)) & (depths[position] == part.depths) if len(depths) else np.zeros(len(part.depths), dtype=bool)

  values = {}
  for name, array in part.values.items():
    if array.ndim == 2:
      joined = np.full((array.shape[0], len(depths)), np.nan, dtype=np.result_type(array.dtype, np.float64))
      joined[:, position[on_axis]] = array[:, on_axis]
      values[name] = joined
    else:
      values[name] = array
  return _Part(times=part.times, depths=depths, values=values)


def _datetime64(value: datetime | None) -> np.datetime64 | None:
  """A time as the stores keep it: `datetime64[ns]` in UTC, without a time zone."""
  return None if value is None else np.datetime64(value.astimezone(UTC).replace(tzinfo=None), "ns")
