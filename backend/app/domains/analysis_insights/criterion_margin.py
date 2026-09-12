"""Fail-closed margin calculation from immutable per-run criterion snapshots."""

from __future__ import annotations

import math
from numbers import Real
from typing import Any

from .policies import json_value


def criterion_margin(scalar: dict[str, Any] | None, condition_row: dict[str, Any] | None) -> dict[str, Any]:
    """Return signed native-unit distance; never infer a criterion from verdicts."""
    if scalar is None:
        return _unknown("RESULT_NOT_RECORDED")
    if condition_row is not None and condition_row.get("id") != scalar.get("analysis_run_id"):
        return _unknown("CRITERION_AMBIGUOUS")
    value = scalar.get("value_double") if scalar.get("value_double") is not None else scalar.get("value_integer")
    if not _finite(value):
        return _unknown("NON_NUMERIC_VALUE")
    metadata = json_value((condition_row or {}).get("metadata_json"))
    criteria = metadata.get("result_criteria") if isinstance(metadata, dict) else None
    criterion = criteria.get(scalar.get("variable_key")) if isinstance(criteria, dict) else None
    if not isinstance(criterion, dict):
        return _unknown("CRITERION_NOT_RECORDED")
    operator, unit = criterion.get("operator"), criterion.get("unit")
    if not _valid_criterion(criterion):
        return _unknown("CRITERION_AMBIGUOUS")
    if not isinstance(unit, str) or scalar.get("unit") != unit:
        return _unknown("CRITERION_UNIT_MISMATCH")
    lower, upper = criterion.get("lower"), criterion.get("upper")
    if operator in {"LT", "LTE"} and _finite(upper):
        margin = float(upper) - float(value)
        meets = float(value) < float(upper) if operator == "LT" else float(value) <= float(upper)
    elif operator in {"GT", "GTE"} and _finite(lower):
        margin = float(value) - float(lower)
        meets = float(value) > float(lower) if operator == "GT" else float(value) >= float(lower)
    elif operator == "BETWEEN" and _finite(lower) and _finite(upper) and float(lower) <= float(upper):
        margin = min(float(value) - float(lower), float(upper) - float(value))
        meets = float(lower) <= float(value) <= float(upper)
    else:
        return _unknown("CRITERION_AMBIGUOUS")
    if not math.isfinite(margin):
        return _unknown("CRITERION_AMBIGUOUS")
    label = criterion.get("label")
    readable = {"LT": f"값 < {upper} {unit}", "LTE": f"값 ≤ {upper} {unit}", "GT": f"값 > {lower} {unit}", "GTE": f"값 ≥ {lower} {unit}", "BETWEEN": f"{lower} ≤ 값 ≤ {upper} {unit}"}[operator]
    return {"status": "AVAILABLE", "value": margin, "unit": unit, "criterion_label": f"{label}: {readable}" if isinstance(label, str) and label else readable, "reason": None, "source": f"analysis_run_metadata.result_criteria:{(condition_row or {}).get('id')}" if (condition_row or {}).get("id") else "analysis_run_metadata.result_criteria", "meets_criterion": meets}


def _finite(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, Real):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def _valid_criterion(criterion: dict[str, Any]) -> bool:
    operator = criterion.get("operator")
    if not isinstance(operator, str) or operator not in {"LT", "LTE", "GT", "GTE", "BETWEEN"}:
        return False
    if not isinstance(criterion.get("unit"), str) or not criterion["unit"].strip():
        return False
    if "label" in criterion and (not isinstance(criterion["label"], str) or not criterion["label"].strip()):
        return False
    allowed = {"operator", "unit", "label"}
    required: set[str]
    if operator in {"LT", "LTE"}:
        allowed.add("upper"); required = {"upper"}
    elif operator in {"GT", "GTE"}:
        allowed.add("lower"); required = {"lower"}
    else:
        allowed.update({"lower", "upper"}); required = {"lower", "upper"}
    return set(criterion) <= allowed and required <= set(criterion)


def _unknown(reason: str) -> dict[str, Any]:
    return {"status": "UNKNOWN", "value": None, "unit": None, "criterion_label": None, "reason": reason, "source": None, "meets_criterion": None}
