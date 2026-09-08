"""HTTP API for the independent product/load-case CSV template library."""
from __future__ import annotations

import base64
from io import BytesIO
from datetime import datetime
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.routing import APIRoute
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..database_connection import connect
from ..modules.access_control import PROJECT_DATA_VIEW, SYSTEM_CATALOG_MANAGE, has_permission, require_permission
from ..services import modeling_templates

class _LimitedBodyRoute(APIRoute):
    """Reject an oversized JSON upload while Starlette is still receiving it."""
    max_body_bytes = 36 * 1024 * 1024

    def get_route_handler(self):  # type: ignore[no-untyped-def]
        handler = super().get_route_handler()

        async def limited(request: Request):
            receive = request._receive
            received = 0

            async def guarded_receive():
                nonlocal received
                message = await receive()
                if message["type"] == "http.request":
                    received += len(message.get("body", b""))
                    if received > self.max_body_bytes:
                        raise HTTPException(413, "템플릿 업로드 본문은 36 MiB를 초과할 수 없습니다.")
                return message

            request._receive = guarded_receive
            return await handler(request)

        return limited


router = APIRouter(prefix="/api/modeling-templates", tags=["modeling-templates"], route_class=_LimitedBodyRoute)

class CardCreate(BaseModel):
    name: str = Field(max_length=160)
    product_name: str = Field(max_length=160)
    load_case_name: str = Field(max_length=160)
    description: str = Field(default="", max_length=2000)

class FilePayload(BaseModel):
    relative_path: str = Field(max_length=1024)
    content_base64: str

class VersionCreate(BaseModel):
    expected_version: int = Field(ge=1)
    mode: Literal["merge", "replace"] = "merge"
    files: list[FilePayload] = Field(max_length=modeling_templates.MAX_FILES)

class FileResponse(BaseModel):
    id: str
    relative_path: str
    size_bytes: int
    checksum: str

class VersionResponse(BaseModel):
    version: int
    created_at: datetime
    file_count: int
    total_bytes: int

class CardResponse(BaseModel):
    id: str
    name: str
    product_name: str
    load_case_name: str
    description: str
    latest_version: int
    file_count: int
    total_bytes: int
    created_at: datetime
    updated_at: datetime

class DetailResponse(CardResponse):
    versions: list[VersionResponse]
    files: list[FileResponse]
    selected_version: VersionResponse

class CatalogResponse(BaseModel):
    items: list[CardResponse]
    products: list[str]
    load_cases: list[str]
    can_manage: bool

def _raise(error: modeling_templates.TemplateError) -> None:
    raise HTTPException(error.status_code, str(error)) from error

def _read(request: Request, conn: object) -> None:
    require_permission(request, PROJECT_DATA_VIEW, conn=conn)  # library access for active users

@router.get("", response_model=CatalogResponse)
def list_templates(request: Request, q: str | None = None, product_name: str | None = None, load_case_name: str | None = None) -> dict:
    with connect() as conn:
        _read(request, conn)
        result = modeling_templates.list_cards(conn, q=q, product_name=product_name, load_case_name=load_case_name)
        result["can_manage"] = has_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
        return result

@router.post("", status_code=201, response_model=DetailResponse)
def create_template(payload: CardCreate, request: Request) -> dict:
    with connect() as conn:
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
        try:
            return modeling_templates.create_card_atomic(conn, **payload.model_dump())
        except modeling_templates.TemplateError as error:
            _raise(error)

@router.get("/{template_id}", response_model=DetailResponse)
def get_template(template_id: str, request: Request) -> dict:
    with connect() as conn:
        _read(request, conn)
        try: return modeling_templates.detail(conn, template_id)
        except modeling_templates.TemplateError as error: _raise(error)

@router.get("/{template_id}/versions/{version}", response_model=DetailResponse)
def get_version(template_id: str, version: int, request: Request) -> dict:
    with connect() as conn:
        _read(request, conn)
        try: return modeling_templates.detail(conn, template_id, version)
        except modeling_templates.TemplateError as error: _raise(error)

@router.post("/{template_id}/versions", response_model=DetailResponse)
def add_version(template_id: str, payload: VersionCreate, request: Request) -> dict:
    incoming = []
    try:
        for file in payload.files:
            incoming.append(modeling_templates.IncomingFile(file.relative_path, base64.b64decode(file.content_base64, validate=True)))
    except (ValueError, TypeError) as error:
        raise HTTPException(422, "CSV Base64 데이터가 올바르지 않습니다.") from error
    with connect() as conn:
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
        try:
            return modeling_templates.write_version_atomic(conn, template_id, expected_version=payload.expected_version, mode=payload.mode, files=incoming)
        except modeling_templates.TemplateError as error:
            _raise(error)

def _snapshot(conn: object, template_id: str, version: int) -> list[dict]:
    try:
        modeling_templates.detail(conn, template_id, version)
        return modeling_templates._files(conn, template_id, version, content=True)
    except modeling_templates.TemplateError as error:
        _raise(error)

@router.get("/{template_id}/versions/{version}/download")
def download_version(template_id: str, version: int, request: Request) -> Response:
    import zipfile
    with connect() as conn:
        _read(request, conn); files = _snapshot(conn, template_id, version)
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file in files: archive.writestr(file["relative_path"], file["content"])
    filename = f"{template_id}-v{version}.zip"
    return Response(buffer.getvalue(), media_type="application/zip", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})

@router.get("/{template_id}/versions/{version}/files/{file_id}/download")
def download_file(template_id: str, version: int, file_id: str, request: Request) -> Response:
    with connect() as conn:
        _read(request, conn); files = _snapshot(conn, template_id, version)
    file = next((item for item in files if item["id"] == file_id), None)
    if file is None: raise HTTPException(404, "CSV 파일을 찾을 수 없습니다.")
    return Response(file["content"], media_type="text/csv", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(file['relative_path'].split('/')[-1])}"})
