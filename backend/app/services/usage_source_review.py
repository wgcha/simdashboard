"""Shared, explicit Usage result-source selection and preflight validation.

The review representation deliberately stores JSON paths as arrays.  Raw JSON
keys commonly contain dots, slashes and spaces, so a dotted internal path is
not a safe representation.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import PurePosixPath
from typing import Any, Iterable

from ..domains.dashboard.parser import EVALUATIONS, parse_usage_file

MEDIA_SUFFIXES = {".mp4", ".webm", ".jpg", ".jpeg", ".png"}
DEFAULT_SELECTION = {"json": True, "video": True, "image": True, "csv": False}
METRICS: dict[str, tuple[tuple[str, str], ...]] = {
    "Settle": (("Set Tilt Angle @ Settle (deg)", "number"),),
    "Wobble": (("Wobble Disp. (mm)", "number"),),
    "Horizontal_Force_Angle": (("Set Tilt Angle Difference (deg)", "number"),),
    "Slope_Angle": (("Slope Angle (deg)", "number"), ("OK/NG", "verdict")),
    "Slope_Angle_360": (("OK/NG", "verdict"),),
}


def selection(value: dict[str, Any] | None) -> dict[str, bool]:
    raw = value or {}
    # ``media`` is accepted only as the legacy compact spelling.
    media = raw.get("media")
    return {name: bool(raw.get(name, media if name in {"video", "image"} and media is not None else default)) for name, default in DEFAULT_SELECTION.items()}


def identity(path: str, evaluation: str) -> tuple[str, str] | None:
    stem = PurePosixPath(path).stem
    if not stem.casefold().endswith("_result"):
        return None
    stem = stem[:-len("_result")]
    patterns = {
        "Settle": r"^(?P<condition>.+)_settle$",
        "Wobble": r"^(?P<condition>.+)_wobble_center_(?P<direction>front|back)$",
        "Horizontal_Force_Angle": r"^(?P<condition>.+)_horizontal_force_angle_(?P<direction>front|back)$",
        "Slope_Angle": r"^(?P<condition>.+)_slope_angle_(?P<direction>front|back)$",
        "Slope_Angle_360": r"^(?P<condition>.+)_slope_angle_(?P<direction>front|back)_360$",
    }
    matched = re.fullmatch(patterns[evaluation], stem, re.I)
    if not matched:
        return None
    return matched.groupdict().get("direction", "common").lower(), matched.group("condition")


def evaluation_for_path(path: str) -> str | None:
    return next((name for name in EVALUATIONS if any(part.casefold() == name.casefold() for part in PurePosixPath(path).parts)), None)


def is_result_candidate(path: str, selected: dict[str, bool]) -> bool:
    suffix = PurePosixPath(path).suffix.casefold()
    if suffix == ".json" and not selected["json"]:
        return False
    if suffix == ".csv" and not selected["csv"]:
        return False
    if suffix not in {".json", ".csv"}:
        return False
    evaluation = evaluation_for_path(path)
    return bool(evaluation and identity(path, evaluation))


def include_path(path: str, selected: dict[str, bool]) -> bool:
    suffix = PurePosixPath(path).suffix.casefold()
    media = (suffix in {".mp4", ".webm"} and selected["video"]) or (suffix in {".jpg", ".jpeg", ".png"} and selected["image"])
    return media or is_result_candidate(path, selected)


def _at_segments(payload: Any, segments: list[str]) -> tuple[Any, str]:
    current = payload
    for segment in segments:
        if not isinstance(current, dict) or segment not in current:
            return None, "MISSING_KEY"
        current = current[segment]
    if current is None:
        return current, "NULL"
    return current, "READY"


def _metric(value: Any, kind: str, status: str) -> str:
    if status != "READY":
        return status
    if kind == "number":
        return "READY" if type(value) in {int, float} and math.isfinite(float(value)) else "INVALID_TYPE"
    return "READY" if isinstance(value, str) and value in {"OK", "NG"} else "INVALID_TYPE"


def _actual_type(value: Any, status: str) -> str:
    if status == "MISSING_KEY": return "missing"
    if value is None: return "null"
    if isinstance(value, bool): return "boolean"
    if isinstance(value, (int, float)): return "number"
    if isinstance(value, str): return "string"
    if isinstance(value, list): return "array"
    if isinstance(value, dict): return "object"
    return type(value).__name__


def _key(evaluation: str, direction: str) -> str:
    return f"{evaluation}:{direction}"


def review(
    files: Iterable[tuple[str, bytes]],
    *,
    selected: dict[str, Any] | None = None,
    selected_sources: dict[str, str] | None = None,
    metric_paths: dict[str, list[str]] | None = None,
    excludes: dict[str, str] | None = None,
    profile_id: str | None = None,
    profile_revision: int | None = None,
) -> dict[str, Any]:
    chosen = selection(selected)
    source_overrides, paths, excludes = selected_sources or {}, metric_paths or {}, excludes or {}
    files = list(files)
    candidates: dict[str, list[tuple[str, bytes]]] = {}
    excluded_count = 0
    media: list[str] = []
    for path, content in files:
        suffix = PurePosixPath(path).suffix.casefold()
        if suffix in MEDIA_SUFFIXES:
            if include_path(path, chosen):
                media.append(path)
            else:
                excluded_count += 1
        elif suffix in {".json", ".csv"}:
            if is_result_candidate(path, chosen):
                evaluation = evaluation_for_path(path)
                found = identity(path, evaluation) if evaluation else None
                if evaluation and found:
                    candidates.setdefault(_key(evaluation, found[0]), []).append((path, content))
            else:
                excluded_count += 1
        else:
            excluded_count += 1
    entries: list[dict[str, Any]] = []
    selected_hashes: list[dict[str, str]] = []
    for evaluation in EVALUATIONS:
        directions = ("common",) if evaluation == "Settle" else ("front", "back")
        for direction in directions:
            slot = _key(evaluation, direction)
            available = sorted(candidates.get(slot, []), key=lambda item: item[0].casefold())
            requested = source_overrides.get(slot)
            source: tuple[str, bytes] | None = next((item for item in available if item[0] == requested), None) if requested else (available[0] if len(available) == 1 else None)
            entry: dict[str, Any] = {"evaluation": evaluation, "direction": direction, "condition": identity(source[0], evaluation)[1] if source else None,
                "source": source[0] if source else None, "candidates": [item[0] for item in available], "candidate_count": len(available), "status": "READY", "metrics": [], "values": {}}
            if not source:
                entry["status"] = "INVALID_SOURCE_SELECTION" if requested else ("DUPLICATE_CANDIDATE" if len(available) > 1 else "MISSING_SOURCE")
            else:
                parsed = parse_usage_file(source[1], source[0], kind=evaluation)
                entry["values"] = parsed["values"]
                entry["status"] = parsed["status"]
                selected_hashes.append({"source": source[0], "sha256": hashlib.sha256(source[1]).hexdigest()})
            for canonical, value_kind in METRICS[evaluation]:
                metric_id = f"{slot}:{canonical}"
                segments = paths.get(metric_id, [canonical])
                if not isinstance(segments, list) or not segments or any(not isinstance(part, str) or not part for part in segments):
                    segments, configured = [canonical], "INVALID_MAPPING"
                else:
                    configured = "READY"
                status, value = entry["status"], None
                if configured != "READY":
                    status = configured
                elif entry["status"] == "READY":
                    value, status = _at_segments(entry["values"], segments)
                    status = _metric(value, value_kind, status)
                if metric_id in excludes:
                    status = "EXCLUDED"
                entry["metrics"].append({"key": canonical, "path": segments, "value": value, "value_type": _actual_type(value, status), "expected_type": value_kind, "status": status,
                    "exclude_reason": excludes.get(metric_id)})
            entries.append(entry)
    selected_hashes.extend({"source": path, "sha256": hashlib.sha256(content).hexdigest()} for path, content in files if include_path(path, chosen) and PurePosixPath(path).suffix.casefold() in MEDIA_SUFFIXES)
    contract = {"version": 1, "profile_id": profile_id, "profile_revision": profile_revision, "selection": chosen,
        "selected_sources": source_overrides, "metric_paths": paths, "excludes": excludes, "sources": sorted(selected_hashes, key=lambda item: item["source"].casefold())}
    contract["fingerprint"] = hashlib.sha256(json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    required = [metric for entry in entries for metric in entry["metrics"] if metric["status"] not in {"READY", "EXCLUDED", "MISSING_SOURCE"}]
    missing = [metric for entry in entries for metric in entry["metrics"] if metric["status"] == "MISSING_SOURCE"]
    return {"contract_version": 1, "selection": chosen, "entries": entries, "excluded_count": excluded_count, "media_paths": sorted(media),
        "review_required_count": len(required), "blocking_count": len(required), "missing_count": len(missing), "can_publish": not required, "contract": contract}
