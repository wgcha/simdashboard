from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ....modules.access_control import PROJECT_DATA_VIEW, require_permission
from ....schemas.api import PhysicsAiLinkResponse
from ....services.physicsai_link import physicsai_link_state


router = APIRouter()


@router.get("/api/integrations/physicsai", response_model=PhysicsAiLinkResponse)
def get_physicsai_link(request: Request) -> dict[str, Any]:
    require_permission(request, PROJECT_DATA_VIEW)
    return physicsai_link_state()
