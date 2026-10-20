from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ....config import ESL_BASE_PATH, esl_link_enabled
from ....modules.access_control import PROJECT_DATA_VIEW, require_permission
from ....schemas.api import EslLinkResponse


router = APIRouter()


@router.get("/api/integrations/esl", response_model=EslLinkResponse)
def get_esl_link(request: Request) -> dict[str, Any]:
    """ESL(등가정적하중) 링크 표시 상태(``SIMDASH_ESL_ENABLED``, 기본 off)."""
    require_permission(request, PROJECT_DATA_VIEW)
    return {"enabled": esl_link_enabled(), "base_path": ESL_BASE_PATH}
