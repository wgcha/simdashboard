from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ....config import PHYSICSAI_BASE_PATH, physicsai_link_enabled
from ....modules.access_control import PROJECT_DATA_VIEW, require_permission
from ....schemas.api import PhysicsAiLinkResponse


router = APIRouter()


@router.get("/api/integrations/physicsai", response_model=PhysicsAiLinkResponse)
def get_physicsai_link(request: Request) -> dict[str, Any]:
    """PhysicsAI "AI 예측" 링크 표시 상태(``SIMDASH_PHYSICSAI_ENABLED``, 기본 off)."""
    require_permission(request, PROJECT_DATA_VIEW)
    return {"enabled": physicsai_link_enabled(), "base_path": PHYSICSAI_BASE_PATH}
