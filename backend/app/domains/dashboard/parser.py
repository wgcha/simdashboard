from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from pathlib import PurePosixPath
from typing import Any, Iterable


EVALUATIONS = ("Settle", "Wobble", "Horizontal_Force_Angle", "Slope_Angle", "Slope_Angle_360")
_SCENE_RE = re.compile(r"^(?:(?P<sequence>\d+)_)?(?P<body>.+)$")
_SCENARIO_RE = re.compile(r"(?:^|_)Scene(?P<number>\d+)(?:_|$)", re.IGNORECASE)
_REPETITION_RE = re.compile(r"(?:^|_)(?P<value>\d+(?:st|nd|rd|th))(?:_|$)", re.IGNORECASE)
_CONTACT_RE = re.compile(r"(?:^|_)(?P<value>(?:Face|Corner|Edge)[A-Za-z0-9]+)(?:_|$)", re.IGNORECASE)
_NUM_RE = re.compile(r"^-?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?$")
MAX_CSV_ROWS = 100_000
MAX_SCENE_OBSERVATIONS = 400_000


def _number(value: Any) -> float | int | None:
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    if not _NUM_RE.fullmatch(text):
        return None
    number = float(text)
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() and "." not in text and "e" not in text.lower() else number


def _csv_rows(content: bytes) -> list[list[str]]:
    rows: list[list[str]] = []
    for row_number, row in enumerate(csv.reader(io.StringIO(content.decode("utf-8-sig"))), 1):
        if row_number > MAX_CSV_ROWS:
            raise csv.Error("CSV row limit exceeded")
        rows.append(row)
    return rows


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _finite_json(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite JSON number")
    if isinstance(value, dict):
        for nested in value.values():
            _finite_json(nested)
    elif isinstance(value, list):
        for nested in value:
            _finite_json(nested)


def _source_error(relative_path: str, *, component_id: str | None = None) -> dict[str, Any]:
    return {
        "status": "SOURCE_PARSE_ERROR",
        "source": relative_path,
        "component_id": component_id,
        "observations": [],
    }


def parse_usage_file(content: bytes, relative_path: str, *, kind: str) -> dict[str, Any]:
    """Parse one Usage evaluation file; malformed JSON never falls back to CSV."""
    suffix = PurePosixPath(relative_path).suffix.lower()
    try:
        if suffix == ".json":
            payload = json.loads(
                content.decode("utf-8-sig"),
                parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
                object_pairs_hook=_json_object,
            )
            if not isinstance(payload, dict):
                raise ValueError("JSON object expected")
            _finite_json(payload)
            return {"status": "READY", "kind": kind, "source": relative_path, "values": payload}
        rows = _csv_rows(content)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, csv.Error):
        return {"status": "SOURCE_PARSE_ERROR", "kind": kind, "source": relative_path, "values": {}}
    values: dict[str, Any] = {}
    for row in rows:
        if not row or not row[0].strip():
            continue
        key = row[0].strip()
        if key in values or len(row) < 2:
            return {"status": "SOURCE_PARSE_ERROR", "kind": kind, "source": relative_path, "values": {}}
        parsed = []
        for item in row[1:]:
            number = _number(item)
            text = item.strip()
            if number is None and text and _NUM_RE.fullmatch(text):
                return {"status": "SOURCE_PARSE_ERROR", "kind": kind, "source": relative_path, "values": {}}
            parsed.append(number if number is not None else (text or None))
        values[key] = parsed[0] if len(parsed) == 1 else parsed
    return {"status": "READY", "kind": kind, "source": relative_path, "values": values}


def _pick_usage(files: Iterable[tuple[str, bytes]]) -> dict[str, Any]:
    grouped: dict[str, list[tuple[str, bytes]]] = {item: [] for item in EVALUATIONS}
    for relative, content in files:
        stem = PurePosixPath(relative).stem.casefold()
        if "result2" in stem or "bushing" in stem:
            continue
        parts = PurePosixPath(relative).parts
        evaluation = next((name for part in parts for name in EVALUATIONS if part.casefold() == name.casefold()), None)
        if evaluation:
            grouped[evaluation].append((relative, content))
    observations: list[dict[str, Any]] = []
    for evaluation in EVALUATIONS:
        entries = grouped[evaluation]
        # The JSON with the exact expected result suffix wins. A malformed JSON
        # is retained as an error and does not silently select an equivalent CSV.
        entries.sort(key=lambda item: (PurePosixPath(item[0]).suffix.lower() != ".json", item[0].casefold()))
        seen: set[tuple[str, str]] = set()
        for relative, content in entries:
            stem = PurePosixPath(relative).stem.casefold()
            identity = (str(PurePosixPath(relative).parent), stem)
            if identity in seen:
                continue
            seen.add(identity)
            parsed = parse_usage_file(content, relative, kind=evaluation)
            observations.append({"evaluation": evaluation, **parsed})
    return {"evaluations": observations, "evaluation_names": list(EVALUATIONS)}


def parse_scene_name(name: str) -> dict[str, Any]:
    match = _SCENE_RE.match(name)
    sequence = int(match.group("sequence")) if match and match.group("sequence") else None
    scenario_match = _SCENARIO_RE.search(name)
    repetition_match = _REPETITION_RE.search(name)
    contact_match = _CONTACT_RE.search(name)
    return {
        "source_name": name,
        "scene_sequence_number": sequence,
        "scenario_number": int(scenario_match.group("number")) if scenario_match else None,
        "sequence_status": "CONFIRMED" if sequence is not None else "UNCONFIRMED",
        "order_status": "CONFIRMED" if sequence is not None else "UNCONFIRMED",
        "contact_code": contact_match.group("value") if contact_match else None,
        "repetition": repetition_match.group("value") if repetition_match else None,
    }


def _component_from_name(name: str) -> str | None:
    match = re.search(r"(?:^|_)(?:COMP|C)(?P<id>\d+)(?:_|$)", name, re.IGNORECASE)
    return f"C{match.group('id')}" if match else None


def parse_distribution_csv(content: bytes, relative_path: str) -> dict[str, Any]:
    """Parse known extracted stress CSVs while preserving source dimensions."""
    path = PurePosixPath(relative_path)
    try:
        rows = _csv_rows(content)
    except (UnicodeDecodeError, csv.Error):
        return _source_error(relative_path)
    if not rows:
        return _source_error(relative_path)
    lowered = path.name.casefold()
    component = _component_from_name(path.name)
    if lowered.startswith("layer_align_"):
        header = rows[0]
        if header[:5] != ["Node_id", "coord_x", "coord_y", "coord_z", "ref_coord"] or len(header) != 9 or component is None:
            return _source_error(relative_path, component_id=component)
        values: list[dict[str, Any]] = []
        line_columns = [(idx, value) for idx, value in enumerate(header) if re.search(r"_L[1-4]$", value, re.I)]
        if len(line_columns) != 4 or {int(name[-1]) for _, name in line_columns} != {1, 2, 3, 4}:
            return _source_error(relative_path, component_id=component)
        positions = {
            match.group(1).upper()
            for _, name in line_columns
            if (match := re.fullmatch(rf"{re.escape(component)}_SIDE_(TOP|BOT|LH|RH)_L[1-4]", name, re.I))
        }
        if len(positions) != 1:
            return _source_error(relative_path, component_id=component)
        position = next(iter(positions))
        for row_number, row in enumerate(rows[1:], 2):
            if not row:
                continue
            if len(row) != len(header) or not row[0].strip():
                return _source_error(relative_path, component_id=component)
            coordinates = [_number(row[index]) for index in (1, 2, 3, 4)]
            stresses = [_number(row[column]) for column, _ in line_columns]
            if any(value is None for value in (*coordinates, *stresses)):
                return _source_error(relative_path, component_id=component)
            for column, column_name in line_columns:
                values.append({
                    "kind": "SIDE", "position": position,
                    "line_index": int(column_name[-1]), "value": _number(row[column]),
                    "alignment_node_id": row[0].strip(),
                    "coord_x": coordinates[0], "coord_y": coordinates[1], "coord_z": coordinates[2],
                    "ref_coord": coordinates[3],
                    "row": row_number, "column": column_name,
                })
        return {"status": "READY", "source": relative_path, "component_id": component, "observations": values}
    if lowered.startswith("max_result_"):
        header = rows[0]
        if header != ["Position", "Layer_1", "Layer_2", "Layer_3", "Layer_4"] or component is None:
            return _source_error(relative_path, component_id=component)
        values = []
        seen_positions: set[str] = set()
        for row_number, row in enumerate(rows[1:], 2):
            if not row:
                continue
            if len(row) != 5:
                return _source_error(relative_path, component_id=component)
            position = row[0].strip().upper()
            parsed = [_number(item) for item in row[1:]]
            if position not in {"TOP", "BOT", "LH", "RH"} or position in seen_positions or any(item is None for item in parsed):
                return _source_error(relative_path, component_id=component)
            seen_positions.add(position)
            for idx, column_name in enumerate(header[1:], 1):
                values.append({"kind": "SIDE", "position": position, "line_index": idx, "value": parsed[idx - 1], "row": row_number, "column": column_name})
        return {"status": "READY", "source": relative_path, "component_id": component, "observations": values, "basis": "REPORTED_SUMMARY"}
    if lowered.startswith("corner_data_"):
        if rows[0] != ["Node_id", "Value"] or component is None:
            return _source_error(relative_path, component_id=component)
        corner_match = re.search(r"CORNER_(TOP|BOT)_(LH|RH)", path.name, re.I)
        if corner_match is None:
            return _source_error(relative_path, component_id=component)
        position = f"{corner_match.group(1).upper()}_{corner_match.group(2).upper()}" if corner_match else None
        values = []
        for row_number, row in enumerate(rows[1:], 2):
            if not row:
                continue
            value = _number(row[1]) if len(row) == 2 else None
            if len(row) != 2 or not row[0].strip() or value is None:
                return _source_error(relative_path, component_id=component)
            values.append({"kind": "CORNER", "position": position, "line_index": None, "node_id": row[0].strip(), "value": value, "row": row_number, "column": "Value"})
        return {"status": "READY", "source": relative_path, "component_id": component, "observations": values, "basis": "DETAIL"}
    if lowered.startswith("corner_max_result_"):
        if rows[0] != ["Position", "Max_Value"] or component is None:
            return _source_error(relative_path, component_id=component)
        values = []
        seen_positions: set[str] = set()
        for row_number, row in enumerate(rows[1:], 2):
            if not row:
                continue
            position = row[0].strip().upper() if row else ""
            value = _number(row[1]) if len(row) == 2 else None
            if position not in {"TOP_LH", "TOP_RH", "BOT_LH", "BOT_RH"} or position in seen_positions or value is None:
                return _source_error(relative_path, component_id=component)
            seen_positions.add(position)
            values.append({"kind": "CORNER", "position": position, "line_index": None, "value": value, "row": row_number, "column": "Max_Value"})
        return {"status": "READY", "source": relative_path, "component_id": component, "observations": values, "basis": "REPORTED_SUMMARY"}
    return {"status": "IGNORED", "source": relative_path, "observations": []}


def _peak(observations: list[dict[str, Any]], basis: str | None = None) -> dict[str, Any] | None:
    numeric = [item for item in observations if isinstance(item.get("value"), (int, float))]
    if not numeric:
        return None
    maximum = max(float(item["value"]) for item in numeric)
    locations = [
        {key: item.get(key) for key in ("kind", "position", "line_index", "node_id", "alignment_node_id", "coord_x", "coord_y", "coord_z", "ref_coord", "row", "column")}
        for item in numeric if float(item["value"]) == maximum
    ]
    return {"basis": basis or "DETAIL", "scope": "EXTRACTED_SIDES_AND_CORNERS", "quantity": "Max_Stress_P1 (major)_Mid", "value": maximum, "unit": None, "unit_status": "UNCONFIRMED", "locations": locations}


def build_distribution_scene(
    scene_name: str,
    files: Iterable[tuple[str, bytes]],
    ignored_sources: list[str] | None = None,
) -> dict[str, Any]:
    scene = parse_scene_name(scene_name)
    observations: list[dict[str, Any]] = []
    media: list[dict[str, Any]] = []
    components: set[str] = set()
    quality: list[str] = []
    for relative, content in files:
        suffix = PurePosixPath(relative).suffix.casefold()
        if suffix in {".csv"}:
            parsed = parse_distribution_csv(content, relative)
            parsed_observations = parsed.get("observations", [])
            if len(observations) + len(parsed_observations) > MAX_SCENE_OBSERVATIONS:
                quality.append("SOURCE_PARSE_ERROR")
                continue
            for observation in parsed_observations:
                observation["component_id"] = parsed.get("component_id")
                observation["basis"] = parsed.get("basis", "DETAIL")
                observation["source_path"] = relative
                observations.append(observation)
            if parsed.get("component_id"):
                components.add(parsed["component_id"])
            if parsed.get("status") == "SOURCE_PARSE_ERROR":
                quality.append("SOURCE_PARSE_ERROR")
            elif parsed.get("status") == "IGNORED" and ignored_sources is not None:
                ignored_sources.append(relative)
        elif suffix in {".jpg", ".jpeg", ".png", ".mp4", ".webm"}:
            media.append({"asset_id": hashlib.sha256(relative.encode()).hexdigest()[:20], "relative_path": relative, "kind": "VIDEO" if suffix in {".mp4", ".webm"} else "IMAGE", "status": "READY", "component_id": _component_from_name(PurePosixPath(relative).name), "subject_role": "UNKNOWN", "frame_role": "UNKNOWN"})
    # A peak without an explicit Component and basis would mix independent
    # measurements. Only the per-basis projections below are meaningful.
    peak = None
    component_results = []
    for component_id in sorted(components):
        corner_summary = [item for item in observations if item.get("component_id") == component_id and item.get("kind") == "CORNER" and item.get("basis") == "REPORTED_SUMMARY"]
        corner_detail = [item for item in observations if item.get("component_id") == component_id and item.get("kind") == "CORNER" and item.get("basis") == "DETAIL"]
        for summary in corner_summary:
            matching = [item for item in corner_detail if item.get("position") == summary.get("position") and isinstance(item.get("value"), (int, float))]
            if matching and max(float(item["value"]) for item in matching) != float(summary.get("value")):
                quality.append("CORNER_SUMMARY_DETAIL_MISMATCH")
    for component_id in sorted(components):
        component_observations = [item for item in observations if item.get("component_id") == component_id]
        peaks_by_basis = {basis: _peak([item for item in component_observations if item.get("basis") == basis], basis=basis) for basis in ("REPORTED_SUMMARY", "DETAIL")}
        component_results.append({"component_id": component_id, "peak": None, "peaks_by_basis": peaks_by_basis, "edge_peaks": [item for item in component_observations if item.get("kind") == "SIDE"]})
    return {**scene, "components": sorted(components), "peak": peak, "observations": observations, "edge_peaks": [item for item in observations if item.get("kind") == "SIDE"], "component_results": component_results, "media": media, "quality_issues": sorted(set(quality)), "verdict": None}


def fingerprint(manifest: Any, recipe_version: str = "dashboard-v1") -> str:
    encoded = json.dumps({"recipe_version": recipe_version, "manifest": manifest}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
