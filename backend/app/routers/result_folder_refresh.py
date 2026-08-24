"""HTTP boundary for refreshing the configured master result folder."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..modules.access_control import SYSTEM_CATALOG_MANAGE, require_permission
from ..database_connection import connect
from ..schemas.result_folder_refresh import MasterResultRefreshItem, MasterResultRefreshResponse
from ..security import write_audit_event
from ..services.master_result_refresh import MasterResultRefreshError, MasterResultRefreshService


router = APIRouter(prefix="/api/result-imports", tags=["result-folder-refresh"])


@router.post("/refresh", response_model=MasterResultRefreshResponse)
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
