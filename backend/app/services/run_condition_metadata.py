"""Bounded validation for optional run-condition metadata in import manifests."""

from __future__ import annotations

import json
import math
from typing import Any

from ..folder_import import FolderImportError

MAX_RUN_CONDITIONS = 64
MAX_RUN_CONDITIONS_BYTES = 16 * 1024
MAX_RUN_CONDITIONS_DEPTH = 6
RUN_CONDITIONS_INVALID = "RUN_CONDITIONS_INVALID"


def validate_run_conditions(value: Any) -> dict[str, Any] | None:
    """Return a detached, bounded JSON object or raise a controlled import error."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _invalid("metadata.run_conditions는 객체여야 합니다.")
    if len(value) > MAX_RUN_CONDITIONS:
        raise _invalid("metadata.run_conditions 조건 개수가 허용 한도를 초과했습니다.")
    _validate_json(value, depth=1)
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _invalid("metadata.run_conditions는 JSON 값만 포함할 수 있습니다.") from exc
    if len(encoded) > MAX_RUN_CONDITIONS_BYTES:
        raise _invalid("metadata.run_conditions 크기가 허용 한도를 초과했습니다.")
    return json.loads(encoded.decode("utf-8"))


def _validate_json(value: Any, *, depth: int) -> None:
    if depth > MAX_RUN_CONDITIONS_DEPTH:
        raise _invalid("metadata.run_conditions 중첩 깊이가 허용 한도를 초과했습니다.")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _invalid("metadata.run_conditions에는 유한한 숫자만 사용할 수 있습니다.")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise _invalid("metadata.run_conditions 객체 키는 문자열이어야 합니다.")
            _validate_json(item, depth=depth + 1)
        return
    if isinstance(value, list):
        for item in value:
            _validate_json(item, depth=depth + 1)
        return
    raise _invalid("metadata.run_conditions는 JSON 값만 포함할 수 있습니다.")


def _invalid(message: str) -> FolderImportError:
    return FolderImportError(message, code=RUN_CONDITIONS_INVALID)
