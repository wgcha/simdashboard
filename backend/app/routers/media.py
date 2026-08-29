"""HTTP boundary for database-backed result media reads."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse

from .. import config as app_config
from ..database_connection import connect
from ..modules.access_control import PROJECT_DATA_VIEW, require_resource_permission
from ..repositories.media_repository import get_blob, get_media_asset
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
        item = get_media_asset(conn, asset_id)
        if not item:
            raise HTTPException(404, "결과 미디어를 찾을 수 없습니다.")
        require_resource_permission(request, PROJECT_DATA_VIEW, "media_asset", asset_id, conn=conn)
        if item.get("blob_id"):
            blob = get_blob(conn, str(item["blob_id"]))
            if blob is None:
                raise HTTPException(404, "결과 미디어 blob을 찾을 수 없습니다.")
            filename = item.get("original_filename") or Path(
                str(item.get("file_path") or "download")
            ).name
            return build_media_response(
                request,
                blob=blob,
                mime_type=str(item.get("mime_type") or "application/octet-stream"),
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
        path = _legacy_asset_path(str(item.get("file_path") or ""))
        return FileResponse(
            path,
            media_type=str(item.get("mime_type") or "application/octet-stream"),
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
