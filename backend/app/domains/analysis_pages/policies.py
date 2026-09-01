from __future__ import annotations

from typing import Any


SYSTEM_ANALYSIS_PAGE_IDS = frozenset(
    {"dashboard-drop-default", "dashboard-chassis-default", "dashboard-run-comparison-default"}
)


def analysis_page_meta(definition: dict[str, Any]) -> dict[str, Any] | None:
    """Return analysis-page metadata, preserving malformed-data behavior."""

    page = definition.get("page")
    if not isinstance(page, dict) or page.get("kind") != "analysis_page":
        return None
    return page


def _is_allowed_page(item: dict[str, Any], page: dict[str, Any], load_case_id: str) -> tuple[bool, bool]:
    """Return (is_allowed, is_system) for one candidate row."""

    is_system = item["id"] in SYSTEM_ANALYSIS_PAGE_IDS and page.get("is_system") is True
    is_current_custom = (
        item.get("load_case_id") == load_case_id
        and page.get("analysis_key") == "custom"
        and page.get("is_system") is False
    )
    return is_system or is_current_custom, is_system


def summarize_analysis_page(
    item: dict[str, Any],
    definition: dict[str, Any],
    *,
    load_case_id: str,
    include_private: bool,
    include_archived: bool,
) -> dict[str, Any] | None:
    """Apply list visibility policy and build the public summary shape."""

    page = analysis_page_meta(definition)
    if not page:
        return None
    allowed, is_system = _is_allowed_page(item, page, load_case_id)
    if not allowed:
        return None
    status = page.get("status")
    if not include_private and not is_system and status != "published":
        return None
    if not include_archived and status == "archived":
        return None
    return {
        "id": item["id"],
        "project_id": item["project_id"],
        "request_id": item.get("request_id"),
        "load_case_id": item.get("load_case_id"),
        "name": item["name"],
        "description": item.get("description") or "",
        "version": item["version"],
        "updated_at": item["updated_at"],
        "page": page,
    }


def sort_analysis_pages(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: (item["page"]["display_order"], item["name"].casefold(), item["id"]))
