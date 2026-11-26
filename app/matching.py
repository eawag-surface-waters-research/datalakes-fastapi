"""
Guesses each inspected variable's parameter from the `parameters` vocabulary, for `POST /datasets/{dataset_id}/inspect`.

The variable's standard_name, long_name and name in the file are compared with each parameter's name, cf_name and translations.
Abbreviations common in logger files are spelled out first, e.g. `AirTC` -> "air temperature" and `WS_ms_Avg` -> "wind speed".
The names are then compared fuzzily, so "Air temperature at 2m" still matches "Air Temperature".
Parameters that published datasets gave a variable of the same name get a bonus, so the guesses learn from what users chose before.
See `docs/netcdf_inspection.md` for the rules.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from rapidfuzz import fuzz
from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.enums import DatasetStatus, VariableRole
from app.models import Dataset, Parameter, Variable
from app.schemas.inspect import InspectedVariable, InspectResult, ParameterCandidate
from app.schemas.vocabularies import ParameterRead

FUZZY_WEIGHT = 0.95  # a fuzzy match, even an exact one, ranks below a standard_name that is the parameter's cf_name
HISTORY_WEIGHT = 50  # points a parameter gains if every published dataset with a variable of this name gave it this parameter
FULL_CONFIDENCE = 5  # number of datasets from which a name's history counts fully. One dataset counts a fifth.
CANDIDATES = 5  # number of parameter candidates suggested per variable

type History = dict[str, dict[int, int]]  # lowercased source_name -> parameter id -> number of published datasets that chose it

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
  """A parameter a variable may measure, with how well it matches: up to 100 for the names, plus up to `HISTORY_WEIGHT` from the history."""

  parameter: Parameter
  score: float


@dataclass(frozen=True)
class _Key:
  """A name of a parameter to compare with: its name, cf_name or a translation."""

  parameter: Parameter
  text: str  # as in the vocabulary
  words: str  # lowercased, words separated by single spaces


def guess_parameters(result: InspectResult, parameters: Iterable[Parameter], history: History | None = None) -> None:
  """
  Sets each variable's `parameter_candidates` to the `CANDIDATES` best matching parameters, best first.
  Flags aren't guessed.
  Among parameters that match equally well, the one with the lowest id comes first.
  `history` comes from `parameter_history`. Without it, only the names are compared.
  """
  keys = _keys(parameters)
  for variable in result.variables:
    if variable.role == VariableRole.flag:
      continue
    ranked = rank_parameters(variable, keys, history or {})
    variable.parameter_candidates = [
      ParameterCandidate(parameter=ParameterRead.model_validate(c.parameter), score=c.score)
      for c in ranked[:CANDIDATES]
      if c.parameter.id is not None
    ]


async def parameter_history(session: AsyncSession, dataset_id: int, source_names: Iterable[str]) -> History:
  """
  For each of the source_names, ignoring case: the parameters that published datasets other than `dataset_id` gave a variable of that name,
  and in how many datasets. Drafts don't count.
  """
  name = func.lower(Variable.source_name)
  query = (
    select(name, Variable.parameter_id, func.count(col(Variable.dataset_id).distinct()))
    .join(Dataset, col(Dataset.id) == col(Variable.dataset_id))
    .where(
      Dataset.status == DatasetStatus.published,
      Variable.dataset_id != dataset_id,
      col(Variable.parameter_id).is_not(None),
      name.in_({source_name.lower() for source_name in source_names}),
    )
    .group_by(name, col(Variable.parameter_id))
  )

  history: History = {}
  for source_name, parameter_id, datasets in (await session.exec(query)).all():
    assert parameter_id is not None  # to satisfy mypy, because the query already filters out None
    history.setdefault(source_name, {})[parameter_id] = datasets
  return history


def rank_parameters(variable: InspectedVariable, keys: list[_Key], history: History | None = None) -> list[Candidate]:
  """
  Every parameter with its score for this variable, best first, and by id among equal scores, so ties always go the same way.
  The score is the best match of the names plus the bonus from the history. Parameters that score 0 are left out.
  """
  standard_name = _text(variable.attributes.get("standard_name"))
  texts = [text for text in (standard_name, _text(variable.attributes.get("long_name")), variable.source_name) if text]

  parameters: dict[int | None, Parameter] = {}
  scores: dict[int | None, float] = {}  # parameter id -> its best score for the names
  for key in keys:
    parameters[key.parameter.id] = key.parameter
    if standard_name and key.text == key.parameter.cf_name and _words(standard_name) == key.words:
      score = 100.0
    else:
      score = max((_similarity(query, key.words) * FUZZY_WEIGHT for text in texts for query in _queries(text)), default=0.0)
    scores[key.parameter.id] = max(scores.get(key.parameter.id, 0.0), score)

  for parameter_id, bonus in _history_bonus(variable.source_name, history or {}).items():
    if parameter_id in scores:  # only parameters still in the vocabulary
      scores[parameter_id] += bonus

  candidates = [Candidate(parameters[parameter_id], round(score, 1)) for parameter_id, score in scores.items() if score > 0]
  return sorted(candidates, key=lambda c: (-c.score, c.parameter.id or 0))


def _history_bonus(source_name: str, history: History) -> dict[int, float]:
  """
  Bonus points per parameter: `HISTORY_WEIGHT` times the share of datasets that chose it for this name,
  times how much the history can be trusted (fully from `FULL_CONFIDENCE` datasets on).
  E.g. a name given Solar Irradiance in 12 of 13 datasets: 50 * 12/13 = +46. A name used in one dataset: 50 * 1/5 = +10.
  """
  chosen = history.get(source_name.lower(), {})
  datasets = sum(chosen.values())
  confidence = min(datasets, FULL_CONFIDENCE) / FULL_CONFIDENCE
  return {parameter_id: HISTORY_WEIGHT * count / datasets * confidence for parameter_id, count in chosen.items()}


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
