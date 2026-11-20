"""
Guesses each inspected variable's parameter from the `parameters` vocabulary, for `POST /datasets/{dataset_id}/inspect`.

The variable's standard_name, long_name and name in the file are compared with each parameter's name, cf_name and translations.
Abbreviations common in logger files are spelled out first, e.g. `AirTC` -> "air temperature" and `WS_ms_Avg` -> "wind speed".
The names are then compared fuzzily, so "Air temperature at 2m" still matches "Air Temperature".
See `docs/netcdf_inspection.md` for the rules.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from rapidfuzz import fuzz

from app.enums import VariableRole
from app.models import Parameter
from app.schemas.inspect import InspectedVariable, InspectResult

FUZZY_WEIGHT = 0.95  # a fuzzy match, even an exact one, ranks below a standard_name that is the parameter's cf_name

# lowercase words in variable names -> what they stand for
ABBREVIATIONS = {
  "temp": "temperature",
  "tmp": "temperature",
  "tc": "temperature",  # thermocouple, e.g. AirTC
  "wtemp": "water temperature",
  "atemp": "air temperature",
  "rh": "relative humidity",
  "hum": "humidity",
  "ws": "wind speed",
  "wspd": "wind speed",
  "spd": "speed",
  "wd": "wind direction",
  "wdir": "wind direction",
  "dir": "direction",
  "bp": "air pressure",  # barometric pressure
  "baro": "air pressure",
  "press": "pressure",
  "pres": "pressure",
  "slr": "solar irradiance",
  "slrw": "solar irradiance",
  "slrkw": "solar irradiance",
  "slrm": "solar irradiance",
  "slrmj": "solar irradiance",
  "rain": "rainfall",
  "precip": "rainfall",
  "batt": "battery",
  "do": "dissolved oxygen",
  "o2": "oxygen",
  "oxy": "oxygen",
  "sat": "saturation",
  "cond": "conductivity",
  "ec": "conductivity",
  "sal": "salinity",
  "chla": "chlorophyll a",
  "chl": "chlorophyll a",
  "turb": "turbidity",
  "lat": "latitude",
  "lon": "longitude",
  "vel": "velocity",
  "rad": "radiation",
  "diss": "dissipation",
  "toc": "total organic carbon",
  "doc": "dissolved organic carbon",
  "dic": "dissolved inorganic carbon",
  "tp": "total phosphorus",
  "tn": "total nitrogen",
  "tss": "total suspended matter",
  "tds": "total dissolved solids",
  "dewpt": "dew point",
}

IGNORED_WORDS = {
  "avg",
  "mean",
  "tot",
  "max",
  "min",
  "std",
  "smpl",
  "c",
  "degc",
  "k",
  "v",
  "w",
  "kw",
  "mj",
  "ms",
  "mbar",
  "hpa",
  "mm",
  "m",
  "pct",
}  # words that say how a value was recorded, not what it is, e.g. the units and statistics in WS_ms_Avg or Rain_mm_Tot

HEIGHT_PATTERN = re.compile(r"\d+(\.\d+)?[a-z]{0,2}")  # numbers and heights such as 2, 2m or 10cm, e.g. in AirTC_2m


@dataclass(frozen=True)
class Candidate:
  """A parameter a variable may measure, with how well it matches (0 to 100)."""

  parameter: Parameter
  score: float


@dataclass(frozen=True)
class _Key:
  """A name of a parameter to compare with: its name, cf_name or a translation."""

  parameter: Parameter
  text: str  # as in the vocabulary
  words: str  # lowercased, words separated by single spaces


def guess_parameters(result: InspectResult, parameters: Iterable[Parameter]) -> None:
  """
  Sets each variable's `parameter_id` to the best matching parameter.
  Flags aren't guessed.
  If several parameters match equally well, the one with the lowest id is set.
  """
  keys = _keys(parameters)
  for variable in result.variables:
    if variable.role == VariableRole.flag:
      continue
    if ranked := rank_parameters(variable, keys):
      variable.parameter_id = ranked[0].parameter.id


def rank_parameters(variable: InspectedVariable, keys: list[_Key]) -> list[Candidate]:
  """
  Every parameter with its best score for this variable, best first, and by id among equal scores, so ties always go the same way.
  Parameters that share no letters with it score 0 and are left out.
  """
  standard_name = _text(variable.attributes.get("standard_name"))
  texts = [text for text in (standard_name, _text(variable.attributes.get("long_name")), variable.source_name) if text]

  best: dict[int | None, Candidate] = {}  # parameter id -> its best candidate

  def offer(candidate: Candidate) -> None:
    current = best.get(candidate.parameter.id)
    if current is None or candidate.score > current.score:
      best[candidate.parameter.id] = candidate

  for key in keys:
    if standard_name and key.text == key.parameter.cf_name and _words(standard_name) == key.words:
      offer(Candidate(key.parameter, 100.0))
      continue
    for text in texts:
      for query in _queries(text):
        if score := _similarity(query, key.words) * FUZZY_WEIGHT:
          offer(Candidate(key.parameter, round(score, 1)))

  return sorted(best.values(), key=lambda c: (-c.score, c.parameter.id or 0))


def _keys(parameters: Iterable[Parameter]) -> list[_Key]:
  """The names of each parameter to compare with."""
  return [
    _Key(parameter, text, words)
    for parameter in parameters
    for text in (parameter.name, parameter.cf_name, parameter.name_de, parameter.name_fr, parameter.name_it)
    if text and (words := _words(text))
  ]


def _queries(text: str) -> set[str]:
  """
  The ways to read a variable's name: as written ("airtc 2m"), and with camelCase split and abbreviations spelled out ("air temperature").
  The first keeps names like pH whole, the second reads logger names.
  """
  return {query for query in (_words(text), _expand(text)) if query}


def _expand(text: str) -> str:
  """Splits camelCase, spells out abbreviations and drops units, statistics and heights, e.g. "WS_ms_Avg" -> "wind speed"."""
  words: list[str] = []
  for part in re.split(r"[\W_]+", text):
    lowered = part.lower()
    pieces = [lowered] if lowered in ABBREVIATIONS else _split_camel_case(part)
    for piece in pieces:
      if piece and piece not in IGNORED_WORDS and not HEIGHT_PATTERN.fullmatch(piece):
        words.extend(ABBREVIATIONS.get(piece, piece).split())
  return " ".join(words)


def _split_camel_case(word: str) -> list[str]:
  """Lowercased parts of a camelCase word, e.g. "AirTC" -> ["air", "tc"] and "WindDir" -> ["wind", "dir"]."""
  word = re.sub(r"([a-z])([A-Z])", r"\1 \2", word)
  word = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", word)
  return word.lower().split()


def _similarity(a: str, b: str) -> float:
  """
  0 to 100. The average of comparing the sorted words, which penalises extra words ("wind" vs "wind speed"),
  and comparing the shared words, which doesn't ("air temperature at" vs "air temperature").
  """
  return (fuzz.token_sort_ratio(a, b) + fuzz.token_set_ratio(a, b)) / 2


def _words(text: str) -> str:
  """Lowercased, with every run of other characters than letters and digits replaced by one space, e.g. "Water_Temperature" -> "water temperature"."""
  return " ".join(re.split(r"[\W_]+", text.lower())).strip()


def _text(value: Any) -> str | None:
  """The value if it is a non-empty string."""
  return (value.strip() or None) if isinstance(value, str) else None
