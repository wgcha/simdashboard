from __future__ import annotations

from typing import Any, Literal, NoReturn
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request

from ....adapters.persistence.workspace_layouts import SQLWorkspaceLayoutRepositoryProvider
from ....application.workspace_layouts.commands import save_project_workspace_layout as save_project_workspace_layout_command
from ....application.workspace_layouts.queries import (
    get_project_workspace_layout as get_project_workspace_layout_query,
    list_workspace_layout_versions,
)
from ....database_connection import ConnectionLike
from ....domains.workspace_layouts.errors import (
    UnsupportedWorkspaceLayoutKindError,
    WorkspaceLayoutError,
    WorkspaceLayoutNotFoundError,
    WorkspaceLayoutProjectNotFoundError,
    WorkspaceLayoutValidationError,
)
from ....domains.workspace_layouts.models import WorkspaceLayoutAuditContext
from ....modules.access_control import PROJECT_DATA_VIEW, PROJECT_LAYOUT_EDIT, require_permission
from ....schemas.api import WorkspaceLayoutResponse, WorkspaceLayoutUpdate, WorkspaceLayoutVersionResponse


router = APIRouter()


def _read_authorizer(request: Request):
    def authorize(project_id: str, connection: ConnectionLike) -> None:
        require_permission(request, PROJECT_DATA_VIEW, project_id, conn=connection)

    return authorize


def _write_authorizer(request: Request):
    def authorize(project_id: str, connection: ConnectionLike) -> None:
        require_permission(request, PROJECT_LAYOUT_EDIT, project_id, conn=connection)

    return authorize


def _audit(request: Request) -> WorkspaceLayoutAuditContext:
    principal = request.state.principal
    return {
        "user_id": principal.user_id,
        "username": principal.username,
        "role": principal.role,
        "method": request.method,
        "path": request.url.path,
        "request_id": getattr(request.state, "request_id", str(uuid4())),
        "client_ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent", "")[:500],
    }


def _raise_mapped(error: WorkspaceLayoutError) -> NoReturn:
    if isinstance(error, WorkspaceLayoutValidationError):
        raise HTTPException(422, error.message) from error
    if isinstance(
        error,
        (UnsupportedWorkspaceLayoutKindError, WorkspaceLayoutProjectNotFoundError, WorkspaceLayoutNotFoundError),
    ):
        raise HTTPException(404, error.message) from error
    raise error


@router.get("/api/projects/{project_id}/workspace-layouts/{layout_kind}", response_model=WorkspaceLayoutResponse)
def get_project_workspace_layout(
    project_id: str,
    layout_kind: Literal["portfolio", "workflow"],
    request: Request,
) -> dict[str, Any]:
    try:
        return get_project_workspace_layout_query(
            project_id,
            layout_kind,
            SQLWorkspaceLayoutRepositoryProvider(authorize_read=_read_authorizer(request)),
        )
    except WorkspaceLayoutError as error:
        _raise_mapped(error)


@router.get("/api/workspace-layouts/{layout_kind}", response_model=WorkspaceLayoutResponse, deprecated=True)
def get_workspace_layout(
    layout_kind: Literal["portfolio", "workflow"],
    request: Request,
    project_id: str = Query(min_length=3, max_length=120),
) -> dict[str, Any]:
    try:
        return get_project_workspace_layout_query(
            project_id,
            layout_kind,
            SQLWorkspaceLayoutRepositoryProvider(authorize_read=_read_authorizer(request)),
        )
    except WorkspaceLayoutError as error:
        _raise_mapped(error)


@router.put("/api/projects/{project_id}/workspace-layouts/{layout_kind}", response_model=WorkspaceLayoutResponse)
def save_project_workspace_layout(
    project_id: str,
    layout_kind: Literal["portfolio", "workflow"],
    payload: WorkspaceLayoutUpdate,
    request: Request,
) -> dict[str, Any]:
    try:
        return save_project_workspace_layout_command(
            project_id=project_id,
            layout_kind=layout_kind,
            definition=payload.definition,
            actor_name=request.state.principal.display_name,
            audit=_audit(request),
            repository_provider=SQLWorkspaceLayoutRepositoryProvider(authorize_write=_write_authorizer(request)),
        )
    except WorkspaceLayoutError as error:
        _raise_mapped(error)


@router.put("/api/workspace-layouts/{layout_kind}", response_model=WorkspaceLayoutResponse, deprecated=True)
def save_workspace_layout(
    layout_kind: Literal["portfolio", "workflow"],
    payload: WorkspaceLayoutUpdate,
    request: Request,
    project_id: str = Query(min_length=3, max_length=120),
) -> dict[str, Any]:
    try:
        return save_project_workspace_layout_command(
            project_id=project_id,
            layout_kind=layout_kind,
            definition=payload.definition,
            actor_name=request.state.principal.display_name,
            audit=_audit(request),
            repository_provider=SQLWorkspaceLayoutRepositoryProvider(authorize_write=_write_authorizer(request)),
        )
    except WorkspaceLayoutError as error:
        _raise_mapped(error)


@router.get(
    "/api/projects/{project_id}/workspace-layouts/{layout_kind}/versions",
    response_model=list[WorkspaceLayoutVersionResponse],
)
def get_workspace_layout_versions(
    project_id: str,
    layout_kind: Literal["portfolio", "workflow"],
    request: Request,
) -> list[dict[str, Any]]:
    try:
        return list_workspace_layout_versions(
            project_id,
            layout_kind,
            SQLWorkspaceLayoutRepositoryProvider(authorize_read=_read_authorizer(request)),
        )
    except WorkspaceLayoutError as error:
        _raise_mapped(error)
