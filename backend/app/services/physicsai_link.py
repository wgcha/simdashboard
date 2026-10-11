"""PhysicsAI 링크 상태(docs/physicsai-integration.md).

대시보드는 PhysicsAI에 SPDM 경로를 링크로 넘기기만 한다. 파일을 옮기거나 쓰지 않는다.
"""

from __future__ import annotations

from typing import Any

from ..config import PHYSICSAI_BASE_PATH, physicsai_link_enabled
from ..database_connection import connect
from . import spdm_storage
from .storage.provider import SpdmStorageError


def physicsai_link_state() -> dict[str, Any]:
    if not physicsai_link_enabled():
        return {"enabled": False, "base_path": PHYSICSAI_BASE_PATH, "spdm_root": None}
    try:
        with connect() as conn:
            root = spdm_storage.storage_root(conn).root
    except (SpdmStorageError, OSError, ValueError):
        root = None
    return {"enabled": True, "base_path": PHYSICSAI_BASE_PATH, "spdm_root": str(root) if root is not None else None}
