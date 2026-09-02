from __future__ import annotations

from typing import Any, NoReturn
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request

from ....adapters.persistence.result_review import SQLResultReviewRepositoryProvider
from ....application.result_review.commands import (
    create_review_item as create_review_item_command,
    update_review_item as update_review_item_command,
)
from ....application.result_review.queries import list_review_items as list_review_items_query
from ....database_connection import ConnectionLike
from ....domains.result_review.errors import ResultReviewError
from ....domains.result_review.models import ResultReviewAuditContext
from ....modules.access_control import RESULT_REVIEW, require_resource_permission
from ....schemas.api import ReviewItemCreate, ReviewItemUpdate
from ....security import write_audit_event


router = APIRouter()


def _authorizer(request: Request):
    def authorize(resource_type: str, resource_id: str, connection: ConnectionLike) -> str:
        context = require_resource_permission(
            request,
            RESULT_REVIEW,
            resource_type,
            resource_id,
            conn=connection,
        )
        return context.project_id

    return authorize


def _audit(request: Request) -> ResultReviewAuditContext:
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


def _audit_writer(request: Request):
    principal = request.state.principal

    def write(audit: Any, connection: ConnectionLike) -> None:
        write_audit_event(
            request=request,
            principal=principal,
            status_code=audit["status_code"],
            action=audit["action"],
            detail=audit.get("detail", {}),
            connection=connection,
        )

    return write


def _raise_mapped(error: ResultReviewError) -> NoReturn:
    status = 404 if isinstance(error, LookupError) else 422
    raise HTTPException(status, error.message) from error


@router.get("/api/analysis-runs/{run_id}/review-items")
def list_review_items(run_id: str) -> list[dict[str, Any]]:
    try:
        return list_review_items_query(run_id, SQLResultReviewRepositoryProvider())
    except ResultReviewError as error:
        _raise_mapped(error)


@router.post("/api/analysis-runs/{run_id}/review-items", status_code=201)
def create_review_item(run_id: str, payload: ReviewItemCreate, request: Request) -> dict[str, Any]:
    try:
        return create_review_item_command(
            run_id=run_id,
            title=payload.title,
            body=payload.body,
            variable_key=payload.variable_key,
            time_value=payload.time_value,
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            review_status=payload.review_status,
            actor_name=request.state.principal.display_name,
            audit=_audit(request),
            repository_provider=SQLResultReviewRepositoryProvider(
                authorize=_authorizer(request),
                audit_writer=_audit_writer(request),
            ),
        )
    except ResultReviewError as error:
        _raise_mapped(error)


@router.patch("/api/review-items/{annotation_id}")
def update_review_item(annotation_id: str, payload: ReviewItemUpdate, request: Request) -> dict[str, Any]:
    try:
        return update_review_item_command(
            annotation_id=annotation_id,
            body=payload.body,
            review_status=payload.review_status,
            audit=_audit(request),
            repository_provider=SQLResultReviewRepositoryProvider(
                authorize=_authorizer(request),
                audit_writer=_audit_writer(request),
            ),
        )
    except ResultReviewError as error:
        _raise_mapped(error)
