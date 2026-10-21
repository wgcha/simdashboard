from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ....schemas.api import ReleasesResponse
from ....services import releases


router = APIRouter()


@router.get("/api/releases", response_model=ReleasesResponse)
def get_releases() -> dict[str, Any]:
    """버전 게시판: 실행 중 서버 버전·빌드 정보와 ``RELEASES.md`` 목록(읽기 전용, 로그인한 모든 계정)."""
    return releases.releases_response()
