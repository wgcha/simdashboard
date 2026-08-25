"""HTTP boundary for refreshing the configured master result folder."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request

from ..adapters.persistence.result_import_history import (
    IMPORT_JOB_STATUSES,
    MASTER_FOLDER_SOURCE_TYPE,
    ImportJobStatus,
    SQLResultImportHistoryRepository,
)
from ..modules.access_control import (
    RESULT_IMPORT,
    SYSTEM_CATALOG_MANAGE,
    require_permission,
    require_resource_permission,
)
from ..database_connection import connect
from ..schemas.result_folder_refresh import (
    MasterResultRefreshItem,
    MasterResultRefreshResponse,
    ResultImportHistoryResponse,
)
from ..security import write_audit_event
from ..services.master_result_refresh import MasterResultRefreshError, MasterResultRefreshService


router = APIRouter(prefix="/api", tags=["result-folder-refresh"])


@router.post("/result-imports/refresh", response_model=MasterResultRefreshResponse)
def refresh_master_result_folder(request: Request) -> MasterResultRefreshResponse:
    """Discover and import trusted result bundles; client paths are never accepted."""
    # This is deliberately an installation-wide capability.  A project manager
    # must not be able to trigger scans that also reveal other projects' errors.
    with connect() as conn:
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
    try:
        items = MasterResultRefreshService().refresh()
    except MasterResultRefreshError as exc:
        raise HTTPException(503, str(exc)) from exc

    response = MasterResultRefreshResponse(
        scanned_count=len(items),
        imported_count=sum(item.status == "IMPORTED" for item in items),
        skipped_count=sum(item.status == "SKIPPED" for item in items),
        failed_count=sum(item.status == "FAILED" for item in items),
        items=[MasterResultRefreshItem(**item.__dict__) for item in items],
    )
    with connect() as conn:
        write_audit_event(
            request=request,
            principal=request.state.principal,
            status_code=200,
            action="MASTER_RESULT_FOLDER_REFRESHED",
            detail={
                "scanned_count": response.scanned_count,
                "imported_count": response.imported_count,
                "skipped_count": response.skipped_count,
                "failed_count": response.failed_count,
            },
            connection=conn,
        )
    return response


@router.get("/load-cases/{load_case_id}/result-imports", response_model=ResultImportHistoryResponse)
def list_result_import_history(
    load_case_id: str,
    request: Request,
    status: ImportJobStatus | None = Query(default=None),
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ResultImportHistoryResponse:
    """List every import attempt visible to a load-case result importer."""
    # Keep an explicit runtime whitelist in addition to FastAPI's typed query
    # validation so this boundary is safe if invoked directly in tests/tools.
    if status is not None and status not in IMPORT_JOB_STATUSES:
        raise HTTPException(422, "지원하지 않는 result import 상태입니다.")
    with connect() as conn:
        require_resource_permission(request, RESULT_IMPORT, "load_case", load_case_id, conn=conn)
        history = SQLResultImportHistoryRepository(conn).list_for_load_case(
            load_case_id,
            status=status,
            limit=limit,
            offset=offset,
        )
    return ResultImportHistoryResponse(**history)


@router.post("/result-imports/{job_id}/retry", response_model=MasterResultRefreshItem)
def retry_result_import(job_id: str, request: Request) -> MasterResultRefreshItem:
    """Retry one eligible master-folder manifest without accepting a client path."""
    with connect() as conn:
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
        job = SQLResultImportHistoryRepository(conn).get(job_id)
        if job is None:
            raise HTTPException(404, "결과 가져오기 이력을 찾을 수 없습니다.")
        # Resolve the job's load case before evaluating retry eligibility; this
        # prevents a catalog operator from probing another project's job IDs.
        require_resource_permission(request, RESULT_IMPORT, "load_case", str(job["load_case_id"]), conn=conn)
        retryable = (
            job["source_type"] == MASTER_FOLDER_SOURCE_TYPE
            and job["status"] in {"FAILED", "REJECTED"}
            and _is_stored_manifest_relative(job["source_folder"])
        )
        if not retryable:
            raise HTTPException(
                409,
                {"code": "RESULT_IMPORT_NOT_RETRYABLE"},
            )
        expected_target = (
            str(job["project_id"]),
            str(job["request_id"]),
            str(job["load_case_id"]),
        )
    try:
        item = MasterResultRefreshService().retry_manifest(
            str(job["source_folder"]),
            expected_target=expected_target,
        )
    except MasterResultRefreshError as exc:
        raise HTTPException(503, str(exc)) from exc
    response = MasterResultRefreshItem(**item.__dict__)
    with connect() as conn:
        write_audit_event(
            request=request,
            principal=request.state.principal,
            status_code=200,
            action="MASTER_RESULT_IMPORT_RETRIED",
            detail={
                "job_id": job_id,
                "load_case_id": job["load_case_id"],
                "manifest_path": job["source_folder"],
                "status": response.status,
                "operation": response.operation,
                "reason_code": response.reason_code,
            },
            connection=conn,
        )
    return response


def _is_stored_manifest_relative(value: object) -> bool:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        return False
    parts = value.split("/")
    return parts[-1] == "manifest.json" and all(part not in {"", ".", ".."} for part in parts)
