"""HTTP API for staged, reviewed result registration."""
from __future__ import annotations

from email import policy
from email.parser import BytesFeedParser
from pathlib import PurePosixPath
from tempfile import SpooledTemporaryFile
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.routing import APIRoute
from fastapi.responses import Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..database_connection import connect
from ..modules.access_control import RESULT_IMPORT, has_permission, require_any_project_permission, require_resource_permission
from ..security import write_audit_event
from ..services import result_drop_upload as drop_service
from ..services import result_registration as service
from ..services import result_registration_locations as location_service
from ..services.result_registration_paths import ResultRegistrationError


class _UploadLimitRoute(APIRoute):
    """Reject clearly oversized multipart requests before Starlette spools files."""

    def get_route_handler(self):  # type: ignore[no-untyped-def]
        handler = super().get_route_handler()

        async def limited(request: Request):
            if request.method == "POST" and request.url.path.endswith("/files"):
                raw = request.headers.get("content-length")
                if raw is not None:
                    try:
                        size = int(raw)
                    except ValueError as exc:
                        raise HTTPException(400, {"code": "RESULT_CONTENT_LENGTH_INVALID"}) from exc
                    if size < 0 or size > service.MAX_TOTAL_BYTES + 2 * 1024 * 1024:
                        raise HTTPException(413, {"code": "RESULT_TOTAL_SIZE_LIMIT", "message": "초안의 전체 파일은 256 MiB 이하여야 합니다."})
            return await handler(request)

        return limited


router = APIRouter(prefix="/api/result-registration", tags=["result-registration"], route_class=_UploadLimitRoute)


class FolderSegment(BaseModel):
    role_kind: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=128)


class PrepareFoldersInput(BaseModel):
    project_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    environment: Literal["USAGE", "DISTRIBUTION"]
    parent_relative_path: str | None = Field(default=None, max_length=2048)
    segments: list[FolderSegment] = Field(min_length=1, max_length=8)
    confirm_create: bool = False


class ResultLocationInput(BaseModel):
    project_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    environment: Literal["USAGE", "DISTRIBUTION"]
    relative_path: str = Field(min_length=1, max_length=2048)


class UpdateResultLocationInput(ResultLocationInput):
    revision: int = Field(ge=1)


class DraftManifestFile(BaseModel):
    relative_path: str = Field(min_length=1, max_length=1024)
    size: int = Field(ge=0, le=service.MAX_FILE_BYTES)
    sha256: str | None = Field(default=None, max_length=64)
    media_type: str = Field(default="", max_length=128)


class CreateDraftInput(BaseModel):
    project_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    environment: Literal["USAGE", "DISTRIBUTION"]
    case_relative_path: str = Field(min_length=1, max_length=2048)
    result_relative_path: str = Field(min_length=1, max_length=2048)
    context: dict[str, Any]
    files: list[DraftManifestFile] = Field(min_length=1, max_length=service.MAX_FILES)


class FileExclusion(BaseModel):
    relative_path: str = Field(min_length=1, max_length=1024)
    reason: str = Field(min_length=1, max_length=500)


class InspectDraftInput(BaseModel):
    exclusions: list[FileExclusion] = Field(default_factory=list, max_length=service.MAX_FILES)


class ApproveDraftInput(BaseModel):
    inspection_revision: str | int
    acknowledge_partial: bool = False
    exclusions: list[FileExclusion] = Field(default_factory=list, max_length=service.MAX_FILES)


class PublishDraftInput(BaseModel):
    inspection_revision: str | int
    idempotency_key: str = Field(min_length=8, max_length=128)


def _parse_multipart_message(body: SpooledTemporaryFile[bytes]):
    """Parse bounded raw bytes without universal-newline conversion."""
    parser = BytesFeedParser(policy=policy.default)
    body.seek(0)
    while chunk := body.read(64 * 1024):
        parser.feed(chunk)
    return parser.close()


def _http_error(exc: ResultRegistrationError) -> HTTPException:
    code = exc.code
    if code in {"RESULT_DRAFT_NOT_FOUND", "RESULT_LOCATION_LINK_NOT_FOUND"}:
        status = 404
    elif code.startswith("FOLDER_SCHEMA_") or code.startswith("RESULT_LOCATION_SCHEMA_") or code in {
        "RESULT_DRAFT_IMMUTABLE", "RESULT_INSPECTION_STALE", "RESULT_APPROVAL_STALE", "RESULT_APPROVAL_REQUIRED",
        "RESULT_INSPECTION_REQUIRED", "RESULT_INSPECTION_BLOCKING", "RESULT_PARTIAL_ACK_REQUIRED",
        "RESULT_APPROVAL_EMPTY", "RESULT_IDEMPOTENCY_CONFLICT", "RESULT_CONTEXT_CHANGED", "RESULT_ROOT_CHANGED",
        "RESULT_MIRROR_NOT_READY", "RESULT_SOURCE_STALE", "RESULT_SOURCE_MISSING", "RESULT_DRAFT_STALE",
        "RESULT_EXCLUSIONS_STALE", "RESULT_FOLDER_SCHEMA_REQUIRED", "RESULT_FOLDER_SCHEMA_STALE",
        "RESULT_FOLDER_SCHEMA_AMBIGUOUS", "RESULT_FOLDER_SCHEMA_REFRESH_REQUIRED",
        "RESULT_LOCATION_SCHEMA_INVALID", "RESULT_LOCATION_LINK_EXISTS", "RESULT_LOCATION_LINK_STALE",
        "RESULT_PATH_OWNERSHIP_CONFLICT", "SPDM_FOLDER_MISSING", "SPDM_FOLDER_UNAVAILABLE",
    }:
        status = 409
    elif code in {"RESULT_FILE_SIZE_LIMIT", "RESULT_TOTAL_SIZE_LIMIT", "RESULT_FILE_COUNT_LIMIT"}:
        status = 413
    else:
        status = 422
    return HTTPException(status, {"code": code, "message": str(exc)})


def _draft_access(request: Request, conn: Any, draft_id: str) -> dict[str, str]:
    try:
        scope = service.draft_scope(conn, draft_id)
    except ResultRegistrationError as exc:
        raise _http_error(exc) from exc
    require_resource_permission(request, RESULT_IMPORT, "request", scope["request_id"], conn=conn)
    return scope


async def _multipart_files(request: Request) -> list[tuple[str, bytes]]:
    """Parse the browser's repeated files/relative_paths fields without runtime packages."""
    content_type = request.headers.get("content-type", "")
    marker = "boundary="
    if "multipart/form-data" not in content_type or marker not in content_type:
        raise HTTPException(415, {"code": "RESULT_MULTIPART_REQUIRED"})
    try:
        boundary = content_type.split(marker, 1)[1].strip().strip('"').encode("ascii", "strict")
    except UnicodeError as exc:
        raise HTTPException(422, {"code": "RESULT_MULTIPART_INVALID"}) from exc
    if not boundary or len(boundary) > 200:
        raise HTTPException(422, {"code": "RESULT_MULTIPART_INVALID"})
    limit = service.MAX_TOTAL_BYTES + 2 * 1024 * 1024
    declared = request.headers.get("content-length")
    if declared is not None and (not declared.isdigit() or int(declared) > limit):
        raise HTTPException(413, {"code": "RESULT_TOTAL_SIZE_LIMIT", "message": "초안의 전체 파일은 256 MiB 이하여야 합니다."})
    with SpooledTemporaryFile(max_size=1024 * 1024) as body:
        body.write((f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n").encode("ascii"))
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > limit:
                raise HTTPException(413, {"code": "RESULT_TOTAL_SIZE_LIMIT", "message": "초안의 전체 파일은 256 MiB 이하여야 합니다."})
            body.write(chunk)
        body.seek(0)
        try:
            # Feed the parser from the bounded binary spool. BytesParser.parse()
            # wraps the stream in TextIOWrapper, whose universal-newline mode
            # can rewrite CR/CRLF octets inside uploaded media bodies.
            message = await run_in_threadpool(_parse_multipart_message, body)
        except (ValueError, UnicodeError) as exc:
            raise HTTPException(422, {"code": "RESULT_MULTIPART_INVALID"}) from exc
    if not message.is_multipart() or message.defects:
        raise HTTPException(422, {"code": "RESULT_MULTIPART_INVALID"})
    files: list[tuple[str, bytes]] = []
    paths: list[str] = []
    part_count = 0
    for part in message.iter_parts():
        part_count += 1
        if part_count > service.MAX_FILES * 2 or part.get("Content-Transfer-Encoding"):
            raise HTTPException(422, {"code": "RESULT_MULTIPART_INVALID"})
        name = part.get_param("name", header="content-disposition")
        if not name:
            raise HTTPException(422, {"code": "RESULT_MULTIPART_INVALID"})
        value = part.get_payload(decode=True) or b""
        if name == "files":
            if not part.get_filename():
                raise HTTPException(422, {"code": "RESULT_MULTIPART_INVALID"})
            if len(value) > service.MAX_FILE_BYTES:
                raise HTTPException(413, {"code": "RESULT_FILE_SIZE_LIMIT", "message": "파일은 32 MiB 이하여야 합니다."})
            files.append((part.get_filename(), value))
        elif name == "relative_paths":
            try:
                paths.append(value.decode("utf-8"))
            except UnicodeError as exc:
                raise HTTPException(422, {"code": "RESULT_MULTIPART_INVALID"}) from exc
        else:
            raise HTTPException(422, {"code": "RESULT_MULTIPART_INVALID"})
    if not files or len(files) != len(paths) or len(files) > service.MAX_FILES:
        raise HTTPException(422, {"code": "RESULT_UPLOAD_FIELDS_MISMATCH", "message": "각 파일의 상대 경로를 확인하세요."})
    total_files = sum(len(content) for _, content in files)
    if total_files > service.MAX_TOTAL_BYTES:
        raise HTTPException(413, {"code": "RESULT_TOTAL_SIZE_LIMIT", "message": "한 번의 업로드는 256 MiB 이하여야 합니다."})
    normalized: list[tuple[str, bytes]] = []
    for (filename, content), relative in zip(files, paths):
        if PurePosixPath(relative.replace("\\", "/")).name.casefold() != PurePosixPath(filename.replace("\\", "/")).name.casefold():
            raise HTTPException(422, {"code": "RESULT_UPLOAD_FILENAME_MISMATCH"})
        normalized.append((relative, content))
    return normalized


@router.get("/targets")
def list_targets(request: Request, environment: Literal["USAGE", "DISTRIBUTION"]):
    with connect() as conn:
        project_ids = service.target_project_ids(conn)
        allowed = {project_id for project_id in project_ids if has_permission(request, RESULT_IMPORT, project_id, conn=conn)}
        if not allowed:
            require_any_project_permission(request, RESULT_IMPORT, conn=conn)
        try:
            return service.targets(conn, environment, allowed)
        except ResultRegistrationError as exc:
            raise _http_error(exc) from exc


@router.get("/folders")
def list_folders(request: Request, project_id: str, request_id: str,
                 environment: Literal["USAGE", "DISTRIBUTION"],
                 parent_relative_path: str | None = Query(default=None, max_length=2048)):
    with connect() as conn:
        require_resource_permission(request, RESULT_IMPORT, "request", request_id, conn=conn)
        try:
            return service.folders(conn, project_id, request_id, environment, parent_relative_path)
        except ResultRegistrationError as exc:
            raise _http_error(exc) from exc


@router.get("/locations")
def list_result_locations(request: Request, project_id: str, request_id: str,
                          environment: Literal["USAGE", "DISTRIBUTION"]):
    with connect() as conn:
        require_resource_permission(request, RESULT_IMPORT, "request", request_id, conn=conn)
        try:
            return location_service.list_links(conn, project_id, request_id, environment)
        except ResultRegistrationError as exc:
            raise _http_error(exc) from exc


@router.post("/locations", status_code=201)
def create_result_location(payload: ResultLocationInput, request: Request):
    with connect() as conn:
        require_resource_permission(request, RESULT_IMPORT, "request", payload.request_id, conn=conn)
        try:
            return location_service.create_link(
                conn, payload.project_id, payload.request_id, payload.environment,
                payload.relative_path, request.state.principal.user_id,
            )
        except ResultRegistrationError as exc:
            raise _http_error(exc) from exc


@router.patch("/locations/{link_id}")
def update_result_location(link_id: str, payload: UpdateResultLocationInput, request: Request):
    with connect() as conn:
        require_resource_permission(request, RESULT_IMPORT, "request", payload.request_id, conn=conn)
        try:
            return location_service.update_link(
                conn, link_id, payload.project_id, payload.request_id, payload.environment,
                payload.relative_path, payload.revision, request.state.principal.user_id,
            )
        except ResultRegistrationError as exc:
            raise _http_error(exc) from exc


@router.delete("/locations/{link_id}")
def delete_result_location(link_id: str, request: Request, project_id: str, request_id: str,
                           environment: Literal["USAGE", "DISTRIBUTION"], revision: int = Query(ge=1)):
    with connect() as conn:
        require_resource_permission(request, RESULT_IMPORT, "request", request_id, conn=conn)
        try:
            return location_service.delete_link(conn, link_id, project_id, request_id, environment, revision)
        except ResultRegistrationError as exc:
            raise _http_error(exc) from exc


@router.post("/folders/prepare")
def prepare_folders(payload: PrepareFoldersInput, request: Request):
    with connect() as conn:
        require_resource_permission(request, RESULT_IMPORT, "request", payload.request_id, conn=conn)
        try:
            return service.prepare_folders(conn, payload.project_id, payload.request_id, payload.environment,
                                           payload.parent_relative_path,
                                           [item.model_dump() for item in payload.segments], payload.confirm_create,
                                           request.state.principal.user_id)
        except ResultRegistrationError as exc:
            raise _http_error(exc) from exc


@router.post("/drafts", status_code=201)
def create_draft(payload: CreateDraftInput, request: Request):
    with connect() as conn:
        require_resource_permission(request, RESULT_IMPORT, "request", payload.request_id, conn=conn)
        try:
            return service.create_draft(conn, payload.model_dump(), request.state.principal.user_id)
        except ResultRegistrationError as exc:
            raise _http_error(exc) from exc


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: str, request: Request):
    with connect() as conn:
        _draft_access(request, conn, draft_id)
        try:
            return service.read_draft(conn, draft_id)
        except ResultRegistrationError as exc:
            raise _http_error(exc) from exc


@router.post("/drafts/{draft_id}/files", openapi_extra={"requestBody": {"required": True, "content": {
    "multipart/form-data": {"schema": {"type": "object", "required": ["files", "relative_paths"], "properties": {
        "files": {"type": "array", "items": {"type": "string", "format": "binary"}},
        "relative_paths": {"type": "array", "items": {"type": "string"}},
    }}}
}}})
async def upload_draft_files(draft_id: str, request: Request):
    with connect() as conn:
        _draft_access(request, conn, draft_id)
    uploaded = await _multipart_files(request)
    with connect() as conn:
        _draft_access(request, conn, draft_id)
        try:
            return service.upload_files(conn, draft_id, uploaded, request.state.principal.user_id)
        except ResultRegistrationError as exc:
            raise _http_error(exc) from exc


@router.post("/drafts/{draft_id}/inspect")
def inspect_draft(draft_id: str, request: Request, payload: InspectDraftInput | None = None):
    with connect() as conn:
        _draft_access(request, conn, draft_id)
        try:
            return service.inspect_draft(conn, draft_id, request.state.principal.user_id,
                                         [item.model_dump() for item in payload.exclusions] if payload else [])
        except ResultRegistrationError as exc:
            raise _http_error(exc) from exc


@router.post("/drafts/{draft_id}/approve")
def approve_draft(draft_id: str, payload: ApproveDraftInput, request: Request):
    with connect() as conn:
        _draft_access(request, conn, draft_id)
        try:
            return service.approve_draft(conn, draft_id, payload.inspection_revision, payload.acknowledge_partial,
                                         [item.model_dump() for item in payload.exclusions], request.state.principal.user_id)
        except ResultRegistrationError as exc:
            raise _http_error(exc) from exc


@router.post("/drafts/{draft_id}/publish")
def publish_draft(draft_id: str, payload: PublishDraftInput, request: Request):
    with connect() as conn:
        _draft_access(request, conn, draft_id)
        try:
            return service.publish_draft(conn, draft_id, payload.inspection_revision,
                                         payload.idempotency_key, request.state.principal.user_id)
        except ResultRegistrationError as exc:
            raise _http_error(exc) from exc


@router.post("/drafts/{draft_id}/mirror/retry")
def retry_mirror(draft_id: str, request: Request):
    with connect() as conn:
        _draft_access(request, conn, draft_id)
        try:
            return service.retry_mirror(conn, draft_id, request.state.principal.user_id)
        except ResultRegistrationError as exc:
            raise _http_error(exc) from exc


@router.get("/drafts/{draft_id}/media")
def draft_media(draft_id: str, request: Request,
                relative_path: str = Query(min_length=1, max_length=1024)):
    with connect() as conn:
        _draft_access(request, conn, draft_id)
        try:
            item = service.preview_media(conn, draft_id, relative_path)
        except ResultRegistrationError as exc:
            raise _http_error(exc) from exc
    filename = quote(item["filename"])
    return Response(item["content"], media_type=item["media_type"], headers={
        "Content-Disposition": f"inline; filename*=UTF-8''{filename}",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
    })


# ---------------------------------------------------------------------------
# W8: drag & drop of files or whole folders into the request's Working tree.
# No drafts, checks or approval; the folder sync assigns roles by depth.
# ---------------------------------------------------------------------------

class DropFile(BaseModel):
    relative_path: str = Field(min_length=1, max_length=2048)
    size: int = Field(ge=0)
    sha256: str | None = Field(default=None, max_length=64)


class DropPlanInput(BaseModel):
    project_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    environment: Literal["USAGE", "DISTRIBUTION"]
    target_relative_path: str = Field(min_length=1, max_length=2048)
    files: list[DropFile] = Field(default_factory=list, max_length=drop_service.MAX_FILES)
    folders: list[str] = Field(default_factory=list, max_length=drop_service.MAX_FOLDERS)


class DropFolderInput(BaseModel):
    project_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    environment: Literal["USAGE", "DISTRIBUTION"]
    parent_relative_path: str = Field(min_length=1, max_length=2048)
    name: str = Field(min_length=1, max_length=255)
    confirm: bool = False


def _drop_error(exc: ResultRegistrationError) -> HTTPException:
    if isinstance(exc, drop_service.DropUploadError):
        return HTTPException(exc.status, {"code": exc.code, "message": str(exc), **exc.extra})
    return _http_error(exc)


def _drop_audit(request: Request, action: str, detail: dict[str, Any], *, status_code: int = 200,
                conn: Any | None = None) -> None:
    write_audit_event(request=request, principal=request.state.principal, status_code=status_code,
                      action=action, detail=detail, connection=conn)


def _session_access(request: Request, conn: Any, session_id: str) -> dict[str, str]:
    try:
        scope = drop_service.session_scope(session_id)
    except ResultRegistrationError as exc:
        raise _drop_error(exc) from exc
    require_resource_permission(request, RESULT_IMPORT, "request", scope["request_id"], conn=conn)
    return scope


@router.get("/drafts")
def list_legacy_drafts(request: Request, project_id: str, request_id: str,
                       environment: Literal["USAGE", "DISTRIBUTION"]):
    """Read-only history of drafts made with the pre-W8 registration screen."""
    with connect() as conn:
        require_resource_permission(request, RESULT_IMPORT, "request", request_id, conn=conn)
        try:
            return service.list_drafts(conn, project_id, request_id, environment)
        except ResultRegistrationError as exc:
            raise _http_error(exc) from exc


@router.get("/drop-target")
def drop_target(request: Request, project_id: str, request_id: str,
                environment: Literal["USAGE", "DISTRIBUTION"]):
    """The request's Working tree by DEPTH_V1 level and the paths users see in Explorer."""
    with connect() as conn:
        require_resource_permission(request, RESULT_IMPORT, "request", request_id, conn=conn)
        try:
            return drop_service.tree(conn, project_id, request_id, environment)
        except ResultRegistrationError as exc:
            raise _drop_error(exc) from exc


@router.post("/drop-target/folders", status_code=201)
def drop_create_folder(payload: DropFolderInput, request: Request):
    with connect() as conn:
        require_resource_permission(request, RESULT_IMPORT, "request", payload.request_id, conn=conn)
        try:
            result = drop_service.create_folder(conn, payload.project_id, payload.request_id, payload.environment,
                                                payload.parent_relative_path, payload.name, confirm=payload.confirm,
                                                user_id=request.state.principal.user_id)
        except ResultRegistrationError as exc:
            raise _drop_error(exc) from exc
        _drop_audit(request, "RESULT_DROP_FOLDER_CREATED", {
            "project_id": payload.project_id, "request_id": payload.request_id, "environment": payload.environment,
            "relative_path": result["relative_path"], "role": result["role"]}, conn=conn)
        return result


@router.post("/drop-uploads/plan")
def drop_plan(payload: DropPlanInput, request: Request):
    """Everything the upload would do, checked before any write (no side effects)."""
    with connect() as conn:
        require_resource_permission(request, RESULT_IMPORT, "request", payload.request_id, conn=conn)
        try:
            plan, _internal = drop_service.build_plan(
                conn, payload.project_id, payload.request_id, payload.environment, payload.target_relative_path,
                [item.model_dump() for item in payload.files], payload.folders)
        except ResultRegistrationError as exc:
            raise _drop_error(exc) from exc
        return plan


@router.post("/drop-uploads", status_code=201)
def drop_create_session(payload: DropPlanInput, request: Request):
    with connect() as conn:
        require_resource_permission(request, RESULT_IMPORT, "request", payload.request_id, conn=conn)
        try:
            session, plan = drop_service.create_session(
                conn, payload.project_id, payload.request_id, payload.environment, payload.target_relative_path,
                [item.model_dump() for item in payload.files], payload.folders, request.state.principal.user_id)
        except ResultRegistrationError as exc:
            raise _drop_error(exc) from exc
        _drop_audit(request, "RESULT_DROP_UPLOAD_STARTED", {
            "session_id": session["session_id"], "project_id": payload.project_id, "request_id": payload.request_id,
            "environment": payload.environment, "target": plan["target_relative_path"],
            "files": plan["file_count"], "bytes": plan["total_bytes"], "folders": plan["folder_count"],
            "skipped": len(plan["skipped"])}, conn=conn)
        return {**session, "plan": plan}


@router.get("/drop-uploads/{session_id}")
def drop_read_session(session_id: str, request: Request):
    with connect() as conn:
        _session_access(request, conn, session_id)
    try:
        return drop_service.read_session(session_id, request.state.principal.user_id)
    except ResultRegistrationError as exc:
        raise _drop_error(exc) from exc


@router.put("/drop-uploads/{session_id}/files/{index}", openapi_extra={"requestBody": {"required": True, "content": {
    "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}})
async def drop_upload_chunk(session_id: str, index: int, request: Request, offset: int = Query(ge=0)):
    """One chunk (≤ 8 MiB) at ``offset``; ``X-Chunk-SHA256`` is optional (browsers without WebCrypto on HTTP)."""
    with connect() as conn:
        _session_access(request, conn, session_id)
    declared = request.headers.get("content-length")
    if declared is not None and (not declared.isdigit() or int(declared) > drop_service.CHUNK_BYTES):
        raise HTTPException(413, {"code": "RESULT_DROP_CHUNK_TOO_LARGE", "message": "조각이 너무 큽니다."})
    body = bytearray()
    async for part in request.stream():
        body.extend(part)
        if len(body) > drop_service.CHUNK_BYTES:
            raise HTTPException(413, {"code": "RESULT_DROP_CHUNK_TOO_LARGE", "message": "조각이 너무 큽니다."})
    try:
        return await run_in_threadpool(drop_service.upload_chunk, session_id, request.state.principal.user_id,
                                       index, offset, bytes(body), request.headers.get("x-chunk-sha256"))
    except ResultRegistrationError as exc:
        raise _drop_error(exc) from exc


@router.post("/drop-uploads/{session_id}/complete")
def drop_complete(session_id: str, request: Request):
    with connect() as conn:
        scope = _session_access(request, conn, session_id)
        try:
            result = drop_service.complete(conn, session_id, request.state.principal.user_id)
        except ResultRegistrationError as exc:
            error = _drop_error(exc)
            _drop_audit(request, "RESULT_DROP_UPLOAD_FAILED", {"session_id": session_id, **scope, "code": exc.code},
                        status_code=error.status_code)
            raise error from exc
        _drop_audit(request, "RESULT_DROP_UPLOAD_PUBLISHED" if result["state"] == "PUBLISHED" else "RESULT_DROP_UPLOAD_PARTIAL", {
            "session_id": session_id, **scope, "target": result["target_relative_path"],
            "files": result["published_files"], "bytes": result["published_bytes"],
            "folders_created": len(result["created_folders"]), "conflicts": len(result["conflicts"]),
            "skipped": len(result["skipped"]), "sync": (result.get("sync") or {}).get("status")}, conn=conn)
        return result


@router.delete("/drop-uploads/{session_id}")
def drop_abort(session_id: str, request: Request):
    with connect() as conn:
        scope = _session_access(request, conn, session_id)
        try:
            result = drop_service.abort(session_id, request.state.principal.user_id)
        except ResultRegistrationError as exc:
            raise _drop_error(exc) from exc
        _drop_audit(request, "RESULT_DROP_UPLOAD_ABORTED", {
            "session_id": session_id, **scope, "received_bytes": result["received_bytes"],
            "files": result["file_count"]}, conn=conn)
        return result
