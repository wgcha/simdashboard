from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ....adapters.persistence.projects import SQLProjectRepositoryProvider, SQLProjectUnitOfWorkProvider
from ....application.projects.commands import create_project as create_project_command, new_identifier, utc_now
from ....application.projects.queries import list_projects
from ....domains.projects.models import CreateProjectCommand, ProjectAuditContext
from ....modules.access_control import PROJECT_DATA_VIEW, SYSTEM_USER_APPROVE, require_permission
from ....schemas.api import ProjectCreate


router = APIRouter()


@router.get("/api/projects")
def get_projects(request: Request) -> list[dict[str, Any]]:
    return list_projects(
        lambda: require_permission(request, PROJECT_DATA_VIEW),
        SQLProjectRepositoryProvider(),
    )


@router.post("/api/projects", status_code=201)
def create_project(payload: ProjectCreate, request: Request) -> dict[str, Any]:
    principal = request.state.principal
    command: CreateProjectCommand = {
        **payload.model_dump(),
        "creator": {
            "user_id": principal.user_id,
            "username": principal.username,
            "role": principal.role,
        },
    }
    audit: ProjectAuditContext = {
        "user_id": principal.user_id,
        "username": principal.username,
        "role": principal.role,
        "action": "PROJECT_CREATED",
        "method": "POST",
        "path": "/api/projects",
        "status_code": 201,
        "request_id": request.state.request_id,
        "client_ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent", "")[:500],
    }
    return create_project_command(
        command,
        audit,
        lambda: require_permission(request, SYSTEM_USER_APPROVE),
        new_identifier,
        utc_now,
        SQLProjectUnitOfWorkProvider(new_identifier),
    )
