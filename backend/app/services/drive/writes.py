"""Drive write availability (stage D2).

Stage D2 has no drive write path: in scx mode every feature that would write
to the SPDM root (folder creation, result drop upload, result mirror, Final
designation, legacy storage panel upload) answers 409 ``DRIVE_WRITE_DISABLED``
before it starts, and ``DriveStorageProvider`` refuses every write primitive.
``SIMDASH_DRIVE_WRITES_ENABLED`` ("드라이브 쓰기 허용", default off) is
reported in the status APIs; stage D3 gates its upload queue on it.
"""
from __future__ import annotations

from fastapi import HTTPException

from .config import drive_mode

WRITES_AVAILABLE_STAGE = "D3"
MESSAGE = ("드라이브 모드에서는 아직 SPDM 폴더에 쓸 수 없습니다(읽기 전용). 업로드·폴더 만들기·Final 지정은 "
           "드라이브 쓰기 단계(D3)와 관리자 '드라이브 쓰기 허용' 설정 이후 사용할 수 있습니다.")


def scx_mode() -> bool:
    return drive_mode() == "scx"


def writes_available() -> bool:
    """Whether the dashboard may write to the SPDM root: local mode yes; scx mode never in D2."""
    return not scx_mode()


def require_drive_writes() -> None:
    """FastAPI dependency for write endpoints: 409 ``DRIVE_WRITE_DISABLED`` in scx mode."""
    if not writes_available():
        raise HTTPException(409, {"code": "DRIVE_WRITE_DISABLED", "message": MESSAGE})


__all__ = ["MESSAGE", "require_drive_writes", "scx_mode", "writes_available"]
