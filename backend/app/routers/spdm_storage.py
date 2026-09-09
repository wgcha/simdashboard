"""HTTP boundary for the separate, trusted SPDM workspace root."""

from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..database_connection import connect, rows
from ..modules.access_control import PROJECT_DATA_VIEW, RESULT_IMPORT, SYSTEM_CATALOG_MANAGE, require_permission, require_resource_permission
from ..security import write_audit_event
from ..services import spdm_storage
from ..schemas.api import ResultImportResponse


router = APIRouter(prefix="/api", tags=["spdm-storage"])


class StorageConfigUpdate(BaseModel):
    root: str = Field(min_length=3, max_length=2048)


class StorageBindingUpdate(BaseModel):
    relative_path: str = Field(min_length=3, max_length=1024)


class StructuredUpload(BaseModel):
    filename: str = Field(min_length=5, max_length=240)
    content: str = Field(min_length=1, max_length=5_000_000)
    author: str | None = Field(default=None, max_length=60)


class StorageConfigResponse(BaseModel):
    configured: bool
    locked: bool
    root: str | None = None


class StorageBindingResponse(BaseModel):
    relative_path: str
    project_id: str
    request_id: str
    load_case_id: str


class StorageFileResponse(BaseModel):
    id: str | None = None
    name: str | None = None
    relative_path: str
    kind: str
    size: int
    checksum: str | None = None
    status: str | None = None
    run_id: str | None = None
    message: str | None = None
    reused: bool | None = None


class StorageResultResponse(BaseModel):
    file_id: str
    relative_path: str
    status: Literal["IMPORTED", "SKIPPED", "FAILED"]
    run_id: str | None = None
    run_no: int | None = None
    filename: str | None = None
    summary: dict[str, Any] | None = None
    scalar_count: int | None = None
    node_count: int | None = None
    element_count: int | None = None
    frame_count: int | None = None
    final_time: float | None = None
    time_series_count: int | None = None
    open_cell_count: int | None = None
    chassis_rear_count: int | None = None
    fail_count: int | None = None
    overall_verdict: Literal["PASS", "FAIL"] | None = None
    source_format: str | None = None
    results: list[Any] = Field(default_factory=list)
    warnings: list[Any] = Field(default_factory=list)
    operation: Literal["CREATED", "NOOP", "REPLACED", "REJECTED"] | None = None
    reason_code: str | None = None
    existing_run_id: str | None = None
    replaced_run_id: str | None = None
    source_revision: int | None = None
    message: str | None = None


class StorageScopeResponse(BaseModel):
    binding: StorageBindingResponse
    files: list[StorageFileResponse]
    results: list[StorageResultResponse]


class StorageLoadCaseResponse(BaseModel):
    config: StorageConfigResponse
    binding: StorageBindingResponse | None
    rules: list[dict[str, Any]]
    files: list[StorageFileResponse]
    candidate_folders: list[str]


class StorageFoldersResponse(BaseModel):
    folders: list[dict[str, Any]]


class StorageGlobalRefreshResponse(BaseModel):
    created_bindings: list[StorageBindingResponse]
    refreshed: list[StorageScopeResponse]


class StorageBindingUpdateResponse(BaseModel):
    binding: StorageBindingResponse


class RawUploadResponse(BaseModel):
    stored_file: StorageFileResponse


class StructuredUploadResponse(ResultImportResponse):
    stored_file: StorageFileResponse


def _error(error: spdm_storage.SpdmStorageError, status: int = 422) -> HTTPException:
    return HTTPException(status, detail={"code": error.code, "message": str(error)})


def _root(conn: Any) -> spdm_storage.StorageRoot:
    try:
        result = spdm_storage.storage_root(conn)
    except spdm_storage.SpdmStorageError as error:
        raise _error(error) from error
    if result.root is None:
        raise HTTPException(409, detail={"code": "SPDM_ROOT_UNSET", "message": "SPDM 저장 폴더가 아직 설정되지 않았습니다."})
    return result


def _binding(conn: Any, load_case_id: str) -> dict[str, str]:
    binding = spdm_storage.get_binding(conn, load_case_id)
    if binding is None:
        raise HTTPException(409, detail={"code": "SPDM_BINDING_UNSET", "message": "이 하중 경우에 연결된 SPDM 폴더가 없습니다."})
    return binding


def _audit(request: Request, connection: Any, action: str, detail: dict[str, Any]) -> None:
    write_audit_event(request=request, principal=request.state.principal, status_code=200, action=action, detail=detail, connection=connection)


def _refresh_bound(conn: Any, request: Request, binding: dict[str, str], root: spdm_storage.StorageRoot) -> dict[str, Any]:
    assert root.root is not None
    try:
        spdm_storage.refresh_binding_files(conn, root.root, binding)
        outcomes = spdm_storage.ingest_ready_results(
            conn, root.root, binding,
            principal_user_id=request.state.principal.user_id,
            actor_name=request.state.principal.display_name,
            authorize=lambda command, transaction_connection: require_resource_permission(request, RESULT_IMPORT, "load_case", command["load_case_id"], conn=transaction_connection),
            audit=lambda record: _audit(request, conn, record.action, dict(record.detail)),
        )
        return {"binding": binding, "files": spdm_storage.list_files(conn, binding["load_case_id"]), "results": outcomes}
    except spdm_storage.SpdmStorageError as error:
        raise _error(error) from error


@router.get("/storage/config", response_model=StorageConfigResponse)
def get_storage_config(request: Request) -> dict[str, Any]:
    with connect() as conn:
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
        try:
            current = spdm_storage.storage_root(conn)
        except spdm_storage.SpdmStorageError as error:
            raise _error(error) from error
    return {"configured": current.configured, "locked": current.locked, "root": str(current.root) if current.root else None}


@router.put("/storage/config", response_model=StorageConfigResponse)
def update_storage_config(payload: StorageConfigUpdate, request: Request) -> dict[str, Any]:
    with connect() as conn:
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
        try:
            current = spdm_storage.set_storage_root(conn, payload.root)
        except spdm_storage.SpdmStorageError as error:
            raise _error(error) from error
        _audit(request, conn, "SPDM_STORAGE_ROOT_CONFIGURED", {"configured": True})
    return {"configured": current.configured, "locked": current.locked, "root": str(current.root) if current.root else None}


@router.get("/storage/folders", response_model=StorageFoldersResponse)
def list_storage_folders(request: Request) -> dict[str, Any]:
    with connect() as conn:
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
        root = _root(conn)
        assert root.root is not None
        bound = spdm_storage.bound_paths(conn)
        folders = [item.__dict__ for item in spdm_storage.candidates(root.root) if item.relative_path not in bound]
    return {"folders": folders}


@router.post("/storage/refresh", response_model=StorageGlobalRefreshResponse)
def refresh_storage(request: Request) -> dict[str, Any]:
    with connect() as conn:
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
        root = _root(conn)
        assert root.root is not None
        try:
            created = spdm_storage.discover_bindings(conn, root.root, creator_id=request.state.principal.user_id, creator_name=request.state.principal.display_name)
        except spdm_storage.SpdmStorageError as error:
            raise _error(error) from error
        refreshed = []
        for binding in spdm_storage.all_bindings(conn):
            refreshed.append(_refresh_bound(conn, request, binding, root))
        _audit(request, conn, "SPDM_STORAGE_REFRESHED", {"discovered_count": len(created), "binding_count": len(refreshed)})
    return {"created_bindings": created, "refreshed": refreshed}


@router.get("/load-cases/{load_case_id}/storage", response_model=StorageLoadCaseResponse)
def get_load_case_storage(load_case_id: str, request: Request) -> dict[str, Any]:
    with connect() as conn:
        require_resource_permission(request, PROJECT_DATA_VIEW, "load_case", load_case_id, conn=conn)
        try:
            current = spdm_storage.storage_root(conn)
        except spdm_storage.SpdmStorageError as error:
            raise _error(error) from error
        binding = spdm_storage.get_binding(conn, load_case_id)
        files = spdm_storage.list_files(conn, load_case_id) if binding else []
    return {
        "config": {"configured": current.configured, "locked": current.locked},
        "binding": binding,
        "rules": spdm_storage.rules(binding),
        "files": files,
        # Do not disclose unbound cross-project paths to a scoped user.
        "candidate_folders": [binding["relative_path"]] if binding else [],
    }


@router.put("/load-cases/{load_case_id}/storage", response_model=StorageBindingUpdateResponse)
def bind_load_case_storage(load_case_id: str, payload: StorageBindingUpdate, request: Request) -> dict[str, Any]:
    with connect() as conn:
        require_resource_permission(request, RESULT_IMPORT, "load_case", load_case_id, conn=conn)
        root = _root(conn)
        assert root.root is not None
        try:
            binding = spdm_storage.bind_existing_target(conn, root.root, load_case_id, payload.relative_path)
        except spdm_storage.SpdmStorageError as error:
            raise _error(error) from error
        _audit(request, conn, "SPDM_STORAGE_BOUND", binding)
    return {"binding": binding}


@router.post("/load-cases/{load_case_id}/storage/refresh", response_model=StorageScopeResponse)
def refresh_load_case_storage(load_case_id: str, request: Request) -> dict[str, Any]:
    with connect() as conn:
        require_resource_permission(request, RESULT_IMPORT, "load_case", load_case_id, conn=conn)
        root = _root(conn); binding = _binding(conn, load_case_id)
        result = _refresh_bound(conn, request, binding, root)
        _audit(request, conn, "SPDM_STORAGE_SCOPE_REFRESHED", {"load_case_id": load_case_id, "result_count": len(result["results"])})
    return result


@router.post("/load-cases/{load_case_id}/storage/upload", response_model=StructuredUploadResponse)
def upload_structured_result(load_case_id: str, payload: StructuredUpload, request: Request) -> dict[str, Any]:
    if not payload.filename.lower().endswith((".csv", ".json")):
        raise HTTPException(422, detail={"code": "SPDM_EXTENSION_INVALID", "message": "구조화 결과는 CSV 또는 JSON만 등록할 수 있습니다."})
    with connect() as conn:
        require_resource_permission(request, RESULT_IMPORT, "load_case", load_case_id, conn=conn)
        root = _root(conn); binding = _binding(conn, load_case_id)
        assert root.root is not None
        try:
            relative, checksum, size, reused = spdm_storage.write_upload(root.root, binding, "results", payload.filename, [payload.content.encode("utf-8")])
            spdm_storage._upsert_file(conn, load_case_id, relative, Path(relative).name, "results", size, checksum, "READY")
        except spdm_storage.SpdmStorageError as error:
            raise _error(error) from error
        refreshed = _refresh_bound(conn, request, binding, root)
        stored = next((item for item in refreshed["files"] if item["relative_path"] == relative), None)
        result = next((item for item in refreshed["results"] if item["relative_path"] == relative), None)
        _audit(request, conn, "SPDM_STRUCTURED_RESULT_UPLOADED", {"load_case_id": load_case_id, "relative_path": relative, "reused": reused})
    if result is None or result["status"] == "FAILED":
        raise HTTPException(422, detail={"code": "SPDM_RESULT_IMPORT_FAILED", "stored_file": stored})
    return {**result, "stored_file": stored}


@router.put(
    "/load-cases/{load_case_id}/storage/files",
    response_model=RawUploadResponse,
    openapi_extra={"requestBody": {"required": True, "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}},
)
async def upload_raw_file(
    load_case_id: str,
    request: Request,
    filename: str = Query(min_length=1, max_length=240),
    kind: Literal["solver", "media", "inputs", "reports"] = Query(),
) -> dict[str, Any]:
    with connect() as conn:
        require_resource_permission(request, RESULT_IMPORT, "load_case", load_case_id, conn=conn)
        root = _root(conn); binding = _binding(conn, load_case_id)
        assert root.root is not None
        with tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b") as spool:
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > 512 * 1024 * 1024:
                    raise HTTPException(413, detail={"code": "SPDM_FILE_TOO_LARGE"})
                spool.write(chunk)
            spool.seek(0)
            def chunks():
                while payload := spool.read(1024 * 1024):
                    yield payload
            try:
                relative, checksum, written, reused = spdm_storage.write_upload(root.root, binding, kind, filename, chunks())
                spdm_storage._upsert_file(conn, load_case_id, relative, Path(relative).name, kind, written, checksum, "READY")
            except spdm_storage.SpdmStorageError as error:
                raise _error(error) from error
        _audit(request, conn, "SPDM_RAW_FILE_UPLOADED", {"load_case_id": load_case_id, "relative_path": relative, "kind": kind, "reused": reused})
    return {"stored_file": {"relative_path": relative, "checksum": checksum, "size": written, "kind": kind, "reused": reused}}


@router.get("/storage/files/{file_id}/download")
def download_storage_file(file_id: str, request: Request):
    with connect() as conn:
        located = spdm_storage.file_locator(conn, file_id)
        if located is None:
            raise HTTPException(404, "원본 파일을 찾을 수 없습니다.")
        load_case_id, relative_path, name = located["load_case_id"], located["relative_path"], located["name"]
        require_resource_permission(request, PROJECT_DATA_VIEW, "load_case", load_case_id, conn=conn)
        root = _root(conn); binding = _binding(conn, load_case_id)
        assert root.root is not None
        try:
            path = spdm_storage.safe_file_path(root.root, binding, relative_path)
        except spdm_storage.SpdmStorageError as error:
            raise _error(error, 404) from error
    try:
        spdm_storage._assert_safe_existing(path, root.root)
        reader_context = spdm_storage.open_stable_reader(path)
        stream = reader_context.__enter__()
    except spdm_storage.SpdmStorageError as error:
        raise _error(error, 404) from error
    def stream_file():
        try:
            while chunk := stream.read(1024 * 1024):
                yield chunk
        finally:
            reader_context.__exit__(None, None, None)
    # An RFC 5987 value works for Unicode names and cannot inject a header from
    # an externally-created storage filename.
    disposition = "attachment; filename=download; filename*=UTF-8''" + quote(name, safe="")
    return StreamingResponse(stream_file(), media_type="application/octet-stream", headers={"Content-Disposition": disposition})
