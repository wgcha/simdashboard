"""Validation for immutable result-criterion snapshots in import manifests."""

from __future__ import annotations

import json
import math
from numbers import Real
from typing import Any

from ..folder_import import FolderImportError

RESULT_CRITERIA_INVALID = "RESULT_CRITERIA_INVALID"
_OPERATORS = {"LT", "LTE", "GT", "GTE", "BETWEEN"}


def validate_result_criteria(value: Any) -> dict[str, dict[str, Any]] | None:
    """Return a detached snapshot; reject incomplete or ambiguous boundaries."""
    if value is None:
        return None
    if not isinstance(value, dict) or len(value) > 128:
        raise _invalid("metadata.result_criteria는 제한된 객체여야 합니다.")
    result: dict[str, dict[str, Any]] = {}
    for key, raw in value.items():
        if not isinstance(key, str) or not key.strip() or len(key) > 120 or not isinstance(raw, dict):
            raise _invalid("metadata.result_criteria 항목이 올바르지 않습니다.")
        operator, unit = raw.get("operator"), raw.get("unit")
        if not isinstance(operator, str) or operator not in _OPERATORS or not isinstance(unit, str) or not unit.strip() or len(unit.strip()) > 40:
            raise _invalid("metadata.result_criteria의 operator 또는 unit이 올바르지 않습니다.")
        allowed = {"operator", "unit", "label"}
        required: set[str]
        if operator in {"LT", "LTE"}:
            allowed.add("upper"); required = {"upper"}
        elif operator in {"GT", "GTE"}:
            allowed.add("lower"); required = {"lower"}
        else:
            allowed.update({"lower", "upper"}); required = {"lower", "upper"}
        if set(raw) - allowed or not required <= set(raw):
            raise _invalid("metadata.result_criteria 경계 정의가 operator와 일치하지 않습니다.")
        spec: dict[str, Any] = {"operator": operator, "unit": unit.strip()}
        label = raw.get("label")
        if label is not None:
            if not isinstance(label, str) or not label.strip() or len(label.strip()) > 240:
                raise _invalid("metadata.result_criteria의 label이 올바르지 않습니다.")
            spec["label"] = label.strip()
        for boundary in required:
            number = raw[boundary]
            if isinstance(number, bool) or not isinstance(number, Real):
                raise _invalid("metadata.result_criteria의 경계값은 유한한 숫자여야 합니다.")
            try:
                converted = float(number)
            except (OverflowError, ValueError) as exc:
                raise _invalid("metadata.result_criteria의 경계값은 유한한 숫자여야 합니다.") from exc
            if not math.isfinite(converted):
                raise _invalid("metadata.result_criteria의 경계값은 유한한 숫자여야 합니다.")
            spec[boundary] = converted
        if operator == "BETWEEN" and spec["lower"] > spec["upper"]:
            raise _invalid("BETWEEN 기준의 lower는 upper 이하여야 합니다.")
        normalized_key = key.strip()
        if normalized_key in result:
            raise _invalid("metadata.result_criteria에 중복된 변수 키가 있습니다.")
        result[normalized_key] = spec
    try:
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:  # pragma: no cover
        raise _invalid("metadata.result_criteria는 JSON 값만 포함할 수 있습니다.") from exc
    if len(encoded) > 16 * 1024:
        raise _invalid("metadata.result_criteria 크기가 허용 한도를 초과했습니다.")
    return json.loads(encoded.decode("utf-8"))


def _invalid(message: str) -> FolderImportError:
    return FolderImportError(message, code=RESULT_CRITERIA_INVALID)
