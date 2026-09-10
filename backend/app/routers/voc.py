from __future__ import annotations

from tempfile import SpooledTemporaryFile
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from ..config import security_settings
from ..database_connection import connect
from ..access_policy import COMPANY_DASHBOARD_VIEW, SYSTEM_USER_APPROVE, require_permission
from ..repositories.voc_repository import VOCRepository
from ..schemas.voc import VOCPostCreate, VOCPostListResponse, VOCPostResponse
from ..services.voc_service import create_post, export_posts


router = APIRouter(tags=["voc"])


def _fresh_author_snapshot(request: Request, repository: VOCRepository) -> tuple[str, str, str]:
    """Use current database identity values, never data supplied by the client."""

    principal = request.state.principal
    current = repository.current_author(principal.user_id)
    if current is None:
        if principal.user_id == "local-admin" and security_settings().auth_mode == "disabled":
            return principal.user_id, principal.username, principal.display_name
        raise HTTPException(403, detail={"code": "PERMISSION_DENIED", "message": "이 작업을 수행할 권한이 없습니다."})
    return str(current["id"]), str(current["username"]), str(current["display_name"])


@router.get("/api/voc/posts", response_model=VOCPostListResponse)
def list_posts(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    with connect() as connection:
        require_permission(request, COMPANY_DASHBOARD_VIEW, conn=connection)
        items, total = VOCRepository(connection).list(limit=limit, offset=offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.post("/api/voc/posts", status_code=201, response_model=VOCPostResponse)
def post_message(payload: VOCPostCreate, request: Request) -> dict[str, object]:
    with connect() as connection:
        require_permission(request, COMPANY_DASHBOARD_VIEW, conn=connection)
        repository = VOCRepository(connection)
        author_user_id, author_username, author_display_name = _fresh_author_snapshot(request, repository)
        return create_post(
            repository,
            author_user_id=author_user_id,
            author_username=author_username,
            author_display_name=author_display_name,
            content=payload.content,
        )


@router.get("/api/voc/export")
def export_voc(
    request: Request,
    format: Literal["csv", "json"] = Query(...),
) -> StreamingResponse:
    # DuckDB serializes its connection lifetime.  Materialize the bounded-memory
    # export before returning StreamingResponse, so the response iterator never
    # carries a database lock into Starlette's worker thread.
    spool = SpooledTemporaryFile(max_size=1_024 * 1_024, mode="w+b")
    try:
        with connect() as connection:
            require_permission(request, SYSTEM_USER_APPROVE, conn=connection)
            for chunk in export_posts(format, connection):
                spool.write(chunk)
        spool.seek(0)
    except Exception:
        spool.close()
        raise

    media_type = "text/csv; charset=utf-8" if format == "csv" else "application/json; charset=utf-8"

    def stream():
        while chunk := spool.read(64 * 1024):
            yield chunk

    return StreamingResponse(
        stream(),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="voc-posts.{format}"'},
        background=BackgroundTask(spool.close),
    )
