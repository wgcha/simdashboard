from __future__ import annotations

from typing import Any


def workflow_payload(request: dict[str, Any], monitoring: dict[str, Any]) -> dict[str, Any]:
    request["status"] = monitoring["status"]
    return {
        "request": request,
        "steps": monitoring["steps"],
        "progress": monitoring["progress"],
        "current_step": monitoring["current_step"],
        "current_step_id": monitoring["current_step_id"],
        "completed_count": monitoring["completed_count"],
        "total_count": monitoring["total_count"],
        "work_plan": monitoring["work_plan"],
        "latest_demo_run": monitoring["latest_demo_run"],
        "request_type_assignment": monitoring["request_type_assignment"],
    }
