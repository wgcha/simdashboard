"""Administrator boundary for read-only surveys and explicit workload creation."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..database_connection import connect
from ..modules.access_control import SYSTEM_CATALOG_MANAGE, require_permission
from ..security import write_audit_event
from ..services import folder_discovery as svc, spdm_storage
from ..services.semantic_mapping import semantic_transaction
from .semantic_body_limit import SemanticBodyLimitRoute

router = APIRouter(prefix="/api/folder-discovery", tags=["folder-discovery"], route_class=SemanticBodyLimitRoute)


class FolderRule(BaseModel):
    depth: int = Field(ge=0, le=64)
    role: str = Field(min_length=1, max_length=64)
    # `prefix` remains readable for saved rules and older clients. New
    # clients send `keyword`, which matches anywhere in the folder name.
    prefix: str = Field(default="", max_length=256)
    keyword: str | None = Field(default=None, max_length=256)
    delimiter: str = Field(default="_", max_length=8)
    code_token: int = Field(default=1, ge=0, le=100)
    name_from_token: int = Field(default=2, ge=0, le=100)
    analysis_type: str = Field(default="", max_length=128)
    result_config: dict | None = None


class FolderScan(BaseModel):
    relative_path: str = Field(default="", max_length=1024)


class FolderPreview(BaseModel):
    scan_id: str = Field(min_length=1, max_length=128)
    rules: list[FolderRule] = Field(min_length=1, max_length=30)
    excluded_paths: list[str] = Field(default_factory=list, max_length=5000)


class FolderApply(BaseModel):
    preview_id: str = Field(min_length=1, max_length=128)


class FolderRuleUpdate(FolderScan):
    rules: list[FolderRule] = Field(min_length=1, max_length=30)
    expected_revision: int = Field(ge=0)


class CatalogRole(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=128)
    kind: Literal["PROJECT", "REQUEST", "LOAD_CASE", "RESULTS", "INPUT"]
    active: bool = True


class CatalogAnalysisType(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=128)
    active: bool = True
    default_result_config: dict | None = None


class FolderCatalogUpdate(BaseModel):
    expected_revision: int = Field(ge=1)
    roles: list[CatalogRole] = Field(min_length=5, max_length=100)
    analysis_types: list[CatalogAnalysisType] = Field(min_length=1, max_length=200)


class ResultConfigItem(BaseModel):
    registry_id: str = Field(min_length=1, max_length=128)
    expected_binding_revision: int | None = Field(default=None, ge=1)
    result_config: dict


class ResultConfigBulk(BaseModel):
    items: list[ResultConfigItem] = Field(min_length=1, max_length=200)


def authorize(request, conn):
    require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
    if not request.state.principal.is_global_admin:
        raise HTTPException(403, {"code": "GLOBAL_ADMIN_REQUIRED", "message": "전체 폴더 조사·업무 생성은 관리자 기능입니다."})


def path_error(error):
    return HTTPException(422, {"code": "FOLDER_PATH_INVALID", "message": str(error)})


@router.get("/browse")
def browse(request: Request, relative_path: str = Query(default="", max_length=1024)):
    with connect() as conn:
        authorize(request, conn)
        try:
            current = spdm_storage.storage_root(conn)
            if current.root is None:
                return {"configured": False, "root_path": None, "relative_path": "", "entries": []}
            relative = svc.normal(relative_path)
            return {"configured": True, "root_path": str(current.root), "relative_path": relative,
                    "entries": svc.browse(current.root, relative)}
        except (ValueError, OSError, spdm_storage.SpdmStorageError) as error:
            raise path_error(error) from error


@router.get("/catalog")
def get_catalog(request: Request):
    with connect() as conn:
        authorize(request, conn)
        return svc.catalog(conn)


@router.put("/catalog")
def put_catalog(payload: FolderCatalogUpdate, request: Request):
    with connect() as conn:
        authorize(request, conn)
        try:
            with svc.WRITE_LOCK, semantic_transaction(conn):
                svc.lock_tables(conn)
                result = svc.save_catalog(conn, payload.model_dump(), request.state.principal.user_id)
                write_audit_event(request=request, principal=request.state.principal, status_code=200,
                                  action="FOLDER_DISCOVERY_CATALOG_SAVED",
                                  detail={"revision": result["revision"]}, connection=conn)
                return result
        except ValueError as error:
            raise HTTPException(422, {"code": "FOLDER_CATALOG_INVALID", "message": str(error)}) from error


@router.post("/scan")
def scan(payload: FolderScan, request: Request):
    with connect() as conn:
        authorize(request, conn)
        try:
            root, relative = svc.configured_root(conn), svc.normal(payload.relative_path)
            identity = svc.root_identity(root)
            result = svc.scan(root, relative)
            if svc.root_identity(svc.configured_root(conn)) != identity:
                svc.fail("ROOT_CHANGED", "조사 중 저장소가 변경되었습니다. 다시 조사하세요.")
            scan_id = svc.save_scan(conn, root, relative, result, request.state.principal.user_id)
            return {"id": scan_id, "relative_path": relative, **result}
        except (ValueError, OSError, spdm_storage.SpdmStorageError) as error:
            raise path_error(error) from error


@router.post("/preview")
def preview(payload: FolderPreview, request: Request):
    with connect() as conn:
        authorize(request, conn)
        try:
            with svc.WRITE_LOCK, semantic_transaction(conn):
                svc.lock_tables(conn)
                return svc.preview(conn, payload.scan_id, [rule.model_dump() for rule in payload.rules], request.state.principal.user_id,
                                   payload.excluded_paths)
        except (ValueError, OSError, spdm_storage.SpdmStorageError) as error:
            raise path_error(error) from error


@router.post("/apply")
def apply(payload: FolderApply, request: Request):
    with connect() as conn:
        authorize(request, conn)
        try:
            return svc.apply(conn, payload.preview_id, request.state.principal,
                             lambda detail: write_audit_event(request=request, principal=request.state.principal,
                             status_code=200, action="FOLDER_DISCOVERY_APPLIED", detail=detail, connection=conn))
        except (ValueError, OSError, spdm_storage.SpdmStorageError) as error:
            raise path_error(error) from error


@router.get("/rules")
def get_rules(request: Request, relative_path: str = Query(default="", max_length=1024)):
    with connect() as conn:
        authorize(request, conn)
        try:
            return svc.rules(conn, svc.root_identity(svc.configured_root(conn)), svc.normal(relative_path))
        except (ValueError, OSError, spdm_storage.SpdmStorageError) as error:
            raise path_error(error) from error


@router.get("/saved-rules")
def get_saved_rules(request: Request, offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=200)):
    with connect() as conn:
        authorize(request, conn)
        try:
            return svc.saved_rule_locations(conn, svc.root_identity(svc.configured_root(conn)), offset=offset, limit=limit)
        except (ValueError, OSError, spdm_storage.SpdmStorageError) as error:
            raise path_error(error) from error


@router.get("/history")
def get_history(request: Request, offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=200)):
    with connect() as conn:
        authorize(request, conn)
        try:
            return svc.applied_history(conn, svc.root_identity(svc.configured_root(conn)), offset=offset, limit=limit)
        except (ValueError, OSError, spdm_storage.SpdmStorageError) as error:
            raise path_error(error) from error


@router.get("/history/{preview_id}/rules")
def get_history_rules(preview_id: str, request: Request):
    with connect() as conn:
        authorize(request, conn)
        try:
            return svc.history_rules(conn, preview_id, svc.root_identity(svc.configured_root(conn)))
        except (ValueError, OSError, spdm_storage.SpdmStorageError) as error:
            raise path_error(error) from error


@router.get("/connections")
def get_connections(request: Request, offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=200)):
    with connect() as conn:
        authorize(request, conn)
        try:
            return svc.connections(conn, svc.root_identity(svc.configured_root(conn)), offset=offset, limit=limit)
        except (ValueError, OSError, spdm_storage.SpdmStorageError) as error:
            raise path_error(error) from error


@router.post("/connections/result-config/preview")
def preview_connection_result_config(payload: ResultConfigBulk, request: Request):
    with connect() as conn:
        authorize(request, conn)
        try:
            return svc.result_config_preview(conn, svc.root_identity(svc.configured_root(conn)), [item.model_dump() for item in payload.items])
        except ValueError as error:
            raise HTTPException(422, {"code": "RESULT_CONFIG_INVALID", "message": str(error)}) from error


@router.post("/connections/result-config/apply")
def apply_connection_result_config(payload: ResultConfigBulk, request: Request):
    with connect() as conn:
        authorize(request, conn)
        try:
            with svc.WRITE_LOCK, semantic_transaction(conn):
                svc.lock_tables(conn)
                result = svc.apply_result_config(conn, svc.root_identity(svc.configured_root(conn)), [item.model_dump() for item in payload.items], request.state.principal.user_id)
                write_audit_event(request=request, principal=request.state.principal, status_code=200,
                                  action="FOLDER_DISCOVERY_RESULT_CONFIG_APPLIED", detail=result, connection=conn)
                return result
        except ValueError as error:
            raise HTTPException(422, {"code": "RESULT_CONFIG_INVALID", "message": str(error)}) from error


@router.put("/rules")
def put_rules(payload: FolderRuleUpdate, request: Request):
    with connect() as conn:
        authorize(request, conn)
        try:
            with svc.WRITE_LOCK, semantic_transaction(conn):
                svc.lock_tables(conn)
                root_key, relative = svc.root_identity(svc.configured_root(conn)), svc.normal(payload.relative_path)
                current = svc.rules(conn, root_key, relative)
                if current["revision"] != payload.expected_revision:
                    svc.fail("RULE_REVISION_CONFLICT", "저장된 규칙이 변경되었습니다. 다시 불러오세요.")
                result = {"rules": [rule.model_dump() for rule in payload.rules], "revision": current["revision"] + 1}
                svc.persist_rules(conn, root_key, relative, result, request.state.principal.user_id)
                write_audit_event(request=request, principal=request.state.principal, status_code=200,
                                  action="FOLDER_DISCOVERY_RULES_SAVED", detail={"relative_path": relative, "revision": result["revision"]}, connection=conn)
                return result
        except (ValueError, OSError, spdm_storage.SpdmStorageError) as error:
            raise path_error(error) from error
