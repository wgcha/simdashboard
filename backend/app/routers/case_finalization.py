"""Scoped APIs for confirming one captured Case into its request Final folders."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..database_connection import connect
from ..modules.access_control import PROJECT_DATA_VIEW, RESULT_IMPORT, require_resource_permission
from ..security import write_audit_event
from ..services import case_finalization as service

router = APIRouter(prefix="/api/dashboard/finalizations", tags=["case-finalization"])


class FinalizationInput(BaseModel):
    project_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    environment: Literal["USAGE", "DISTRIBUTION"]
    case_id: str = Field(min_length=1, max_length=128)
    capture_id: str = Field(min_length=1, max_length=128)


class ConfirmInput(FinalizationInput):
    operation_id: str = Field(pattern=r"^[0-9a-f]{32}$")


def _error(exc: service.CaseFinalizationError) -> HTTPException:
    if exc.code.endswith("NOT_FOUND") or exc.code == "FINALIZATION_PLAN_NOT_FOUND":
        status = 404
    elif any(token in exc.code for token in ("SCOPE", "PERMISSION", "OWNERSHIP")):
        status = 403 if "SCOPE" in exc.code else 409
    else:
        status = 409 if any(token in exc.code for token in ("STALE", "CHANGED", "CONFLICT", "INCOMPATIBLE", "UNCONFIRMED", "UNTRUSTED")) else 422
    return HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)})


@router.post("/preview")
def preview(payload: FinalizationInput, request: Request):
    with connect() as conn:
        require_resource_permission(request, RESULT_IMPORT, "request", payload.request_id, conn=conn)
        try:
            result = service.preview(
                conn, project_id=payload.project_id, request_id=payload.request_id,
                environment=payload.environment, case_id=payload.case_id,
                capture_id=payload.capture_id, actor=request.state.principal.user_id,
            )
        except service.CaseFinalizationError as exc:
            raise _error(exc) from exc
        return result


@router.post("/confirm")
def confirm(payload: ConfirmInput, request: Request):
    with connect() as conn:
        require_resource_permission(request, RESULT_IMPORT, "request", payload.request_id, conn=conn)
        try:
            result = service.confirm(
                conn, project_id=payload.project_id, request_id=payload.request_id,
                environment=payload.environment, case_id=payload.case_id,
                capture_id=payload.capture_id, operation_id=payload.operation_id,
                actor=request.state.principal.user_id,
            )
        except service.CaseFinalizationError as exc:
            raise _error(exc) from exc
        write_audit_event(
            request=request, principal=request.state.principal, status_code=200,
            action="CASE_FINALIZATION_CONFIRMED",
            detail={"project_id": payload.project_id, "request_id": payload.request_id,
                    "case_id": payload.case_id, "capture_id": payload.capture_id,
                    "operation_id": payload.operation_id},
            connection=conn,
        )
        return result


@router.get("/status")
def status(request: Request, project_id: str = Query(min_length=1, max_length=128),
           request_id: str = Query(min_length=1, max_length=128),
           environment: Literal["USAGE", "DISTRIBUTION"] = Query(...),
           case_id: str = Query(min_length=1, max_length=128)):
    with connect() as conn:
        require_resource_permission(request, PROJECT_DATA_VIEW, "request", request_id, conn=conn)
        try:
            return service.status(conn, project_id=project_id, request_id=request_id,
                                  environment=environment, case_id=case_id)
        except service.CaseFinalizationError as exc:
            raise _error(exc) from exc
