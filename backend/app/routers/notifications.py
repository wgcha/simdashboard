"""Per-user notifications (top bar bell, ``/workspace/notifications``).

* ``GET /api/notifications`` — the signed-in user's notifications (newest first, paged,
  ``unread_only``/``type`` filters) with ``unread_count``.
* ``GET /api/notifications/unread-count`` — the badge (polled every 60 s and on focus).
* ``POST /api/notifications/read`` — mark ``ids`` or ``all`` of the user's own notifications read.

Rows are written by the event code (``services.notifications``); recipients were
filtered by project permission when the row was written.  A user only ever reads
or changes rows whose ``user_id`` is their own.  ``docs/features/notifications.md``.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..access_policy import COMPANY_DASHBOARD_VIEW, require_permission
from ..database_connection import connect
from ..services import notifications as service

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

NotificationSeverity = Literal["INFO", "SUCCESS", "WARNING", "ERROR"]


class NotificationItem(BaseModel):
    id: str
    type: str
    type_label: str
    severity: NotificationSeverity
    title: str
    body: str | None = None
    link: str | None = None
    project_id: str | None = None
    request_id: str | None = None
    created_at: str | None = None
    read_at: str | None = None
    read: bool


class NotificationType(BaseModel):
    type: str
    label: str


class NotificationList(BaseModel):
    items: list[NotificationItem]
    total: int
    unread_count: int
    limit: int
    offset: int
    types: list[NotificationType]


class NotificationUnreadCount(BaseModel):
    unread_count: int


class NotificationReadInput(BaseModel):
    ids: list[str] | None = Field(default=None, max_length=500)
    all: bool = False


class NotificationReadResult(BaseModel):
    updated: int
    unread_count: int


def _user_id(request: Request, conn) -> str:
    # Any ACTIVE account (company permission); a suspended or pending account gets 403.
    require_permission(request, COMPANY_DASHBOARD_VIEW, conn=conn)
    return str(request.state.principal.user_id)


@router.get("", response_model=NotificationList)
def list_notifications(request: Request, unread_only: bool = False,
                       type: str | None = Query(default=None, max_length=40),
                       limit: int = Query(default=50, ge=1, le=200),
                       offset: int = Query(default=0, ge=0, le=100_000)) -> NotificationList:
    if type is not None and type not in service.TYPES:
        raise HTTPException(422, {"code": "NOTIFICATION_TYPE_INVALID", "message": "알 수 없는 알림 종류입니다."})
    with connect() as conn:
        user_id = _user_id(request, conn)
        return NotificationList(**service.list_for_user(conn, user_id, unread_only=unread_only, type=type,
                                                        limit=limit, offset=offset))


@router.get("/unread-count", response_model=NotificationUnreadCount)
def unread_count(request: Request) -> NotificationUnreadCount:
    with connect() as conn:
        return NotificationUnreadCount(unread_count=service.unread_count(conn, _user_id(request, conn)))


@router.post("/read", response_model=NotificationReadResult)
def mark_read(payload: NotificationReadInput, request: Request) -> NotificationReadResult:
    if not payload.all and not payload.ids:
        raise HTTPException(422, {"code": "NOTIFICATION_READ_TARGET_REQUIRED", "message": "읽음 처리할 알림을 선택하세요."})
    with connect() as conn:
        user_id = _user_id(request, conn)
        updated = service.mark_read(conn, user_id, ids=payload.ids, all=payload.all)
        return NotificationReadResult(updated=updated, unread_count=service.unread_count(conn, user_id))
