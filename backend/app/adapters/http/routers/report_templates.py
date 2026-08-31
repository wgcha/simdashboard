from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response

from ....adapters.documents.pptx_templates import PptxTemplateDocumentProcessor
from ....adapters.persistence.report_templates import SQLReportTemplateRepositoryProvider
from ....adapters.storage.report_template_files import ReportTemplateFileStore
from ....application.reports.template_commands import (
    create_report_template as create_report_template_command,
    deactivate_report_template as deactivate_report_template_command,
    render_report_template as render_report_template_command,
)
from ....application.reports.template_queries import list_report_templates as list_report_templates_query
from ....domains.reports.template_models import (
    InvalidReportTemplateError,
    ReportTemplateError,
    ReportTemplateFileCollisionError,
    ReportTemplateFileMissingError,
    ReportTemplateNotFoundError,
    UnsafeReportTemplateFileError,
)
from ....modules.access_control import REPORT_EXPORT, SYSTEM_CATALOG_MANAGE, require_permission
from ....schemas.api import ReportTemplateRenderPayload, ReportTemplateUploadPayload


router = APIRouter()


def _audit(request: Request) -> dict[str, Any]:
    principal = request.state.principal
    return {
        "user_id": principal.user_id,
        "username": principal.username,
        "role": principal.role,
        "method": request.method,
        "path": request.url.path,
        "request_id": getattr(request.state, "request_id", str(uuid4())),
        "client_ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent", "")[:500],
    }


def _manage(request: Request):
    return lambda: require_permission(request, SYSTEM_CATALOG_MANAGE)


def _export(request: Request):
    return lambda: require_permission(request, REPORT_EXPORT)


def _raise_mapped(error: ReportTemplateError) -> NoReturn:
    if isinstance(error, InvalidReportTemplateError):
        raise HTTPException(error.status_code, error.message) from error
    if isinstance(error, ReportTemplateNotFoundError):
        raise HTTPException(404, error.message) from error
    if isinstance(error, (ReportTemplateFileMissingError, UnsafeReportTemplateFileError)):
        raise HTTPException(410, "PPTX 템플릿 파일이 없습니다.") from error
    if isinstance(error, ReportTemplateFileCollisionError):
        raise HTTPException(409, "PPTX 템플릿 파일을 안전하게 저장할 수 없습니다.") from error
    raise error


@router.get(
    "/api/report-templates",
    operation_id="list_report_templates_api_report_templates_get",
)
def list_report_templates() -> list[dict[str, Any]]:
    # Legacy list access relies on the API middleware but has no explicit
    # catalog-management permission check.
    return list_report_templates_query(lambda: None, SQLReportTemplateRepositoryProvider())


@router.post(
    "/api/report-templates",
    status_code=201,
    operation_id="upload_report_template_api_report_templates_post",
)
def upload_report_template(payload: ReportTemplateUploadPayload, request: Request) -> dict[str, Any]:
    # Preserve legacy precedence: catalog permission was checked before the
    # endpoint inspected the filename or decoded body content.
    require_permission(request, SYSTEM_CATALOG_MANAGE)
    if not payload.filename.lower().endswith(".pptx") or payload.filename.lower().endswith(".pptm"):
        raise HTTPException(422, ".pptx 템플릿만 업로드할 수 있습니다.")
    try:
        data = base64.b64decode(payload.content_base64, validate=True)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, "PPTX Base64 데이터가 올바르지 않습니다.") from exc
    principal = request.state.principal
    try:
        return create_report_template_command(
            name=payload.name,
            filename=Path(payload.filename).name,
            data=data,
            actor_name=principal.display_name,
            audit=_audit(request),
            authorize=lambda: None,
            repository_provider=SQLReportTemplateRepositoryProvider(),
            files=ReportTemplateFileStore(),
            processor=PptxTemplateDocumentProcessor(),
        )
    except ReportTemplateError as error:
        _raise_mapped(error)


@router.post(
    "/api/report-templates/{template_id}/render",
    operation_id="render_report_template_api_report_templates__template_id__render_post",
)
def render_report_template(template_id: str, payload: ReportTemplateRenderPayload, request: Request) -> Response:
    try:
        rendered = render_report_template_command(
            template_id=template_id,
            replacements=payload.replacements,
            authorize=_export(request),
            repository_provider=SQLReportTemplateRepositoryProvider(),
            files=ReportTemplateFileStore(),
            processor=PptxTemplateDocumentProcessor(),
        )
    except ReportTemplateError as error:
        _raise_mapped(error)
    filename = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", Path(payload.filename).name)
    ascii_filename = re.sub(r"[^0-9A-Za-z._-]+", "_", filename) or "analysis-report.pptx"
    disposition = f'attachment; filename="{ascii_filename}"; filename*=UTF-8\'\'{quote(filename)}'
    return Response(
        content=rendered,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": disposition},
    )


@router.delete(
    "/api/report-templates/{template_id}",
    operation_id="delete_report_template_api_report_templates__template_id__delete",
)
def delete_report_template(template_id: str, request: Request) -> dict[str, str]:
    try:
        return deactivate_report_template_command(
            template_id=template_id,
            authorize=_manage(request),
            repository_provider=SQLReportTemplateRepositoryProvider(),
            files=ReportTemplateFileStore(),
        )
    except ReportTemplateError as error:
        _raise_mapped(error)
