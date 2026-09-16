"""Pure helpers for dashboard variable binding settings."""

from __future__ import annotations

from typing import Any


MULTI_VARIABLE_WIDGET_TYPES = frozenset(
    {"result_table", "name_value", "edge_bar", "time_series", "chassis_bar", "chassis_table"}
)


def validate_widget_variable_binding_shape(widget: dict[str, Any]) -> None:
    settings = widget.get("settings") or {}
    if not isinstance(settings, dict):
        raise ValueError("위젯 설정은 객체여야 합니다.")
    if "variableIds" not in settings:
        return
    if widget.get("type") not in MULTI_VARIABLE_WIDGET_TYPES:
        raise ValueError("이 위젯 유형은 여러 변수를 지원하지 않습니다.")
    variable_ids = settings["variableIds"]
    if not isinstance(variable_ids, list):
        raise ValueError("variableIds는 문자열 배열이어야 합니다.")
    if any(not isinstance(value, str) or not value.strip() for value in variable_ids):
        raise ValueError("variableIds에는 비어 있지 않은 변수 키만 지정할 수 있습니다.")
    if len(variable_ids) != len(set(variable_ids)):
        raise ValueError("variableIds에는 중복 변수가 포함될 수 없습니다.")


def referenced_variable_keys(widget: dict[str, Any]) -> list[str]:
    """Return effective keys while tolerating malformed old snapshots."""
    settings = widget.get("settings") or {}
    if not isinstance(settings, dict):
        return []
    variable_ids = settings.get("variableIds")
    if isinstance(variable_ids, list):
        return [value for value in variable_ids if isinstance(value, str)]
    variable_id = settings.get("variableId")
    return [variable_id] if isinstance(variable_id, str) else []


def has_variable_bindings(definition: dict[str, Any]) -> bool:
    return any(
        isinstance(widget, dict)
        and isinstance(widget.get("settings"), dict)
        and isinstance(widget["settings"].get("variableIds"), list)
        and bool(referenced_variable_keys(widget))
        for widget in definition.get("widgets", [])
    )


def has_variable_ids(definition: dict[str, Any]) -> bool:
    return any(
        isinstance(widget, dict)
        and isinstance(widget.get("settings"), dict)
        and "variableIds" in widget["settings"]
        for widget in definition.get("widgets", [])
    )


def validate_dashboard_variable_binding_shapes(definition: dict[str, Any]) -> None:
    for widget in definition.get("widgets", []):
        if isinstance(widget, dict) and isinstance(widget.get("settings"), dict) and "variableIds" in widget["settings"]:
            validate_widget_variable_binding_shape(widget)
