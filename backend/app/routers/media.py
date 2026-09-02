"""HTTP boundary for database-backed result media reads."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse

from .. import config as app_config
from ..adapters.persistence.media import SQLResultMediaQuery
from ..application.results.queries import get_result_media_read
from ..database_connection import connect
from ..modules.access_control import PROJECT_DATA_VIEW, require_resource_permission
from ..services.media_http import build_media_response
from ..security import write_audit_event


router = APIRouter(prefix="/api")


def media_storage_mode() -> app_config.MediaStorageMode:
    """Read media cutover policy at request time and keep it patchable."""
    return app_config.media_storage_mode()


def _media_audit_callback(
    request: Request,
    action: str,
    bytes_yielded: int,
    status_code: int,
) -> None:
    try:
        with connect() as audit_connection:
            write_audit_event(
                request=request,
                principal=getattr(request.state, "principal", None),
                status_code=status_code,
                action=f"MEDIA_STREAM_{action}",
                detail={"bytes_yielded_to_asgi": bytes_yielded},
                connection=audit_connection,
            )
    except Exception:
        # Observability must not turn a successful media response into a 5xx.
        return


def _legacy_asset_path(file_path: str) -> Path:
    assets_root = (Path(__file__).resolve().parents[2] / "assets").resolve()
    relative_path = Path(file_path)
    if relative_path.parts and relative_path.parts[0].lower() == "assets":
        relative_path = Path(*relative_path.parts[1:])
    path = (assets_root / relative_path).resolve()
    if assets_root not in path.parents or not path.is_file():
        raise HTTPException(404, "결과 미디어 파일을 찾을 수 없습니다.")
    return path


def _result_asset_response(asset_id: str, request: Request, *, download: bool) -> Response:
    with connect() as conn:
        media = get_result_media_read(
            SQLResultMediaQuery(conn),
            asset_id,
            authorize=lambda _asset: require_resource_permission(
                request,
                PROJECT_DATA_VIEW,
                "media_asset",
                asset_id,
                conn=conn,
            ),
        )
        if media is None:
            raise HTTPException(404, "결과 미디어를 찾을 수 없습니다.")
        if media.asset.blob_id:
            if media.blob is None:
                raise HTTPException(404, "결과 미디어 blob을 찾을 수 없습니다.")
            filename = media.asset.original_filename or Path(
                media.asset.file_path or "download"
            ).name
            return build_media_response(
                request,
                blob=media.blob,
                mime_type=media.asset.mime_type or "application/octet-stream",
                filename=str(filename),
                download=download,
                audit=lambda action, yielded, status: _media_audit_callback(
                    request,
                    action,
                    yielded,
                    status,
                ),
            )
        if media_storage_mode() == "database-only":
            raise HTTPException(404, "결과 미디어 blob을 찾을 수 없습니다.")
        path = _legacy_asset_path(media.asset.file_path or "")
        return FileResponse(
            path,
            media_type=media.asset.mime_type or "application/octet-stream",
            filename=path.name if download else None,
        )


@router.get("/assets/{asset_id}", operation_id="get_result_asset")
@router.head("/assets/{asset_id}", include_in_schema=False)
def get_result_asset(asset_id: str, request: Request) -> Response:
    return _result_asset_response(asset_id, request, download=False)


@router.get("/assets/{asset_id}/download", operation_id="download_result_asset")
@router.head("/assets/{asset_id}/download", include_in_schema=False)
def download_result_asset(asset_id: str, request: Request) -> Response:
    return _result_asset_response(asset_id, request, download=True)
