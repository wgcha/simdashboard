"""Global-administrator database backups (``/api/admin/db-backups``; app/services/db_backup.py).

* ``POST``   start one background backup (409 ``DB_BACKUP_RUNNING`` while another runs).
* ``GET``    backup folder, retention, existing backups and the running / last job.
* ``GET /{id}/download`` stream the dump file.  ``DELETE /{id}`` remove a backup and its manifest.
Audit events: ``BACKUP_CREATED`` (also on failure, with the error code), ``BACKUP_DOWNLOADED``, ``BACKUP_DELETED``.
Restore is not part of the web application (docs/deployment-security-backup-guide.md §6).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..security import write_audit_event
from ..services import db_backup

logger = logging.getLogger("app.routers.db_backups")
router = APIRouter(prefix="/api/admin/db-backups", tags=["db-backups"])


class DbBackupStart(BaseModel):
    memo: str | None = Field(default=None, max_length=db_backup.MAX_MEMO)


class DbBackupError(BaseModel):
    code: str
    message: str
    stage: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class DbBackupBackup(BaseModel):
    id: str
    path: str
    size: int
    created_at: str


class DbBackupJob(BaseModel):
    status: str   # running | succeeded | failed
    reason: str
    actor: str | None = None
    memo: str | None = None
    started_at: str
    finished_at: str | None = None
    stage: str | None = None
    backup: DbBackupBackup | None = None
    error: DbBackupError | None = None


class DbBackupItem(BaseModel):
    id: str
    file: str
    size: int
    created_at: str
    reason: str | None = None
    kind: str | None = None          # manual | automatic
    memo: str | None = None
    created_by: str | None = None
    status: str                      # ok | no_manifest | size_mismatch
    app_version: str | None = None
    commit: str | None = None
    database: str | None = None
    sha256: str | None = None
    format: str | None = None


class DbBackupOverview(BaseModel):
    directory: str
    keep_automatic: int
    backend: str
    backups: list[DbBackupItem]
    job: DbBackupJob | None = None
    restore_command: str


class DbBackupDeleted(BaseModel):
    id: str
    deleted: bool = True


def _principal(request: Request):
    principal = getattr(request.state, "principal", None)
    if principal is None or not principal.is_global_admin:
        raise HTTPException(403, {"code": "GLOBAL_ADMIN_REQUIRED", "message": "DB 백업은 전체 관리자 기능입니다."})
    return principal


def _fail(error: db_backup.BackupError) -> HTTPException:
    return HTTPException(error.status, error.as_dict())


def _audit(request: Request, principal: Any, status: int, action: str, detail: dict[str, Any]) -> None:
    try:
        write_audit_event(request=request, principal=principal, status_code=status, action=action, detail=detail)
    except Exception:  # noqa: BLE001 - the audit failure must not hide the result
        logger.error("DB backup audit %s could not be written", action)


@router.get("", response_model=DbBackupOverview)
async def list_db_backups(request: Request) -> dict[str, Any]:
    _principal(request)
    try:
        return await run_in_threadpool(db_backup.overview)
    except db_backup.BackupError as error:
        raise _fail(error) from None


@router.post("", response_model=DbBackupJob, status_code=202)
async def start_db_backup(request: Request, payload: DbBackupStart | None = None) -> dict[str, Any]:
    principal = _principal(request)
    memo = payload.memo if payload else None

    def finished(job: dict[str, Any]) -> None:
        backup = job.get("backup") or {}
        error = job.get("error") or {}
        _audit(request, principal, 200 if job["status"] == "succeeded" else 500, "BACKUP_CREATED",
               {"result": job["status"], "reason": job["reason"], "memo": job.get("memo"), "id": backup.get("id"),
                "size": backup.get("size"), "error_code": error.get("code"), "stage": error.get("stage")})

    try:
        return await run_in_threadpool(lambda: db_backup.start_job(db_backup.MANUAL_REASON, principal.user_id,
                                                                    memo=memo, on_finish=finished))
    except db_backup.BackupError as error:
        raise _fail(error) from None


@router.get("/{backup_id}/download")
async def download_db_backup(backup_id: str, request: Request) -> FileResponse:
    principal = _principal(request)
    try:
        path = db_backup.resolve(backup_id)
    except db_backup.BackupError as error:
        raise _fail(error) from None
    _audit(request, principal, 200, "BACKUP_DOWNLOADED", {"id": backup_id, "size": path.stat().st_size})
    return FileResponse(path, media_type="application/octet-stream", filename=path.name,
                        headers={"Cache-Control": "no-store"})


@router.delete("/{backup_id}", response_model=DbBackupDeleted)
async def delete_db_backup(backup_id: str, request: Request) -> dict[str, Any]:
    principal = _principal(request)
    try:
        entry = await run_in_threadpool(db_backup.delete_backup, backup_id)
    except db_backup.BackupError as error:
        raise _fail(error) from None
    _audit(request, principal, 200, "BACKUP_DELETED", {"id": backup_id, "size": entry["size"], "kind": entry["kind"],
                                                       "reason": entry["reason"]})
    return {"id": backup_id, "deleted": True}
