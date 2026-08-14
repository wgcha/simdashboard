from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request

from ....adapters.persistence.requests import SQLRequestReassignmentUnitOfWorkProvider
from ....application.requests.commands import reassign_request as reassign_request_command
from ....database_connection import ConnectionLike
from ....domains.requests.models import (
    RequestAssignee,
    RequestAssignmentError,
    RequestNotFoundError,
    RequestReassignmentAudit,
)
from ....modules.access_control import REQUEST_EDIT, require_permission, resolve_project_assignee
from ....schemas.api import AssigneeUpdate


router = APIRouter()


@router.patch("/api/requests/{request_id}/assignee")
def reassign_request(request_id: str, payload: AssigneeUpdate, request: Request) -> dict[str, Any]:
    principal = request.state.principal

    def authorize(project_id: str, connection: ConnectionLike) -> object:
        return require_permission(request, REQUEST_EDIT, project_id, conn=connection)

    def resolve_assignee(project_id: str, owner_user_id: str, connection: ConnectionLike) -> RequestAssignee:
        try:
            assignee = resolve_project_assignee(connection, project_id, owner_user_id)
        except HTTPException as error:
            # Existing resolver errors are 422 with an exact detail payload;
            # carry that data through the application boundary without making
            # the persistence adapter depend on FastAPI.
            raise RequestAssignmentError(error.detail) from error
        return {"user_id": assignee.user_id, "display_name": assignee.display_name}

    audit: RequestReassignmentAudit = {
        "user_id": principal.user_id,
        "username": principal.username,
        "role": principal.role,
        "method": "PATCH",
        "path": request.url.path,
        "status_code": 200,
        "request_id": getattr(request.state, "request_id", str(uuid4())),
        "client_ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent", "")[:500],
    }
    try:
        return reassign_request_command(
            request_id,
            payload.owner_user_id,
            audit,
            SQLRequestReassignmentUnitOfWorkProvider(authorize, resolve_assignee),
        )
    except RequestNotFoundError as error:
        raise HTTPException(404, detail={"code": "REQUEST_NOT_FOUND", "request_id": error.request_id}) from error
    except RequestAssignmentError as error:
        raise HTTPException(422, detail=error.detail) from error
