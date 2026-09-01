from __future__ import annotations

from collections.abc import Callable
from typing import Any, NoReturn
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request

from ....adapters.persistence.quality_thresholds import SQLQualityThresholdRepositoryProvider
from ....application.quality_thresholds.commands import update_quality_threshold as update_quality_threshold_command
from ....application.quality_thresholds.queries import list_quality_thresholds as list_quality_thresholds_query
from ....database_connection import ConnectionLike
from ....domains.quality_thresholds.errors import (
    ProjectScopedThresholdUrlRequiredError,
    QualityThresholdError,
    QualityThresholdNotFoundError,
)
from ....domains.quality_thresholds.models import QualityThresholdAuditContext
from ....modules.access_control import PROJECT_THRESHOLD_MANAGE, require_permission
from ....schemas.api import QualityThresholdUpdate
from ....security import write_audit_event


router = APIRouter()


def _authorizer(request: Request) -> Callable[[str, ConnectionLike], None]:
    def authorize(project_id: str, connection: ConnectionLike) -> None:
        require_permission(request, PROJECT_THRESHOLD_MANAGE, project_id, conn=connection)

    return authorize


def _actor_name(request: Request) -> Callable[[], str]:
    return lambda: request.state.principal.display_name


def _audit(request: Request) -> Callable[[], QualityThresholdAuditContext]:
    def audit() -> QualityThresholdAuditContext:
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

    return audit


def _audit_writer(request: Request):
    def write(audit: Any, connection: ConnectionLike) -> None:
        write_audit_event(
            request=request,
            principal=request.state.principal,
            status_code=audit["status_code"],
            action=audit["action"],
            detail=audit.get("detail", {}),
            connection=connection,
        )

    return write


def _raise_mapped(error: QualityThresholdError) -> NoReturn:
    if isinstance(error, QualityThresholdNotFoundError):
        raise HTTPException(404, error.message) from error
    if isinstance(error, ProjectScopedThresholdUrlRequiredError):
        raise HTTPException(409, error.message) from error
    raise error


@router.get("/api/projects/{project_id}/quality-thresholds")
def get_quality_thresholds(project_id: str) -> list[dict[str, Any]]:
    return list_quality_thresholds_query(project_id, SQLQualityThresholdRepositoryProvider())


def _update_quality_threshold(
    criterion_key: str,
    payload: QualityThresholdUpdate,
    request: Request,
    expected_project_id: str | None = None,
) -> dict[str, Any]:
    try:
        return update_quality_threshold_command(
            criterion_key=criterion_key,
            threshold_double=payload.threshold_double,
            expected_project_id=expected_project_id,
            actor_name=_actor_name(request),
            audit=_audit(request),
            repository_provider=SQLQualityThresholdRepositoryProvider(
                authorize=_authorizer(request),
                audit_writer=_audit_writer(request),
            ),
        )
    except QualityThresholdError as error:
        _raise_mapped(error)


@router.put("/api/projects/{project_id}/quality-thresholds/{criterion_key}")
def update_project_quality_threshold(
    project_id: str,
    criterion_key: str,
    payload: QualityThresholdUpdate,
    request: Request,
) -> dict[str, Any]:
    return _update_quality_threshold(criterion_key, payload, request, expected_project_id=project_id)


@router.put("/api/quality-thresholds/{criterion_key}", deprecated=True)
def update_quality_threshold(
    criterion_key: str,
    payload: QualityThresholdUpdate,
    request: Request,
) -> dict[str, Any]:
    return _update_quality_threshold(criterion_key, payload, request)
