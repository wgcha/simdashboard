"""Per-Scene value summaries of a DISTRIBUTION capture (issue #55, stage 2).

A capture used to keep every csv value as one JSON observation inside ``dashboard_captures.payload_json``
(about 27 times the csv size; a Case reached PostgreSQL's 256 MB jsonb limit).  New captures keep, per Scene,
only what the screens compute from those observations: for every (component, basis, kind, position, line)
group the value count, the numeric count, the maximum, the observations at the maximum (the peak
locations) and the explicit units.  Every maximum, envelope and completeness flag of the query layer is a
function of these groups, so the screens show the same numbers.  Full observations (per-line detail
charts) are parsed again on demand from the original csv bytes kept in ``dashboard_assets``.

Pure functions only; storage is in ``services.dashboard_capture`` and the reads in ``dashboard_queries``.
"""
from __future__ import annotations

import math
from collections import OrderedDict
from threading import Lock
from typing import Any, Iterable

from .parser import SOURCE_SKIPPED_LIMIT, parse_distribution_csv
from .units import explicit_unit

FORMAT_VERSION = 1
# Marks a Scene of a capture whose values live in ``dashboard_scene_summaries`` (no ``observations``).
VALUES_STORAGE = "SCENE_SUMMARY_V1"
# Peak locations kept per group.  A group with more ties at its maximum (e.g. a csv of zeros) is marked
# truncated; only a peak that this group decides is then computed from the Scene's csv files again, so no
# location is ever lost and other groups of the Scene keep using the summary.
LOCATION_CAP = 32
# Keys of an observation that the group and the Scene sources already carry.
_DERIVED_KEYS = ("component_id", "basis", "source_path", "source_ref")

GroupKey = tuple[Any, Any, Any, Any, Any]   # (component_id, basis, kind, position, line_index)


def finite_number(value: Any) -> float | None:
    """The query layer's number rule: finite floats only, booleans are not numbers."""
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


class Group:
    """Observations of one (component, basis, kind, position, line) group, reduced to what peaks need."""

    __slots__ = ("count", "numeric", "maximum", "locations", "units", "truncated")

    def __init__(self) -> None:
        self.count = 0
        self.numeric = 0
        self.maximum: float | None = None
        self.locations: list[tuple[int, dict[str, Any]]] = []   # (Scene order, observation) at the maximum
        self.units: set[str] = set()
        self.truncated = False


def group_key(observation: dict[str, Any]) -> GroupKey:
    return (observation.get("component_id"), observation.get("basis"), observation.get("kind"),
            observation.get("position"), observation.get("line_index"))


def groups_from_observations(observations: Iterable[dict[str, Any]]) -> dict[GroupKey, Group]:
    groups: dict[GroupKey, Group] = {}
    for sequence, observation in enumerate(observations):
        key = group_key(observation)
        group = groups.get(key)
        if group is None:
            group = groups[key] = Group()
        group.count += 1
        value = finite_number(observation.get("value"))
        if value is None:
            continue
        group.numeric += 1
        unit = explicit_unit(observation)
        if unit and len(group.units) < 2:   # only "none / one / several" matters (units.stress_unit_of)
            group.units.add(unit)
        if group.maximum is None or value > group.maximum:
            group.maximum = value
            group.locations = [(sequence, observation)]
        elif value == group.maximum:
            group.locations.append((sequence, observation))
    return groups


def value_components(groups: dict[GroupKey, Group]) -> list[str]:
    """Components with at least one value (the catalog's ``has_values``)."""
    return sorted({str(key[0]) for key in groups if key[0]})


def encode(groups: dict[GroupKey, Group], sources: list[dict[str, Any]]) -> dict[str, Any]:
    """JSON form of a Scene's groups; locations name their csv by index into the Scene ``sources``."""
    index = {str(source.get("path")): position for position, source in enumerate(sources)}
    encoded = []
    for key, group in groups.items():
        locations = []
        for sequence, observation in group.locations[:LOCATION_CAP]:
            compact = {name: value for name, value in observation.items() if name not in _DERIVED_KEYS}
            compact["q"] = sequence
            compact["s"] = index.get(str(observation.get("source_path")))
            locations.append(compact)
        item = {"k": list(key), "n": group.count, "m": group.numeric, "x": group.maximum,
                "u": sorted(group.units), "l": locations}
        if len(group.locations) > LOCATION_CAP:
            item["t"] = len(group.locations)
        encoded.append(item)
    return {"v": FORMAT_VERSION, "groups": encoded}


def decode(data: dict[str, Any], sources: list[dict[str, Any]]) -> dict[GroupKey, Group] | None:
    """Groups of one stored summary, or ``None`` for an unknown format.  Truncated groups are marked."""
    if not isinstance(data, dict) or data.get("v") != FORMAT_VERSION:
        return None
    groups: dict[GroupKey, Group] = {}
    for item in data.get("groups") or []:
        key = tuple(item["k"])
        group = Group()
        group.truncated = bool(item.get("t"))
        group.count, group.numeric, group.maximum = int(item["n"]), int(item["m"]), item.get("x")
        group.units = set(item.get("u") or [])
        for compact in item.get("l") or []:
            observation = {name: value for name, value in compact.items() if name not in {"q", "s"}}
            observation["component_id"], observation["basis"] = key[0], key[1]
            position = compact.get("s")
            source = sources[position] if isinstance(position, int) and 0 <= position < len(sources) else {}
            observation["source_path"] = source.get("path")
            if source.get("asset_id"):
                observation["source_ref"] = {"asset_id": source["asset_id"], "row": observation.get("row"),
                                             "column": observation.get("column")}
            group.locations.append((int(compact.get("q") or 0), observation))
        groups[key] = group
    return groups


def observations_from_sources(sources: list[dict[str, Any]], contents: dict[str, bytes]) -> list[dict[str, Any]]:
    """A Scene's observations parsed again from its original csv bytes, as ``build_distribution_scene`` made them.

    ``contents`` maps an asset id to the stored bytes.  A csv that the capture left out (Scene value limit)
    or whose bytes are missing contributes nothing, exactly as at capture time.
    """
    observations: list[dict[str, Any]] = []
    for source in sources:
        asset_id = source.get("asset_id")
        if source.get("status") == SOURCE_SKIPPED_LIMIT or not source.get("observations") or asset_id not in contents:
            continue
        parsed = _parsed(str(asset_id), str(source.get("path")), contents[asset_id])
        for parsed_observation in parsed.get("observations", []):
            observation = dict(parsed_observation)
            observation["component_id"] = parsed.get("component_id")
            observation["basis"] = parsed.get("basis", "DETAIL")
            observation["source_path"] = source.get("path")
            observation["source_ref"] = {"asset_id": asset_id, "row": observation.get("row"),
                                         "column": observation.get("column")}
            observations.append(observation)
    return observations


# In-process LRU of parsed csv files for the on-demand detail view.  Assets are immutable (the id is derived
# from the capture and the path), so the asset id is a safe key.  Bounded by entries and by values held.
_CACHE_MAX_ENTRIES = 64
_CACHE_MAX_OBSERVATIONS = 50_000   # about 30 MB of parsed observations
_cache: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
_cache_lock = Lock()


def _parsed(asset_id: str, path: str, content: bytes) -> dict[str, Any]:
    key = f"{asset_id}:{path}"
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    parsed = parse_distribution_csv(content, path)
    with _cache_lock:
        _cache[key] = parsed
        held = sum(len(item.get("observations") or ()) for item in _cache.values())
        while _cache and (len(_cache) > _CACHE_MAX_ENTRIES or held > _CACHE_MAX_OBSERVATIONS):
            _, dropped = _cache.popitem(last=False)
            held -= len(dropped.get("observations") or ())
    return parsed


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
