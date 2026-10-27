"""Separate API so legacy folder-discovery clients retain their contract."""
from typing import Literal
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from ..database_connection import connect
from ..security import write_audit_event
from ..modules.access_control import PROJECT_DATA_VIEW, RESULT_IMPORT, SYSTEM_CATALOG_MANAGE, require_permission, require_resource_permission
from ..services import (capture_jobs, dashboard_capture, folder_auto_discovery, folder_auto_sync, folder_discovery as legacy,
                        folder_discovery_environment as service, result_registration, storage_rescan)
from ..services.drive import gateway as drive_gateway
from ..services.drive import reads as drive_reads
from ..services.drive import sources as drive_sources
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
def _prefetch_scan(session, payload: Scan, request: Request) -> None:
    """scx mode: list the scanned folder's tree before the scan runs (issue #51, bounded by the drive scan limits)."""
    from ..services import folder_discovery_scan, spdm_storage

    with connect() as conn:                      # authorise before any drive listing (the body checks again)
        admin(request, conn); scoped(request, conn, payload.request_id)
    try:
        relative = legacy.normal(payload.relative_path)
    except (ValueError, spdm_storage.SpdmStorageError):
        return                                   # the endpoint reports the invalid path
    limits = folder_discovery_scan.drive_limits()
    drive_reads.prefetch_subtree(session, relative, max_listings=limits.max_folders, max_seconds=limits.max_seconds)


@router.post("/scan")
@drive_reads.read_session(prepare=_prefetch_scan)
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
    SCX drive mode: the drive is read without a DB connection held, changed
    files wait for confirmation and the result carries ``drive`` counts.
    """
    with connect() as conn:
        require_resource_permission(request, PROJECT_DATA_VIEW, "request", payload.request_id, conn=conn)
        if not folder_auto_sync.request_in_project(conn, payload.project_id, payload.request_id):
            raise HTTPException(404, {"code": "FOLDER_SCHEMA_SCOPE_MISMATCH", "message": "프로젝트와 의뢰 문맥이 일치하지 않습니다."})
        if not drive_reads.active():
            result = folder_auto_sync.sync(conn, payload.project_id, payload.request_id, payload.environment,
                                           request.state.principal.user_id, force=payload.force)
            _sync_audit(request, payload, result, conn)
            return result
    result = folder_auto_sync.sync_drive(payload.project_id, payload.request_id, payload.environment,
                                         request.state.principal.user_id, force=payload.force)
    with connect() as conn:
        _sync_audit(request, payload, result, conn)
    return result
class SyncRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
@router.post("/sync-request")
def sync_request(payload: SyncRequest, request: Request):
    """[새 결과 확인]: check every registered environment of one request now (2026-10-14).

    Same permission and coalescing as ``/sync`` (force = the short manual interval); the server
    reads only the request's registered folder scopes. Returns a summary for the screen.
    """
    with connect() as conn:
        require_resource_permission(request, PROJECT_DATA_VIEW, "request", payload.request_id, conn=conn)
        if not folder_auto_sync.request_in_project(conn, payload.project_id, payload.request_id):
            raise HTTPException(404, {"code": "FOLDER_SCHEMA_SCOPE_MISMATCH", "message": "프로젝트와 의뢰 문맥이 일치하지 않습니다."})
    result = folder_auto_sync.sync_request(payload.project_id, payload.request_id, request.state.principal.user_id)
    if result["changed"]:
        with connect() as conn:
            write_audit_event(request=request, principal=request.state.principal, status_code=200,
                              action="FOLDER_ENVIRONMENT_AUTO_SYNCED",
                              detail={"project_id": payload.project_id, "request_id": payload.request_id,
                                      "manual": True, "added": result["added"]}, connection=conn)
    return result
def _sync_audit(request, payload: Sync, result: dict, conn) -> None:
    if not result.get("coalesced") and result["status"] in {"REFRESHED", "CONFLICT"}:
        write_audit_event(request=request, principal=request.state.principal, status_code=200,
                          action="FOLDER_ENVIRONMENT_AUTO_SYNCED" if result["status"] == "REFRESHED" else "FOLDER_ENVIRONMENT_REFRESH_CONFLICT",
                          detail={"snapshot_id": result.get("snapshot_id"), "project_id": payload.project_id,
                                  "request_id": payload.request_id, "environment": payload.environment,
                                  "status": result["status"], "force": payload.force}, connection=conn)
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
@router.post("/rescan")
def rescan_start(request: Request):
    """[저장 폴더 새로고침]: full rescan of the storage with the current depth schema (2026-10-17).

    Global administrators only. Registers every new project/request folder, checks every
    registered request and reports why a folder was not registered (see ``storage_rescan``).
    Runs in the background; a call while a rescan runs returns that run. Takes no client input.
    """
    with connect() as conn:
        admin(request, conn)
    principal = request.state.principal
    # No DB connection is held while the rescan starts (drive reads must start without one).
    current, started = storage_rescan.start(str(principal.user_id), started_by=str(getattr(principal, "username", "") or principal.user_id))
    if started:
        with connect() as conn:
            write_audit_event(request=request, principal=principal, status_code=200,
                              action="STORAGE_RESCAN_STARTED", detail={}, connection=conn)
    return {**current, "started": started}
@router.get("/rescan")
def rescan_status(request: Request):
    """State of the current or last full rescan (global administrators only: it lists folder paths)."""
    with connect() as conn:
        admin(request, conn)
    return storage_rescan.state()
@router.get("/capture-jobs")
def capture_jobs_status(request: Request, status: Literal["PENDING", "RUNNING", "COMPLETED", "FAILED"] | None = None,
                        limit: int = Query(300, ge=1, le=1000)):
    """[결과 받기 상태] (global administrators, issue #55): download status of the SCX drive capture jobs.

    Per job: request/Case, status (PENDING 대기 · RUNNING 받는 중 · COMPLETED · FAILED), error code with a
    plain Korean message, times and, while RUNNING, the live progress (files / bytes of the current run,
    current file; in memory of the server process). ``totals`` count all jobs, whatever ``status``/``limit``.
    Failed jobs are retried with ``POST /registrations/{registration_id}/capture/retry``. Read only.
    """
    with connect() as conn:
        admin(request, conn)
        return capture_jobs.list_jobs(conn, status=status, limit=limit)
@router.post("/rescan/cancel")
def rescan_cancel(request: Request):
    """[중지]: stop the running full rescan at its next check point (global administrators only, issue #55).

    The run ends ``CANCELLED`` and keeps what it found so far. ``requested`` is false when no rescan runs.
    """
    with connect() as conn:
        admin(request, conn)
    principal = request.state.principal
    current, requested = storage_rescan.cancel(str(getattr(principal, "username", "") or principal.user_id))
    if requested:
        with connect() as conn:
            write_audit_event(request=request, principal=principal, status_code=200,
                              action="STORAGE_RESCAN_CANCELLED", detail={}, connection=conn)
    return {**current, "requested": requested}
class Exclusions(BaseModel):
    relative_paths: list[str] = Field(min_length=1, max_length=1000)


@router.post("/exclusions")
def exclude_folders(payload: Exclusions, request: Request):
    """[제외] / [모두 제외] in the rescan result: request folders the discovery never registers (issue #55).

    Global administrators only. Only request-level folders listed in the last rescan/discovery result are
    accepted (no free path); registered and whitespace-ambiguous folders are refused (``skipped``).
    The rescan state moves the folders to ``ADMIN_EXCLUDED`` at once.
    """
    paths = [str(path)[:1024] for path in payload.relative_paths]
    with connect() as conn:
        admin(request, conn)
        result = folder_auto_discovery.exclude_folders(conn, legacy.configured_root(conn), paths,
                                                       actor=str(request.state.principal.user_id),
                                                       listed=storage_rescan.listed_reviews())
        if result["excluded"]:
            write_audit_event(request=request, principal=request.state.principal, status_code=200,
                              action="FOLDER_DISCOVERY_EXCLUDED", detail={"paths": result["excluded"]}, connection=conn)
    storage_rescan.mark_excluded(result["excluded"])
    return result


@router.post("/exclusions/restore")
def restore_folders(payload: Exclusions, request: Request):
    """[다시 포함]: undo an exclusion made from the rescan result (the folder is a candidate again)."""
    paths = [str(path)[:1024] for path in payload.relative_paths]
    with connect() as conn:
        admin(request, conn)
        result = folder_auto_discovery.restore_folders(conn, legacy.configured_root(conn), paths)
        if result["restored"]:
            write_audit_event(request=request, principal=request.state.principal, status_code=200,
                              action="FOLDER_DISCOVERY_EXCLUSION_REMOVED", detail={"paths": result["restored"]}, connection=conn)
    storage_rescan.mark_restored(result["restored"])
    return result
def _classify_for_refresh(session, payload: "Refresh", request: Request) -> None:
    """scx: record new/changed/missing drive files of the request before a manual refresh (05 §4).

    Fail closed (M2): when the drive folder cannot be classified the refresh is
    refused with that error instead of reading drive files nobody compared.
    """
    with connect() as conn:
        require_resource_permission(request, RESULT_IMPORT, "request", payload.request_id, conn=conn)
    key = (session.root_key, payload.project_id, payload.request_id, str(payload.environment).upper())
    try:
        with folder_auto_sync._drive_lock(key):   # not concurrently with the auto-sync of the same request
            drive_sources.classify_request(session, payload.project_id, payload.request_id, payload.environment,
                                           request.state.principal.user_id)
    except (ValueError, OSError) as exc:
        code = str(getattr(exc, "code", "") or "DRIVE_SOURCE_CLASSIFY_FAILED")
        status_code = getattr(exc, "status_code", None)
        if not isinstance(status_code, int):
            status_code = drive_gateway.storage_http_status(code, 422 if isinstance(exc, ValueError) else 503)
        message = str(exc) if isinstance(exc, ValueError) else "드라이브 원본을 확인하지 못했습니다. 잠시 후 다시 시도하세요."
        folder_auto_sync.log_outcome(payload.project_id, payload.request_id, payload.environment,
                                     {"status": "FAILED", "code": code, "message": message}, exc, source="refresh")
        with connect() as conn:
            write_audit_event(request=request, principal=request.state.principal, status_code=status_code,
                              action="FOLDER_ENVIRONMENT_REFRESH_FAILED",
                              detail={"project_id": payload.project_id, "request_id": payload.request_id,
                                      "environment": payload.environment, "code": code}, connection=conn)
        raise HTTPException(status_code, {"code": code, "message": message}) from exc
@router.post("/refresh")
@drive_reads.read_session(prepare=_classify_for_refresh)
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
            if result["status"] == "CONFLICT":
                folder_auto_sync.log_outcome(payload.project_id, payload.request_id, payload.environment,
                                             {"status": "CONFLICT", "code": "FOLDER_SCHEMA_ROLE_CONFLICT",
                                              "message": "폴더 역할이 모호한 항목이 있어 이전 구조를 유지합니다."}, source="refresh")
            write_audit_event(request=request, principal=request.state.principal, status_code=200,
                              action="FOLDER_ENVIRONMENT_REFRESH_CONFLICT" if result["status"] == "CONFLICT" else "FOLDER_ENVIRONMENT_REFRESHED",
                              detail={"snapshot_id": result["snapshot_id"], "project_id": payload.project_id,
                                      "request_id": payload.request_id, "environment": payload.environment,
                                      "status": result["status"]}, connection=conn)
            return result
        except ValueError as exc:
            code = getattr(exc, "code", "ENVIRONMENT_REFRESH_INVALID")
            status_code = getattr(exc, "status_code", 422)
            folder_auto_sync.log_outcome(payload.project_id, payload.request_id, payload.environment,
                                         {"status": "FAILED", "code": code, "message": str(exc)}, exc, source="refresh")
            write_audit_event(request=request, principal=request.state.principal, status_code=status_code,
                              action="FOLDER_ENVIRONMENT_REFRESH_FAILED",
                              detail={"project_id": payload.project_id, "request_id": payload.request_id,
                                      "environment": payload.environment, "code": code}, connection=conn)
            raise HTTPException(status_code, {"code": code, "message": str(exc)}) from exc
@router.post("/previews")
@drive_reads.read_session()
def preview(payload: Preview, request: Request):
    with connect() as conn:
        admin(request, conn)
        try: return service.preview(conn, payload.scan_id, [x.model_dump() for x in payload.assignments], request.state.principal.user_id, payload.require_usage_review)
        except ValueError as exc: raise HTTPException(422, {"code":"ENVIRONMENT_PREVIEW_INVALID", "message":str(exc)}) from exc
@router.post("/previews/{preview_id}/usage-review")
@drive_reads.read_session()
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
@drive_reads.read_session()
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
@drive_reads.read_session()
def retry(registration_id: str, payload: Retry, request: Request):
    with connect() as conn:
        admin(request, conn); scoped(request, conn, service.registration_context(conn, registration_id)["request_id"])
        result = service.retry(conn, registration_id, payload.job_ids, request.state.principal, legacy.configured_root(conn))
        write_audit_event(request=request, principal=request.state.principal, status_code=200, action="FOLDER_ENVIRONMENT_CAPTURE_RETRIED", detail={"registration_id": registration_id, "job_ids": payload.job_ids}, connection=conn)
        return result
