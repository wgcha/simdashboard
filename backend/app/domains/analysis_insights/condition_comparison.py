from __future__ import annotations

import math
from typing import Any

from .policies import json_value


ConditionValue = dict[str, Any]

# Only these exact persisted keys are interpreted.  This is intentionally not a
# substring matcher: a historic snapshot may contain unrelated model settings.
_KNOWN = (
    ("material", "재질", "material", None, False, "material"),
    ("material_id", "재질 ID", "material_id", None, False, "material"),
    ("material_grade", "재질 등급", "material_grade", None, False, "material"),
    ("thickness", "두께", "thickness", None, True, "thickness"),
    ("thickness", "두께", "thickness_mm", "mm", True, "thickness"),
    ("pressure_mpa", "하중 압력", "pressure_mpa", "MPa", False, "load"),
    ("drop_height_mm", "낙하 높이", "drop_height_mm", "mm", False, "load"),
    ("hold_time_sec", "유지 시간", "hold_time_sec", "s", False, "load"),
    ("load", "하중", "load", None, True, "load"),
    ("load_case", "하중 조건", "load_case", None, False, "load"),
    ("contact", "접촉", "contact", None, False, "contact"),
    ("contact_type", "접촉 유형", "contact_type", None, False, "contact"),
    ("mesh_size_mm", "메시 크기", "mesh_size_mm", "mm", False, "other"),
)
_CORE = {"material", "thickness", "load", "contact", "solver"}


def _equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return set(left) == set(right) and all(_equal(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    return left == right


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or value == [] or value == {}


def _nonfinite(value: Any) -> bool:
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, dict):
        return any(_nonfinite(item) for item in value.values())
    if isinstance(value, list):
        return any(_nonfinite(item) for item in value)
    return False


def _value(value: Any, default_unit: str | None, source: str) -> ConditionValue:
    if isinstance(value, dict) and set(value) == {"value", "unit"}:
        unit = value["unit"]
        if _missing(value["value"]) or _nonfinite(value["value"]) or (unit is not None and (not isinstance(unit, str) or not unit.strip())):
            return {"value": None, "unit": None, "source": source, "unusable": True}
        return {"value": value["value"], "unit": unit, "source": source}
    if _missing(value) or _nonfinite(value):
        return {"value": None, "unit": None, "source": source, "unusable": True}
    return {"value": value, "unit": default_unit, "source": source}


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict) and value and set(value) != {"value", "unit"}:
        result: dict[str, Any] = {}
        for key in sorted(value):
            result.update(_flatten(value[key], f"{prefix}.{key}" if prefix else str(key)))
        return result
    return {prefix: value} if prefix else {}


def _sources(row: dict[str, Any], load_case_id: str) -> list[tuple[str, dict[str, Any]]]:
    sources: list[tuple[str, dict[str, Any]]] = []
    input_json = json_value(row.get("input_json"))
    if row.get("execution_load_case_id") == load_case_id and isinstance(input_json, dict):
        sources.append((f"template_execution.input_json:{row.get('execution_id')}", input_json))
    metadata = json_value(row.get("metadata_json"))
    if isinstance(metadata, dict) and isinstance(metadata.get("run_conditions"), dict):
        sources.append((f"analysis_run_metadata.run_conditions:{row.get('id')}", metadata["run_conditions"]))
    return sources


def _resolved(candidates: list[ConditionValue]) -> tuple[ConditionValue | None, str | None]:
    if not candidates:
        return None, None
    if any(item.get("unusable") for item in candidates):
        return None, f"조건 값이 비어 있거나 사용할 수 없습니다: {', '.join(sorted(item['source'] for item in candidates))}."
    first = candidates[0]
    if any(not _equal(first["value"], item["value"]) or first["unit"] != item["unit"] for item in candidates[1:]):
        return None, f"같은 Run의 조건 출처가 서로 다릅니다: {', '.join(sorted(item['source'] for item in candidates))}."
    return {**first, "source": " + ".join(sorted(item["source"] for item in candidates))}, None


def _row(key: str, label: str, baseline: ConditionValue | None, target: ConditionValue | None, baseline_issue: str | None, target_issue: str | None, requires_unit: bool = False) -> dict[str, Any]:
    if baseline_issue or target_issue:
        return {"key": key, "label": label, "baseline": None if baseline_issue else baseline, "target": None if target_issue else target, "status": "UNKNOWN", "reason": baseline_issue or target_issue}
    if baseline is None and target is None:
        return {"key": key, "label": label, "baseline": None, "target": None, "status": "UNKNOWN", "reason": "두 Run에 기록된 조건이 없습니다."}
    if baseline is None or target is None:
        return {"key": key, "label": label, "baseline": baseline, "target": target, "status": "UNKNOWN", "reason": "한 Run에만 기록된 조건입니다."}
    if requires_unit and (not baseline["unit"] or not target["unit"]):
        return {"key": key, "label": label, "baseline": baseline, "target": target, "status": "UNKNOWN", "reason": "비교에 필요한 단위 기록이 없습니다."}
    if baseline["unit"] != target["unit"]:
        return {"key": key, "label": label, "baseline": baseline, "target": target, "status": "UNKNOWN", "reason": f"단위가 일치하지 않습니다 ({baseline['unit']} / {target['unit']})."}
    return {"key": key, "label": label, "baseline": baseline, "target": target, "status": "SAME" if _equal(baseline["value"], target["value"]) else "CHANGED", "reason": "기록된 조건이 같습니다." if _equal(baseline["value"], target["value"]) else "기록된 조건 값이 다릅니다."}


def compare_run_conditions(rows: list[dict[str, Any]], load_case_id: str, baseline_run_id: str, target_run_id: str) -> dict[str, Any]:
    by_run = {row["id"]: row for row in rows}
    collected: dict[str, dict[str, list[ConditionValue]]] = {baseline_run_id: {}, target_run_id: {}}
    known_keys = {item[2] for item in _KNOWN}
    for run_id in (baseline_run_id, target_run_id):
        row = by_run.get(run_id, {})
        for source, payload in _sources(row, load_case_id):
            for key, _, alias, unit, _, _ in _KNOWN:
                if alias in payload:
                    candidate = _value(payload[alias], unit, source)
                    collected[run_id].setdefault(key, []).append(candidate)
            for key, value in _flatten(payload).items():
                if key.split(".", 1)[0] not in known_keys:
                    candidate = _value(value, None, source)
                    collected[run_id].setdefault(f"input.{key}", []).append(candidate)
        if row.get("solver") is not None:
            candidate = _value(row["solver"], None, f"analysis_runs.solver:{run_id}")
            collected[run_id].setdefault("solver", []).append(candidate)

    result: list[dict[str, Any]] = []
    present_groups = {group for key, _, _, _, _, group in _KNOWN if key in collected[baseline_run_id] or key in collected[target_run_id]}
    emitted: set[str] = set()
    for key, label, _, _, requires_unit, group in _KNOWN:
        if key in emitted:
            continue
        emitted.add(key)
        if key in collected[baseline_run_id] or key in collected[target_run_id]:
            baseline, baseline_issue = _resolved(collected[baseline_run_id].get(key, []))
            target, target_issue = _resolved(collected[target_run_id].get(key, []))
            result.append(_row(key, label, baseline, target, baseline_issue, target_issue, requires_unit))
        elif key in _CORE and group not in present_groups:
            result.append(_row(key, label, None, None, None, None, requires_unit))
    if not any(item["key"] == "solver" for item in result):
        baseline, baseline_issue = _resolved(collected[baseline_run_id].get("solver", []))
        target, target_issue = _resolved(collected[target_run_id].get("solver", []))
        result.append(_row("solver", "해석기", baseline, target, baseline_issue, target_issue))
    for key in sorted((set(collected[baseline_run_id]) | set(collected[target_run_id])) - {item[0] for item in _KNOWN} - {"solver"}):
        baseline, baseline_issue = _resolved(collected[baseline_run_id].get(key, []))
        target, target_issue = _resolved(collected[target_run_id].get(key, []))
        result.append(_row(key, key.removeprefix("input."), baseline, target, baseline_issue, target_issue))
    summary = {status.lower(): sum(item["status"] == status for item in result) for status in ("CHANGED", "SAME", "UNKNOWN")}
    return {"rows": result, "summary": summary}
