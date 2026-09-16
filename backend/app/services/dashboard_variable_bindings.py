"""Database-backed validation for dashboard variable bindings."""

from __future__ import annotations

import json
from typing import Any

from ..database import json_value
from ..database_connection import ConnectionLike
from ..domains.dashboard_writes.variable_bindings import (
    has_variable_bindings,
    has_variable_ids,
    referenced_variable_keys,
    validate_dashboard_variable_binding_shapes,
    validate_widget_variable_binding_shape,
)

__all__ = [
    "has_variable_bindings",
    "has_variable_ids",
    "referenced_variable_keys",
    "validate_dashboard_variable_binding_shapes",
    "validate_widget_variable_binding_shape",
    "validate_dashboard_variable_bindings",
]


def _catalog_widgets(value: Any) -> set[str]:
    value = json_value(value)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return set()
    return {item for item in value if isinstance(item, str)} if isinstance(value, list) else set()


def validate_dashboard_variable_bindings(
    connection: ConnectionLike,
    load_case_id: str | None,
    definition: dict[str, Any],
) -> None:
    """Validate new array bindings against active catalog definitions."""
    bindings: list[tuple[str, str]] = []
    for widget in definition.get("widgets", []):
        if not isinstance(widget, dict):
            continue
        settings = widget.get("settings") or {}
        if not isinstance(settings, dict) or "variableIds" not in settings:
            continue
        validate_widget_variable_binding_shape(widget)
        bindings.extend((str(widget.get("type", "")), key) for key in settings["variableIds"])

    if not bindings:
        return
    if not load_case_id:
        raise ValueError("변수에 연결된 대시보드는 하중 경우가 필요합니다.")

    catalog_rows = connection.execute(
        """
        SELECT variable_key, allowed_widgets_json
        FROM variable_definitions
        WHERE load_case_id = ? AND is_active = true
        """,
        [load_case_id],
    ).fetchall()
    catalog = {str(row[0]): _catalog_widgets(row[1]) for row in catalog_rows}
    unknown = sorted({key for _, key in bindings if key not in catalog})
    if unknown:
        raise ValueError(f"존재하지 않거나 비활성화된 변수가 포함되어 있습니다: {', '.join(unknown)}")
    incompatible = sorted({f"{widget_type}:{key}" for widget_type, key in bindings if widget_type not in catalog[key]})
    if incompatible:
        raise ValueError(f"위젯에서 사용할 수 없는 변수가 포함되어 있습니다: {', '.join(incompatible)}")
