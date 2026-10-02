"""Scoped APIs for confirming one Case result into its request Final folders.

Flow: ``POST /preview`` (signed plan) -> ``PUT /{operation_id}/reports/{pptx|html}``
(raw body, one request per browser-built report) -> ``POST /confirm`` with
``report_formats``. Reports use a separate raw-body upload step bound to the
operation (no multipart runtime dependency); each request carries one file.
"""
from __future__ import annotations

from tempfile import SpooledTemporaryFile
from typing import Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

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
    # ``latest:<dashboard_case_id>`` (merged latest result) or one stored capture id.
    capture_id: str = Field(min_length=1, max_length=160)


class ConfirmInput(FinalizationInput):
    operation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    # At least one is required (FINALIZATION_REPORT_REQUIRED); each must be uploaded first.
    report_formats: list[Literal["pptx", "html"]] = Field(default_factory=list, max_length=2)


def _error(exc: service.CaseFinalizationError) -> HTTPException:
    if exc.code in {"FINALIZATION_REPORT_TOO_LARGE", "FINALIZATION_OUTPUT_TOO_LARGE"}:
        status = 413
    elif exc.code == "FINALIZATION_ALREADY_COMPLETED":
        status = 409
    elif exc.code.endswith("NOT_FOUND") or exc.code == "FINALIZATION_PLAN_NOT_FOUND":
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
                report_formats=payload.report_formats, actor=request.state.principal.user_id,
            )
        except service.CaseFinalizationError as exc:
            raise _error(exc) from exc
        write_audit_event(
            request=request, principal=request.state.principal, status_code=200,
            action="CASE_FINALIZATION_CONFIRMED",
            detail={"project_id": payload.project_id, "request_id": payload.request_id,
                    "case_id": payload.case_id, "capture_id": payload.capture_id,
                    "operation_id": payload.operation_id,
                    "reports": [{"format": item["format"], "sha256": item["sha256"], "size": item["size"]}
                                for item in result.get("reports", [])]},
            connection=conn,
        )
        return result


@router.put("/{operation_id}/reports/{report_format}")
async def upload_report(
    request: Request,
    operation_id: str = Path(pattern=r"^[0-9a-f]{32}$"),
    report_format: Literal["pptx", "html"] = Path(...),
    project_id: str = Query(min_length=1, max_length=128),
    request_id: str = Query(min_length=1, max_length=128),
    environment: Literal["USAGE", "DISTRIBUTION"] = Query(...),
    case_id: str = Query(min_length=1, max_length=128),
    capture_id: str = Query(min_length=1, max_length=160),
):
    """Stage one browser-built report (raw body) for an unfinished operation."""
    limit = service.MAX_REPORT_BYTES[report_format]
    too_large = HTTPException(413, {"code": "FINALIZATION_REPORT_TOO_LARGE",
                                     "message": f"{report_format.upper()} 보고서는 {limit // (1024 * 1024)} MiB 이하여야 합니다."})

    def authorize() -> None:
        with connect() as conn:
            require_resource_permission(request, RESULT_IMPORT, "request", request_id, conn=conn)

    # Permission first, so an unauthorized caller cannot make the server spool a large body.
    await run_in_threadpool(authorize)
    declared = request.headers.get("content-length")
    if declared is not None and (not declared.isdigit() or int(declared) > limit):
        raise too_large
    with SpooledTemporaryFile(max_size=1024 * 1024) as upload:
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > limit:
                raise too_large
            upload.write(chunk)

        def stage() -> dict:
            with connect() as conn:
                require_resource_permission(request, RESULT_IMPORT, "request", request_id, conn=conn)
                try:
                    result = service.stage_report(
                        conn, project_id=project_id, request_id=request_id, environment=environment,
                        case_id=case_id, capture_id=capture_id, operation_id=operation_id,
                        report_format=report_format, upload=upload, actor=request.state.principal.user_id,
                    )
                except service.CaseFinalizationError as exc:
                    raise _error(exc) from exc
                write_audit_event(
                    request=request, principal=request.state.principal, status_code=200,
                    action="CASE_FINALIZATION_REPORT_STAGED",
                    detail={"project_id": project_id, "request_id": request_id, "case_id": case_id,
                            "operation_id": operation_id, "format": report_format,
                            "sha256": result["sha256"], "size": result["size"]},
                    connection=conn,
                )
                return result

        return await run_in_threadpool(stage)


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
