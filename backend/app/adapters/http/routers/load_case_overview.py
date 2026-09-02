from __future__ import annotations

from typing import Any, NoReturn

from fastapi import APIRouter, HTTPException, Query, Request

from ....adapters.persistence.load_case_overview import SQLLoadCaseOverviewRepositoryProvider
from ....adapters.persistence.products import SQLProductInformationRepositoryProvider
from ....application.load_case_overview.queries import get_load_case_overview as get_load_case_overview_query
from ....domains.load_case_overview.errors import LoadCaseOverviewError
from ....modules.access_control import PROJECT_DATA_VIEW, require_resource_permission


router = APIRouter()


def _raise_mapped(error: LoadCaseOverviewError) -> NoReturn:
    raise HTTPException(404, error.message) from error


def _with_media_urls(payload: dict[str, Any]) -> dict[str, Any]:
    for item in payload["media"]:
        item["asset_url"] = f"/api/assets/{item['id']}"
        item["download_url"] = f"/api/assets/{item['id']}/download"
    return payload


@router.get("/api/load-cases/{load_case_id}/overview")
def get_load_case_overview(
    load_case_id: str,
    request: Request,
    run_id: str | None = Query(default=None, min_length=3, max_length=120),
) -> dict[str, Any]:
    def authorize_product_information() -> object:
        try:
            return require_resource_permission(
                request,
                PROJECT_DATA_VIEW,
                "load_case",
                load_case_id,
            )
        except HTTPException as error:
            if error.status_code == 404:
                raise HTTPException(404, "하중 경우를 찾을 수 없습니다.") from error
            raise

    try:
        payload = get_load_case_overview_query(
            load_case_id,
            run_id,
            authorize_product_information,
            SQLProductInformationRepositoryProvider(),
            SQLLoadCaseOverviewRepositoryProvider(),
        )
        return _with_media_urls(payload)
    except LoadCaseOverviewError as error:
        _raise_mapped(error)
