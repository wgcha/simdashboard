"""Administrator boundary for read-only surveys and explicit workload creation."""
from __future__ import annotations

import json
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
    role: Literal["PROJECT", "REQUEST", "LOAD_CASE"]
    prefix: str = Field(default="", max_length=256)
    delimiter: str = Field(default="_", min_length=1, max_length=8)
    code_token: int = Field(default=1, ge=0, le=100)
    name_from_token: int = Field(default=2, ge=0, le=100)
    analysis_type: str = Field(default="", max_length=128)


class FolderScan(BaseModel):
    relative_path: str = Field(default="", max_length=1024)


class FolderPreview(BaseModel):
    scan_id: str = Field(min_length=1, max_length=128)
    rules: list[FolderRule] = Field(min_length=1, max_length=30)


class FolderApply(BaseModel):
    preview_id: str = Field(min_length=1, max_length=128)


class FolderRuleUpdate(FolderScan):
    rules: list[FolderRule] = Field(min_length=1, max_length=30)
    expected_revision: int = Field(ge=0)


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
                return svc.preview(conn, payload.scan_id, [rule.model_dump() for rule in payload.rules], request.state.principal.user_id)
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
                conn.execute("INSERT INTO folder_discovery_rules(root_key,relative_path,rules_json,revision,updated_at,updated_by) "
                             "VALUES(?,?,?,?,?,?) ON CONFLICT(root_key,relative_path) DO UPDATE SET rules_json=excluded.rules_json,"
                             "revision=excluded.revision,updated_at=excluded.updated_at,updated_by=excluded.updated_by",
                             [root_key, relative, json.dumps(result["rules"]), result["revision"], svc.now(), request.state.principal.user_id])
                write_audit_event(request=request, principal=request.state.principal, status_code=200,
                                  action="FOLDER_DISCOVERY_RULES_SAVED", detail={"relative_path": relative, "revision": result["revision"]}, connection=conn)
                return result
        except (ValueError, OSError, spdm_storage.SpdmStorageError) as error:
            raise path_error(error) from error
