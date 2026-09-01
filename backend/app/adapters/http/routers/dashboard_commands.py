from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from ....application.dashboard_commands.queries import preview_dashboard_command as preview_dashboard_command_query
from ....modules.access_control import DASHBOARD_EDIT, require_permission
from ....schemas.api import NaturalLanguageCommand


router = APIRouter()


@router.post("/api/dashboard-commands/preview")
def preview_dashboard_command(
    payload: NaturalLanguageCommand,
    request: Request,
    project_id: str | None = Query(default=None),
) -> dict[str, Any]:
    # Authorization must remain the first operation before command processing.
    require_permission(request, DASHBOARD_EDIT, project_id)
    return preview_dashboard_command_query(payload.command)
