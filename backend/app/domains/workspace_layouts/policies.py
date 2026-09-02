from __future__ import annotations

import re
from typing import Any

from .errors import UnsupportedWorkspaceLayoutKindError, WorkspaceLayoutValidationError


WORKSPACE_LAYOUT_KINDS = frozenset({"portfolio", "workflow"})


def _validated_workspace_layout(kind: str, definition: dict[str, Any]) -> dict[str, Any]:
    """Preserve the established workspace-layout normalization contract."""
    if kind not in WORKSPACE_LAYOUT_KINDS:
        raise UnsupportedWorkspaceLayoutKindError()
    font_size = definition.get("fontSize")
    if not isinstance(font_size, int) or not 8 <= font_size <= 18:
        raise WorkspaceLayoutValidationError("레이아웃 글자 크기는 8~18 사이의 정수여야 합니다.")
    if kind == "portfolio":
        chart_order = definition.get("chartOrder")
        expected = {"trend", "status", "quality", "type"}
        if not isinstance(chart_order, list) or len(chart_order) != len(expected) or set(chart_order) != expected:
            raise WorkspaceLayoutValidationError("운영 대시보드 차트 순서가 올바르지 않습니다.")
        return {"fontSize": font_size, "chartOrder": chart_order}
    accent = definition.get("accentColor")
    if not isinstance(accent, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", accent):
        raise WorkspaceLayoutValidationError("워크플로 강조 색상은 #을 포함한 6자리 HEX여야 합니다.")
    items = definition.get("items")
    if not isinstance(items, list):
        raise WorkspaceLayoutValidationError("워크플로 레이아웃 items 배열이 필요합니다.")
    normalized_items = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("requestId"), str):
            raise WorkspaceLayoutValidationError("워크플로 레이아웃 항목에는 requestId가 필요합니다.")
        coordinates = {key: item.get(key) for key in ("x", "y", "w", "h")}
        if any(not isinstance(value, int) for value in coordinates.values()):
            raise WorkspaceLayoutValidationError("워크플로 레이아웃 위치와 크기는 정수여야 합니다.")
        normalized_items.append({"requestId": item["requestId"], **coordinates})
    return {"fontSize": font_size, "accentColor": accent, "items": normalized_items}
