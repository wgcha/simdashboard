"""Drive write availability (stages D2–D3).

``SIMDASH_DRIVE_WRITES_ENABLED`` ("드라이브 쓰기 허용", default off) gates every
drive write of scx mode:

* off: scx mode is read-only as in D2; every write endpoint answers 409
  ``DRIVE_WRITE_DISABLED`` before it starts.
* on (D3): result drop uploads (folder creation, chunk upload, publish) and
  Final designation (preview, report upload, confirm, summary repair) write
  through the drive upload queue (:mod:`.upload_queue`, ``mkdirs``/``copy_within``
  /``upload_new`` only).  Features without a drive design keep answering
  ``DRIVE_WRITE_DISABLED`` in scx mode (:func:`require_local_writes`): the legacy
  result registration drafts (prepare/publish/mirror retry) and the storage panel
  uploads (integration 03 8-6: uploads go through result registration).

``DriveStorageProvider`` itself still refuses every write primitive; drive
writes happen only in the queue worker.
"""
from __future__ import annotations

from fastapi import HTTPException

from .config import drive_mode

MESSAGE = ("드라이브 모드에서 드라이브 쓰기가 꺼져 있습니다(읽기 전용). 업로드·폴더 만들기·Final 지정은 관리자가 "
           "'드라이브 쓰기 허용'(SIMDASH_DRIVE_WRITES_ENABLED)을 켠 뒤 사용할 수 있습니다.")
LOCAL_ONLY_MESSAGE = ("드라이브 모드에서는 이 화면에서 SPDM 폴더에 쓸 수 없습니다. 결과는 결과 등록(끌어놓기)으로 올리세요.")


def scx_mode() -> bool:
    return drive_mode() == "scx"


def drive_writes_active() -> bool:
    """scx mode with drive writes enabled and an SPDM root on the drive (the D3 write path is live)."""
    if not scx_mode():
        return False
    from . import gateway as drive_gateway

    try:
        settings = drive_gateway.current_settings()
    except Exception:  # noqa: BLE001 - a startup error surfaces elsewhere; no writes then
        return False
    return bool(settings.enabled and settings.writes_enabled and settings.spdm_root)


def writes_available() -> bool:
    """Whether the dashboard may write to the SPDM root: local mode yes; scx mode only with writes enabled."""
    return not scx_mode() or drive_writes_active()


def require_drive_writes() -> None:
    """FastAPI dependency for D3-capable write endpoints: 409 ``DRIVE_WRITE_DISABLED`` while scx writes are off."""
    if not writes_available():
        raise HTTPException(409, {"code": "DRIVE_WRITE_DISABLED", "message": MESSAGE})


def require_local_writes() -> None:
    """Endpoints without a drive write design: 409 ``DRIVE_WRITE_DISABLED`` in scx mode regardless of the setting."""
    if scx_mode():
        raise HTTPException(409, {"code": "DRIVE_WRITE_DISABLED", "message": LOCAL_ONLY_MESSAGE})


__all__ = ["LOCAL_ONLY_MESSAGE", "MESSAGE", "drive_writes_active", "require_drive_writes", "require_local_writes",
           "scx_mode", "writes_available"]
