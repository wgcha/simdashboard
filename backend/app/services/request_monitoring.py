from __future__ import annotations

from typing import Any

from ..database import json_value, rows


def summarize_request_steps(steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the canonical request-milestone summary used by every dashboard.

    Request milestones intentionally remain separate from workbench task runs.  The
    legacy progress policy (arithmetic mean) is preserved so existing requests do
    not jump when the workbench catalog is introduced.
    """

    progress = round(sum(int(step.get("progress") or 0) for step in steps) / len(steps)) if steps else 0
    required = [step for step in steps if not step.get("is_optional")]
    statuses = {str(step.get("status") or "WAITING") for step in steps}
    if "FAILED" in statuses:
        status = "FAILED"
    elif "BLOCKED" in statuses:
        status = "BLOCKED"
    elif required and all(step.get("status") == "COMPLETED" for step in required):
        status = "COMPLETED"
    elif any(step.get("status") in {"IN_PROGRESS", "COMPLETED"} or int(step.get("progress") or 0) > 0 for step in steps):
        status = "IN_PROGRESS"
    else:
        status = "READY"

    current = next((step for step in steps if step.get("status") in {"FAILED", "BLOCKED"}), None)
    current = current or next((step for step in steps if step.get("status") == "IN_PROGRESS"), None)
    current = current or next((step for step in required if step.get("status") != "COMPLETED"), None)
    current = current or (steps[-1] if steps else None)
    return {
        "status": status,
        "progress": progress,
        "current_step": current.get("name") if current else None,
        "current_step_id": current.get("id") if current else None,
    }


def request_monitoring_summary(conn: Any, request_id: str) -> dict[str, Any]:
    plans = rows(conn.execute("SELECT * FROM request_work_plans WHERE request_id=?", [request_id]))
    work_plan = None
    if plans:
        work_plan = plans[0]
        work_plan["definition_snapshot"] = json_value(work_plan.pop("definition_snapshot_json")) or {"nodes": []}
        work_items = rows(conn.execute("SELECT * FROM request_work_items WHERE request_id=? ORDER BY sequence_no", [request_id]))
        completed_count = sum(1 for item in work_items if item["status"] == "COMPLETED")
        total_count = len(work_items)
        progress = round(completed_count / total_count * 100) if total_count else 0
        if total_count and completed_count == total_count:
            status = "COMPLETED"
        elif any(item["status"] == "IN_PROGRESS" for item in work_items) or completed_count:
            status = "IN_PROGRESS"
        else:
            status = "READY"
        current = None
        if status != "COMPLETED":
            current = next((item for item in work_items if item["status"] == "IN_PROGRESS"), None)
            current = current or next((item for item in work_items if item["status"] == "READY"), None)
            current = current or next((item for item in work_items if item["status"] == "WAITING"), None)
        steps = [
            {
                **item,
                "name": item["display_name"],
                "progress": 100 if item["status"] == "COMPLETED" else 0,
                "is_optional": False,
                "note": "",
                "blocked_reason": None,
            }
            for item in work_items
        ]
        work_summary = {
            "status": status,
            "progress": progress,
            "current_step": current["display_name"] if current else None,
            "current_step_id": current["id"] if current else None,
            "completed_count": completed_count,
            "total_count": total_count,
            "work_plan": work_plan,
        }
    else:
        steps = rows(conn.execute("SELECT * FROM request_steps WHERE request_id = ? ORDER BY sequence_no", [request_id]))
        legacy = summarize_request_steps(steps)
        work_summary = {**legacy, "completed_count": None, "total_count": None, "work_plan": None}
    latest_runs = rows(
        conn.execute(
            """
            SELECT id, name, execution_mode, status, progress, created_at, completed_at
            FROM workflow_runs
            WHERE request_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            [request_id],
        )
    )
    assignments = rows(
        conn.execute(
            """
            SELECT request_type_id, request_type_version, source, decided_by, decided_at
            FROM analysis_request_type_assignments WHERE request_id=?
            """,
            [request_id],
        )
    )
    return {
        **work_summary,
        "steps": steps,
        "latest_demo_run": latest_runs[0] if latest_runs else None,
        "request_type_assignment": assignments[0] if assignments else None,
    }


def sync_request_status(conn: Any, request_id: str, steps: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    has_plan = bool(conn.execute("SELECT 1 FROM request_work_plans WHERE request_id=?", [request_id]).fetchone())
    summary = request_monitoring_summary(conn, request_id) if has_plan or steps is None else summarize_request_steps(steps)
    conn.execute("UPDATE analysis_requests SET status = ? WHERE id = ?", [summary["status"], request_id])
    return summary
