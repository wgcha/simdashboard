from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from ....adapters.persistence.project_assignees import SQLProjectAssigneeReaderProvider
from ....application.project_assignees.queries import list_assignee_candidates
from ....config import security_settings
from ....database_connection import ConnectionLike
from ....domains.project_assignees.models import ProjectNotFoundError
from ....modules.access_control import REQUEST_EDIT, require_permission


router = APIRouter()


@router.get("/api/projects/{project_id}/assignee-candidates")
def assignee_candidates(
    project_id: str,
    request: Request,
    q: str | None = Query(default=None, max_length=80),
) -> list[dict[str, Any]]:
    def authorize(project_id: str, connection: ConnectionLike) -> None:
        require_permission(request, REQUEST_EDIT, project_id, conn=connection)

    try:
        return list_assignee_candidates(
            project_id,
            q,
            exclude_local_admin=security_settings().auth_mode != "disabled",
            reader_provider=SQLProjectAssigneeReaderProvider(authorize),
        )
    except ProjectNotFoundError as error:
        raise HTTPException(404, "프로젝트를 찾을 수 없습니다.") from error
