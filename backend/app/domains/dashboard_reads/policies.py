from __future__ import annotations

from typing import Any


def analysis_page_meta(definition: dict[str, Any]) -> dict[str, Any] | None:
    """Return the analysis-page metadata when a dashboard is an analysis page."""

    page = definition.get("page")
    if not isinstance(page, dict) or page.get("kind") != "analysis_page":
        return None
    return page
