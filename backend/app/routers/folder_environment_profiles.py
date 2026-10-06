"""Depth schema editing, registration history and registration delete (global admin)."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..database_connection import connect
from ..security import write_audit_event
from ..services import (environment_folder_profiles as service, folder_discovery as legacy,
                        folder_discovery_environment as environment_service, folder_environment_deletion as deletion,
                        project_cleanup as cleanup)
from ..services.semantic_mapping import semantic_transaction
from .folder_discovery_environment import admin
from .semantic_body_limit import SemanticBodyLimitRoute

router = APIRouter(prefix="/api/folder-discovery/environments", tags=["folder-discovery-environments"], route_class=SemanticBodyLimitRoute)
requests_router = APIRouter(tags=["folder-discovery-environments"], route_class=SemanticBodyLimitRoute)

_DEPRECATED = {"code": "DEPRECATED_USE_DEPTH_SCHEMA",
               "message": "폴더별 규칙 편집은 폐기되었습니다. 깊이 스키마(…/environments/depth-schema)를 사용하세요."}


class DepthSchemaPut(BaseModel):
    expected_schema_set_id: str = Field(min_length=1, max_length=128)
    upper: dict
    environments: dict


class DepthSchemaDraft(BaseModel):
    upper: dict
    environments: dict


class DepthSamples(BaseModel):
    segment: Literal["UPPER", "USAGE", "DISTRIBUTION"]
    upper: dict | None = None


class DeleteTargets(BaseModel):
    registration_ids: list[str] = Field(min_length=1, max_length=deletion.MAX_IDS)


class DeleteConfirm(DeleteTargets):
    confirm_token: str = Field(min_length=1, max_length=128)


class CleanupTargets(BaseModel):
    project_ids: list[str] = Field(min_length=1, max_length=cleanup.MAX_IDS)


class CleanupConfirm(CleanupTargets):
    confirm_token: str = Field(min_length=1, max_length=128)


def _invalid(exc: ValueError) -> HTTPException:
    return HTTPException(422, {"code": "DEPTH_SCHEMA_INVALID", "message": str(exc)})


# ---- depth schema (§6) ------------------------------------------------------------

@router.get("/depth-schema")
def get_depth_schema(request: Request):
    with connect() as conn:
        admin(request, conn)
        return service.get_depth_schema(conn)


@router.put("/depth-schema")
def put_depth_schema(payload: DepthSchemaPut, request: Request):
    with connect() as conn:
        admin(request, conn)
        try:
            with legacy.WRITE_LOCK, semantic_transaction(conn):
                saved = service.save_depth_schema(conn, request.state.principal, payload.expected_schema_set_id,
                                                  payload.upper, payload.environments)
                write_audit_event(request=request, principal=request.state.principal, status_code=200,
                                  action="FOLDER_DEPTH_SCHEMA_SAVED",
                                  detail={"schema_set_id": saved["schema_set_id"],
                                          "previous_schema_set_id": payload.expected_schema_set_id}, connection=conn)
                return saved
        except ValueError as exc:
            raise _invalid(exc) from exc


@router.post("/depth-schema/samples")
def depth_schema_samples(payload: DepthSamples, request: Request):
    with connect() as conn:
        admin(request, conn)
        try:
            return service.depth_schema_samples(conn, legacy.configured_root(conn), payload.segment, payload.upper)
        except ValueError as exc:
            raise _invalid(exc) from exc


@router.post("/depth-schema/check")
def depth_schema_check(payload: DepthSchemaDraft, request: Request):
    with connect() as conn:
        admin(request, conn)
        try:
            return service.depth_schema_check(conn, legacy.configured_root(conn), payload.upper, payload.environments)
        except ValueError as exc:
            raise _invalid(exc) from exc


# ---- deprecated folder-rule editing (§6 폐기) --------------------------------------

@router.post("/profiles", status_code=410)
def create(request: Request):
    raise HTTPException(410, _DEPRECATED)


@router.put("/profiles/{profile_id}", status_code=410)
def update(profile_id: str, request: Request):
    raise HTTPException(410, _DEPRECATED)


@router.delete("/profiles/{profile_id}", status_code=410)
def archive(profile_id: str, request: Request):
    raise HTTPException(410, _DEPRECATED)


@router.post("/profiles/from-legacy", status_code=410)
def copy(request: Request):
    raise HTTPException(410, _DEPRECATED)


# ---- registration history & delete (§13.6) ----------------------------------------

@router.get("/history")
def history(request: Request, limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0),
            include_deleted: bool = Query(default=False)):
    with connect() as conn:
        admin(request, conn)
        return service.history(conn, limit, offset, include_deleted)


@router.post("/registrations/delete-preview")
def delete_preview(payload: DeleteTargets, request: Request):
    with connect() as conn:
        admin(request, conn)
        try:
            return deletion.delete_preview(conn, payload.registration_ids)
        except ValueError as exc:
            raise HTTPException(422, {"code": "REGISTRATION_DELETE_INVALID", "message": str(exc)}) from exc


@router.post("/registrations/delete")
def delete_registrations(payload: DeleteConfirm, request: Request):
    principal = request.state.principal
    with connect() as conn:
        admin(request, conn)

        def audit(connection, detail):
            write_audit_event(request=request, principal=principal, status_code=200,
                              action="FOLDER_ENVIRONMENT_REGISTRATION_DELETED", detail=detail, connection=connection)

        try:
            return deletion.delete(conn, payload.registration_ids, payload.confirm_token, principal.user_id, audit)
        except ValueError as exc:
            raise HTTPException(422, {"code": "REGISTRATION_DELETE_INVALID", "message": str(exc)}) from exc


# ---- project cleanup (§16, D15 exception) -----------------------------------------

@router.get("/project-cleanup")
def project_cleanup_candidates(request: Request):
    with connect() as conn:
        admin(request, conn)
        return cleanup.candidates(conn)


@router.post("/project-cleanup/preview")
def project_cleanup_preview(payload: CleanupTargets, request: Request):
    with connect() as conn:
        admin(request, conn)
        try:
            return cleanup.preview(conn, payload.project_ids)
        except ValueError as exc:
            raise HTTPException(422, {"code": "PROJECT_CLEANUP_INVALID", "message": str(exc)}) from exc


@router.post("/project-cleanup/delete")
def project_cleanup_delete(payload: CleanupConfirm, request: Request):
    principal = request.state.principal
    with connect() as conn:
        admin(request, conn)

        def audit(connection, detail):
            write_audit_event(request=request, principal=principal, status_code=200,
                              action="PROJECT_CLEANUP_DELETED", detail=detail, connection=connection)

        try:
            return cleanup.delete(conn, payload.project_ids, payload.confirm_token, audit)
        except ValueError as exc:
            raise HTTPException(422, {"code": "PROJECT_CLEANUP_INVALID", "message": str(exc)}) from exc


# ---- request reinterpret (§6, D9) --------------------------------------------------

@requests_router.post("/api/requests/{request_id}/reinterpret")
def reinterpret(request_id: str, request: Request):
    with connect() as conn:
        admin(request, conn)
        try:
            result = environment_service.reinterpret_request(conn, request.state.principal, request_id)
        except ValueError as exc:
            code = getattr(exc, "code", "REINTERPRET_INVALID")
            raise HTTPException(getattr(exc, "status_code", 422), {"code": code, "message": str(exc)}) from exc
        write_audit_event(request=request, principal=request.state.principal, status_code=200,
                          action="FOLDER_ENVIRONMENT_REINTERPRETED" if result.get("registered") else "FOLDER_ENVIRONMENT_REINTERPRET_BLOCKED",
                          detail={"request_id": request_id, "registration_id": result.get("registration_id"),
                                  "deviation_codes": sorted({d.get("code") for d in result.get("deviations") or [] if d.get("code")})},
                          connection=conn)
        return result
