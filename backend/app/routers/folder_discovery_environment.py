"""Separate API so legacy folder-discovery clients retain their contract."""
from typing import Literal
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from ..database_connection import connect
from ..security import write_audit_event
from ..modules.access_control import RESULT_IMPORT, SYSTEM_CATALOG_MANAGE, require_permission, require_resource_permission
from ..services import folder_discovery as legacy, folder_discovery_environment as service
from .semantic_body_limit import SemanticBodyLimitRoute

router = APIRouter(prefix="/api/folder-discovery/environments", tags=["folder-discovery-environments"], route_class=SemanticBodyLimitRoute)
class Scan(BaseModel):
    environment: Literal["USAGE", "DISTRIBUTION"]
    relative_path: str = Field(default="", max_length=1024)
    profile_id: str | None = None
    project_id: str | None = None
    request_id: str | None = None
class Assignment(BaseModel):
    node_id: str; role_kind: str; confirm: bool = True; target_mode: Literal["CREATE", "LINK"] = "CREATE"; target_id: str | None = None
class Preview(BaseModel): scan_id: str; assignments: list[Assignment] = Field(default_factory=list, max_length=5000)
class Register(BaseModel): preview_id: str; idempotency_key: str = Field(min_length=8, max_length=128); capture: bool = True
class Retry(BaseModel): job_ids: list[str] | None = Field(default=None, max_length=500)
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
@router.post("/previews")
def preview(payload: Preview, request: Request):
    with connect() as conn:
        admin(request, conn)
        try: return service.preview(conn, payload.scan_id, [x.model_dump() for x in payload.assignments], request.state.principal.user_id)
        except ValueError as exc: raise HTTPException(422, {"code":"ENVIRONMENT_PREVIEW_INVALID", "message":str(exc)}) from exc
@router.post("/registrations")
def register(payload: Register, request: Request):
    with connect() as conn:
        admin(request, conn)
        context = service.preview_context(conn, payload.preview_id); scoped(request, conn, context["request_id"])
        try:
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
