from __future__ import annotations

"""Materialize an immutable request-result snapshot as an editable dashboard."""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from ..schemas.api import DashboardDefinition


def _decoded(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return json.loads(value)
    return dict(value or {})


def _dashboard_id(request_id: str, load_case_id: str, source_page_id: str) -> str:
    key = f"{request_id}:{load_case_id}:{source_page_id}".encode("utf-8")
    return f"dashboard-result-{hashlib.sha256(key).hexdigest()[:20]}"


def materialize_request_result_dashboard(
    conn: Any,
    *,
    request_id: str,
    load_case_id: str,
    page_id: str | None,
    created_by: str,
) -> dict[str, Any]:
    """Copy one snapshot page into a deterministic custom published dashboard."""
    request_row = conn.execute(
        "SELECT project_id FROM analysis_requests WHERE id=?", [request_id]
    ).fetchone()
    if not request_row:
        raise LookupError("REQUEST_NOT_FOUND")
    if not conn.execute(
        "SELECT 1 FROM load_cases WHERE id=? AND request_id=?", [load_case_id, request_id]
    ).fetchone():
        raise LookupError("LOAD_CASE_NOT_FOUND")
    snapshot_row = conn.execute(
        "SELECT snapshot_json FROM request_result_layout_snapshots WHERE request_id=?",
        [request_id],
    ).fetchone()
    if not snapshot_row:
        if page_id:
            raise LookupError("RESULT_LAYOUT_SNAPSHOT_NOT_FOUND")
        source_page = {"id": "unconfigured", "name": "요청 결과", "description": "", "widgets": []}
    else:
        snapshot = _decoded(snapshot_row[0])
        pages = snapshot.get("pages") if isinstance(snapshot.get("pages"), list) else []
        source_page = next((page for page in pages if page.get("id") == page_id), None) if page_id else (pages[0] if pages else None)
        if not isinstance(source_page, dict):
            raise LookupError("RESULT_LAYOUT_PAGE_NOT_FOUND")

    dashboard_id = _dashboard_id(request_id, load_case_id, str(source_page["id"]))
    existing = conn.execute(
        "SELECT project_id, request_id, load_case_id, definition_json FROM dashboards WHERE id=?",
        [dashboard_id],
    ).fetchone()
    if existing:
        if tuple(existing[:3]) != (request_row[0], request_id, load_case_id):
            raise ValueError("RESULT_LAYOUT_MATERIALIZATION_CONFLICT")
        return DashboardDefinition.model_validate(_decoded(existing[3])).model_dump()

    custom_orders = []
    for row in conn.execute("SELECT definition_json FROM dashboards WHERE load_case_id=?", [load_case_id]).fetchall():
        page = _decoded(row[0]).get("page")
        if isinstance(page, dict) and page.get("analysis_key") == "custom" and page.get("is_system") is False:
            custom_orders.append(int(page.get("display_order", 99)))
    name = f"{str(source_page.get('name') or '결과')} 사용자 편집"[:120]
    widgets = json.loads(json.dumps(source_page.get("widgets") or [], ensure_ascii=False))
    for widget in widgets:
        settings = widget.get("settings")
        if isinstance(settings, dict) and settings.get("variable_key") and not settings.get("variableId"):
            widget["settings"] = {**settings, "variableId": settings["variable_key"]}
    definition = DashboardDefinition.model_validate(
        {
            "id": dashboard_id,
            "name": name,
            "description": str(source_page.get("description") or ""),
            "widgets": widgets,
            "page": {
                "kind": "analysis_page",
                "analysis_key": "custom",
                "status": "published",
                "display_order": max([99, *custom_orders]) + 1,
                "is_system": False,
            },
        }
    ).model_dump()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    encoded = json.dumps(definition, ensure_ascii=False)
    conn.execute(
        "INSERT INTO dashboards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [dashboard_id, request_row[0], request_id, load_case_id, definition["name"], definition["description"], 1, encoded, now],
    )
    conn.execute(
        "INSERT INTO dashboard_versions VALUES (?, ?, ?, ?, ?, ?)",
        [dashboard_id, 1, encoded, created_by, now, True],
    )
    return definition
