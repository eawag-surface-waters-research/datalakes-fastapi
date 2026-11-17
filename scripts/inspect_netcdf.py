"""
Inspects one local NetCDF file and prints the suggested feature type and variables, like `POST /datasets/{dataset_id}/inspect` does.

uv run python -m scripts.inspect_netcdf path/to/file.nc [--json] [--sample-size 100000] [--parameters scripts/seed_data/parameters.json]

The rules are in `app/inspection.py`. Parameters are guessed from a JSON file of the `parameters` vocabulary (the seed data by default),
so no database is needed. Nothing is saved.
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from app.errors import ApiError
from app.inspection import SAMPLE_SIZE, guess_parameters, inspect_file
from app.models import Parameter
from app.schemas.inspect import InspectResult

SEED_PARAMETERS = Path(__file__).parent / "seed_data" / "parameters.json"


def load_parameters(path: Path) -> list[Parameter]:
  """The parameters vocabulary from a JSON list like `seed_data/parameters.json`."""
  return [Parameter(**row) for row in json.loads(path.read_text(encoding="utf-8"))]


def _short(value: Any) -> str:
  """Shortens a value for printing in the report."""
  if value is None:
    return "-"
  return f"{value:.6g}" if isinstance(value, float) else str(value)


def print_report(result: InspectResult, size: int, seconds: float, parameter_names: dict[int, str]) -> None:
  print(f"File        {result.file} ({size / 1e6:.1f} MB, {result.format}), read in {seconds:.3f} s")
  print(f"Dimensions  {', '.join(f'{dim}={size}' for dim, size in result.dimensions.items())}")
  print(f"Feature     {result.feature_type or '?'} ({result.feature_type_reason})")
  if result.time_range:
    tr = result.time_range
    print(f"Time        {tr.start:%Y-%m-%dT%H:%M:%S} to {tr.end:%Y-%m-%dT%H:%M:%S} ({tr.count} values, {tr.units})")
  if result.location:
    print(f"Location    {result.location.latitude}, {result.location.longitude}")
  for scalar in result.scalars:
    unit = f" {scalar.unit}" if scalar.unit else ""
    print(f"Scalar      {scalar.source_name} = {scalar.value}{unit} (no dimension, not suggested as a variable)")

  print("\nSuggested variables")
  rows = [
    ["source_name", "name", "role", "dimensions", "unit", "parameter", "flag_for", "decoding", "sample min..max (missing/count)", "description"]
  ]
  for v in result.variables:
    parameter = f"{parameter_names.get(v.parameter_id, '?')} ({v.parameter_id})" if v.parameter_id else ""
    sample = f"{_short(v.sample.min)}..{_short(v.sample.max)} ({v.sample.missing}/{v.sample.count})"
    decoding = json.dumps(v.decoding) if v.decoding else ""
    row = [v.source_name, v.name, v.role, ",".join(v.dimensions), v.unit or "", parameter, v.flag_for or "", decoding, sample, v.description or ""]
    rows.append(row)
  widths = [max(len(str(row[i])) for row in rows) for i in range(len(rows[0]))]
  for row in rows:
    print("  " + "  ".join(str(cell).ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip())

  if result.warnings:
    print("\nWarnings")
    for warning in result.warnings:
      print(f"  - {warning}")


if __name__ == "__main__":
  parser = argparse.ArgumentParser(prog="python -m scripts.inspect_netcdf")
  parser.add_argument("file", type=Path, help="A local NetCDF file (.nc).")
  parser.add_argument("--json", action="store_true", help="Print the result as JSON, as the endpoint returns it.")
  parser.add_argument("--sample-size", type=int, default=SAMPLE_SIZE, help="Read at most about this many values per variable.")
  parser.add_argument(
    "--parameters",
    default=str(SEED_PARAMETERS),
    help="JSON file with the parameters vocabulary to guess parameter_id from. An empty string skips the guessing.",
  )
  args = parser.parse_args()
  if not args.file.is_file():
    sys.exit(f"No such file: {args.file}")

  parameters = load_parameters(Path(args.parameters)) if args.parameters else []
  started = time.perf_counter()
  try:
    result = inspect_file(args.file, args.sample_size)
  except ApiError as exc:
    sys.exit(f"{exc.code}: {exc.detail}")
  seconds = time.perf_counter() - started
  guess_parameters(result, parameters)

  if args.json:
    print(result.model_dump_json(indent=2))
  else:
    print_report(result, args.file.stat().st_size, seconds, {p.id: p.name for p in parameters if p.id is not None})
