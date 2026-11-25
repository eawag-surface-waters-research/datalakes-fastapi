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
class TimeSeriesData:
  times: np.ndarray  # datetime64[ns] in UTC, sorted
  values: dict[str, np.ndarray]  # variable name -> its values on the time axis, NaN where missing


def read_time_series(stores: Sequence[Path], time_name: str, names: Sequence[str], start: datetime | None, end: datetime | None) -> TimeSeriesData:
  """
  Reads the variables `names` of a `timeSeries` dataset from `stores`, between `start` and `end`, both included. `None` means no limit.
  """
  lower, upper = _datetime64(start), _datetime64(end)
  times: list[np.ndarray] = []
  values: dict[str, list[np.ndarray]] = {name: [] for name in names}

  for store in stores:
    with xr.open_zarr(store, consolidated=False, chunks=None) as ds:
      # the time steps of a store are sorted and unique, so the requested ones are one slice
      store_times = ds[time_name].values
      first = 0 if lower is None else int(np.searchsorted(store_times, lower, side="left"))  # the first time step that is >= lower
      last = len(store_times) if upper is None else int(np.searchsorted(store_times, upper, side="right"))  # the first time step that is > upper
      times.append(store_times[first:last])

      for name in names:
        if name in ds:
          values[name].append(ds[name].isel({time_name: slice(first, last)}).values)  # the variable is present in this store, so read its values
        else:
          values[name].append(np.full(last - first, np.nan))  # the variable is missing in this store, so fill with NaN

  return TimeSeriesData(
    times=np.concatenate(times) if times else np.array([], dtype="datetime64[ns]"),
    values={name: np.concatenate(parts) if parts else np.array([], dtype="float64") for name, parts in values.items()},
  )


def _datetime64(value: datetime | None) -> np.datetime64 | None:
  """A time as the stores keep it: `datetime64[ns]` in UTC, without a time zone."""
  return None if value is None else np.datetime64(value.astimezone(UTC).replace(tzinfo=None), "ns")
