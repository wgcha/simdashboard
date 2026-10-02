"""Separate API so legacy folder-discovery clients retain their contract."""
from typing import Literal
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from ..database_connection import connect
from ..security import write_audit_event
from ..modules.access_control import PROJECT_DATA_VIEW, RESULT_IMPORT, SYSTEM_CATALOG_MANAGE, require_permission, require_resource_permission
from ..services import (dashboard_capture, folder_auto_discovery, folder_auto_sync, folder_discovery as legacy,
                        folder_discovery_environment as service, result_registration)
from .semantic_body_limit import SemanticBodyLimitRoute

router = APIRouter(prefix="/api/folder-discovery/environments", tags=["folder-discovery-environments"], route_class=SemanticBodyLimitRoute)
class Scan(BaseModel):
    environment: Literal["USAGE", "DISTRIBUTION"]
    relative_path: str = Field(default="", max_length=1024)
    profile_id: str | None = None
    project_id: str | None = None
    request_id: str | None = None
class Assignment(BaseModel):
    node_id: str; role_kind: str; confirm: bool = True; target_mode: Literal["CREATE", "LINK"] = "CREATE"; target_id: str | None = None; propagate_same_level: bool = False
class Preview(BaseModel):
    scan_id: str
    assignments: list[Assignment] = Field(default_factory=list, max_length=5000)
    require_usage_review: bool = False
class Register(BaseModel): preview_id: str; idempotency_key: str = Field(min_length=8, max_length=128); capture: bool = True
class Refresh(BaseModel):
    project_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    environment: Literal["USAGE", "DISTRIBUTION"]
class Sync(Refresh):
    force: bool = False
class Discover(BaseModel):
    force: bool = False
class Retry(BaseModel): job_ids: list[str] | None = Field(default=None, max_length=500)
class UsageReview(BaseModel):
    case_relative_path: str = Field(min_length=1, max_length=1024)
    selection: dict = Field(default_factory=dict)
    selected_sources: dict[str, str] = Field(default_factory=dict)
    metric_paths: dict[str, list[str]] = Field(default_factory=dict)
    excludes: dict[str, str] = Field(default_factory=dict)
    acknowledge_partial: bool = False
def admin(request, conn):
    require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
    if not request.state.principal.is_global_admin:
        raise HTTPException(403, {"code": "GLOBAL_ADMIN_REQUIRED", "message": "환경 폴더 조사는 관리자 기능입니다."})
def scoped(request, conn, request_id):
    if request_id: require_resource_permission(request, RESULT_IMPORT, "request", request_id, conn=conn)
@router.get("")
def list_profiles(request: Request):
    with connect() as conn: admin(request, conn); return service.profiles(conn)
@router.post("/scan")
def scan(payload: Scan, request: Request):
    with connect() as conn:
        admin(request, conn); scoped(request, conn, payload.request_id)
        try: return service.save_scan(conn, legacy.configured_root(conn), legacy.normal(payload.relative_path), payload.environment, payload.profile_id, payload.project_id, payload.request_id, request.state.principal.user_id)
        except ValueError as exc: raise HTTPException(422, {"code":"ENVIRONMENT_SCAN_INVALID", "message":str(exc)}) from exc
@router.post("/sync")
def sync(payload: Sync, request: Request):
    """Keep a viewed request in step with its SPDM folders (screens poll this).

    No client path is accepted; the server re-reads only the selected request's
    confirmed folder scope. Checks are coalesced per scope (see folder_auto_sync).
    """
    with connect() as conn:
        require_resource_permission(request, PROJECT_DATA_VIEW, "request", payload.request_id, conn=conn)
        if not folder_auto_sync.request_in_project(conn, payload.project_id, payload.request_id):
            raise HTTPException(404, {"code": "FOLDER_SCHEMA_SCOPE_MISMATCH", "message": "프로젝트와 의뢰 문맥이 일치하지 않습니다."})
        result = folder_auto_sync.sync(conn, payload.project_id, payload.request_id, payload.environment,
                                       request.state.principal.user_id, force=payload.force)
        if not result.get("coalesced") and result["status"] in {"REFRESHED", "CONFLICT"}:
            write_audit_event(request=request, principal=request.state.principal, status_code=200,
                              action="FOLDER_ENVIRONMENT_AUTO_SYNCED" if result["status"] == "REFRESHED" else "FOLDER_ENVIRONMENT_REFRESH_CONFLICT",
                              detail={"snapshot_id": result.get("snapshot_id"), "project_id": payload.project_id,
                                      "request_id": payload.request_id, "environment": payload.environment,
                                      "status": result["status"], "force": payload.force}, connection=conn)
        return result
@router.post("/discover")
def discover(request: Request, payload: Discover | None = None):
    """Register new SPDM project/request folders found under the storage root (screens poll this).

    Takes no path or scope from the client. Any active account may trigger it:
    project.data.view is the company permission that already lets every active
    account list all projects and requests (GET /api/projects), so the result
    reveals nothing new. Folder paths needing review go to global admins only.
    Runs are throttled server-side (see folder_auto_discovery).
    """
    force = bool(payload and payload.force)
    # Short permission check; the discovery opens a DB connection only when it
    # actually runs, and never waits for a run that is already in progress.
    access = require_permission(request, PROJECT_DATA_VIEW)
    result = folder_auto_discovery.discover(force=force)
    if not result.get("coalesced") and (result["created_projects"] or result["created_requests"]):
        write_audit_event(request=request, principal=request.state.principal, status_code=200,
                          action="FOLDER_ENVIRONMENT_AUTO_DISCOVERED",
                          detail=folder_auto_discovery.audit_detail(result))
    return folder_auto_discovery.visible_result(
        result, is_global_admin=bool(getattr(access.principal, "is_global_admin", False)))
@router.post("/refresh")
def refresh(payload: Refresh, request: Request):
    with connect() as conn:
        require_resource_permission(request, RESULT_IMPORT, "request", payload.request_id, conn=conn)
        try:
            with legacy.WRITE_LOCK:
                result = service.refresh_scope(
                    conn, legacy.configured_root(conn), payload.project_id, payload.request_id,
                    payload.environment, request.state.principal.user_id,
                )
            if result.get("activated") and result.get("snapshot_id"):
                result_registration.reconcile_schema_refresh_failures(
                    conn, payload.project_id, payload.request_id, payload.environment,
                    result["snapshot_id"], result["status"], request.state.principal.user_id,
                )
            write_audit_event(request=request, principal=request.state.principal, status_code=200,
                              action="FOLDER_ENVIRONMENT_REFRESH_CONFLICT" if result["status"] == "CONFLICT" else "FOLDER_ENVIRONMENT_REFRESHED",
                              detail={"snapshot_id": result["snapshot_id"], "project_id": payload.project_id,
                                      "request_id": payload.request_id, "environment": payload.environment,
                                      "status": result["status"]}, connection=conn)
            return result
        except ValueError as exc:
            code = getattr(exc, "code", "ENVIRONMENT_REFRESH_INVALID")
            status_code = getattr(exc, "status_code", 422)
            write_audit_event(request=request, principal=request.state.principal, status_code=status_code,
                              action="FOLDER_ENVIRONMENT_REFRESH_FAILED",
                              detail={"project_id": payload.project_id, "request_id": payload.request_id,
                                      "environment": payload.environment, "code": code}, connection=conn)
            raise HTTPException(status_code, {"code": code, "message": str(exc)}) from exc
@router.post("/previews")
def preview(payload: Preview, request: Request):
    with connect() as conn:
        admin(request, conn)
        try: return service.preview(conn, payload.scan_id, [x.model_dump() for x in payload.assignments], request.state.principal.user_id, payload.require_usage_review)
        except ValueError as exc: raise HTTPException(422, {"code":"ENVIRONMENT_PREVIEW_INVALID", "message":str(exc)}) from exc
@router.post("/previews/{preview_id}/usage-review")
def usage_review(preview_id: str, payload: UsageReview, request: Request):
    with connect() as conn:
        admin(request, conn)
        context = service.preview_context(conn, preview_id); scoped(request, conn, context["request_id"])
        try:
            with legacy.WRITE_LOCK:
                return service.usage_review(conn, preview_id, payload.case_relative_path, payload.selection, payload.selected_sources,
                                            payload.metric_paths, payload.excludes, payload.acknowledge_partial, legacy.configured_root(conn))
        except dashboard_capture.DashboardCaptureError as exc:
            raise HTTPException(422, {"code": exc.code, "message": str(exc)}) from exc
        except ValueError as exc:
            raise HTTPException(422, {"code":"USAGE_SOURCE_REVIEW_INVALID", "message":str(exc)}) from exc
@router.post("/registrations")
def register(payload: Register, request: Request):
    with connect() as conn:
        admin(request, conn)
        context = service.preview_context(conn, payload.preview_id); scoped(request, conn, context["request_id"])
        try:
            with legacy.WRITE_LOCK:
                result = service.register(conn, payload.preview_id, payload.idempotency_key, payload.capture, request.state.principal, legacy.configured_root(conn))
            write_audit_event(request=request, principal=request.state.principal, status_code=200, action="FOLDER_ENVIRONMENT_REGISTERED", detail={"registration_id": result["registration_id"], "preview_id": payload.preview_id}, connection=conn)
            return result
        except ValueError as exc: raise HTTPException(422, {"code":"ENVIRONMENT_REGISTRATION_INVALID", "message":str(exc)}) from exc
@router.get("/registrations/{registration_id}")
def status(registration_id: str, request: Request):
    with connect() as conn:
        admin(request, conn); scoped(request, conn, service.registration_context(conn, registration_id)["request_id"]); return service.registration(conn, registration_id)
@router.post("/registrations/{registration_id}/capture/retry")
def retry(registration_id: str, payload: Retry, request: Request):
    with connect() as conn:
        admin(request, conn); scoped(request, conn, service.registration_context(conn, registration_id)["request_id"])
        result = service.retry(conn, registration_id, payload.job_ids, request.state.principal, legacy.configured_root(conn))
        write_audit_event(request=request, principal=request.state.principal, status_code=200, action="FOLDER_ENVIRONMENT_CAPTURE_RETRIED", detail={"registration_id": registration_id, "job_ids": payload.job_ids}, connection=conn)
        return result
