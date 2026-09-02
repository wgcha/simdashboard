from __future__ import annotations

from typing import Any, NoReturn

from fastapi import APIRouter, HTTPException, Query, Request

from ....adapters.filesystem.drop_videos import example_drop_videos
from ....adapters.persistence.drop_videos import SQLDropVideoRepositoryProvider
from ....application.drop_videos.queries import list_drop_videos
from ....domains.drop_videos.errors import DropVideoError
from ....modules.access_control import PROJECT_DATA_VIEW, require_resource_permission
from ....schemas.api import DropVideoPageResponse
from .... import config as app_config
from ....database import json_value
from ....services.drop_video_demo import (
    DEMO_DROP_VIDEO_LOAD_CASE_IDS,
    DROP_VIDEO_DEMO_BY_ID,
    build_demo_evaluation,
    summarize_demo_evaluations,
)

router = APIRouter()


def _raise_mapped(error: DropVideoError) -> NoReturn:
    raise HTTPException(404, str(error)) from error


@router.get("/api/load-cases/{load_case_id}/drop-videos", response_model=DropVideoPageResponse)
def get_drop_videos(
    load_case_id: str,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=20),
) -> dict[str, Any]:
    try:
        return list_drop_videos(
            load_case_id, page, page_size,
            lambda connection: require_resource_permission(
                request, PROJECT_DATA_VIEW, "load_case", load_case_id, conn=connection
            ),
            SQLDropVideoRepositoryProvider(),
            example_source=lambda case_id: example_drop_videos(
                case_id, DEMO_DROP_VIDEO_LOAD_CASE_IDS
            ),
            storage_mode=app_config.media_storage_mode,
            demo_by_id=DROP_VIDEO_DEMO_BY_ID, json_value=json_value,
            build_evaluation=build_demo_evaluation, summarize=summarize_demo_evaluations,
        )
    except DropVideoError as error:
        _raise_mapped(error)
