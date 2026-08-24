from __future__ import annotations

"""Compile request-type result widget tags into the internal dashboard contract."""

import hashlib
from typing import Any

from ..schemas.api import DashboardDefinition
from .identifiers import slugify


def internal_template_id(request_type_id: str) -> str:
    """Return a deterministic, private template family id for a request type."""
    readable = slugify(request_type_id, fallback="request-type")[:48]
    digest = hashlib.sha1(request_type_id.encode("utf-8")).hexdigest()[:10]
    return f"request-result-{readable}-{digest}"


def _canonical_contracts(values: list[str]) -> list[str]:
    return list(
        dict.fromkeys(
            value.strip().upper() for value in values if isinstance(value, str) and value.strip()
        )
    )


def _widget_size(widget_type: str) -> tuple[int, int]:
    if widget_type in {"time_series", "scatter", "result_table", "contour", "video", "video_grid", "model3d", "run_comparison"}:
        return 12, 5
    return 6, 3


def compile_request_result_definition(
    definition: dict[str, Any],
    *,
    request_type_id: str,
    request_type_display_name: str,
) -> dict[str, Any]:
    """Create one valid 12-column dashboard page and its matching profile input."""
    widgets: list[dict[str, Any]] = []
    x = 0
    y = 0
    contracts: list[str] = []
    for tag in definition["widgets"]:
        tag = dict(tag)
        width, height = _widget_size(str(tag["type"]))
        if width == 12:
            if x:
                y += 3
                x = 0
        elif x + width > 12:
            y += 3
            x = 0
        tag_contracts = _canonical_contracts(tag.get("data_contracts") or [])
        contracts.extend(tag_contracts)
        settings: dict[str, Any] = {"data_contracts": tag_contracts, "required": bool(tag.get("required", False))}
        if tag.get("variable_key"):
            settings["variable_key"] = tag["variable_key"]
        widgets.append(
            {
                "id": tag["id"],
                "type": tag["type"],
                "title": tag["title"],
                "x": x,
                "y": y,
                "w": width,
                "h": height,
                "settings": settings,
            }
        )
        if width == 12:
            y += height
        else:
            x += width
            if x == 12:
                x = 0
                y += height

    page = DashboardDefinition.model_validate(
        {
            "id": f"{internal_template_id(request_type_id)}-page",
            "name": definition.get("page_name") or f"{request_type_display_name} 결과",
            "description": definition.get("page_description", ""),
            "widgets": widgets,
        }
    ).model_dump()
    selected_ids = [widget["id"] for widget in widgets]
    return {
        "template": {
            "id": internal_template_id(request_type_id),
            "display_name": f"{request_type_display_name} 결과",
            "description": definition.get("page_description", ""),
            "page_definitions": [page],
            "lifecycle_status": "PUBLISHED",
            "scope_kind": "SYSTEM",
            "project_id": None,
        },
        "profile": {
            "included_widget_ids": selected_ids,
            "overrides": {},
            "required_data_contracts": _canonical_contracts(contracts),
        },
    }
