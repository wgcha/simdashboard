from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from ....adapters.persistence.variable_catalog import SQLVariableCatalogRepositoryProvider
from ....application.variable_catalog.commands import (
    create_variable as create_variable_command,
    delete_variable as delete_variable_command,
    update_variable as update_variable_command,
)
from ....application.variable_catalog.queries import list_variables
from ....database_connection import ConnectionLike
from ....domains.variable_catalog.errors import DashboardVariableReferenceError
from ....modules.access_control import PROJECT_VARIABLE_MANAGE, require_resource_permission
from ....schemas.api import VariableCreate, VariableUpdate


router = APIRouter()


def _catalog_error(exc: Exception) -> HTTPException:
    messages = {
        "VARIABLE_EXISTS": (409, "같은 변수 키가 이미 존재합니다."),
        "VARIABLE_NOT_FOUND": (404, "변수를 찾을 수 없습니다."),
        "LOAD_CASE_NOT_FOUND": (404, "하중 경우를 찾을 수 없습니다."),
        "INVALID_CATALOG_OPTIONS": (422, "데이터 유형에 허용되지 않은 위젯 또는 집계 방식입니다."),
        "NUMBER_THRESHOLD_REQUIRED": (422, "숫자 변수에는 판정 기준값이 필요합니다."),
    }
    status, message = messages.get(str(exc), (422, str(exc)))
    raise HTTPException(status, message) from exc


def _authorize(request: Request):
    def authorize(load_case_id: str, active_connection: ConnectionLike) -> None:
        require_resource_permission(
            request,
            PROJECT_VARIABLE_MANAGE,
            "load_case",
            load_case_id,
            conn=active_connection,
        )

    return authorize


@router.get("/api/load-cases/{load_case_id}/variables")
def get_variables(load_case_id: str) -> list[dict[str, Any]]:
    try:
        return list_variables(load_case_id, SQLVariableCatalogRepositoryProvider())
    except LookupError as exc:
        _catalog_error(exc)


@router.post("/api/load-cases/{load_case_id}/variables", status_code=201)
def create_variable(load_case_id: str, payload: VariableCreate, request: Request) -> dict[str, Any]:
    try:
        return create_variable_command(
            load_case_id,
            payload.model_dump(),
            SQLVariableCatalogRepositoryProvider(authorize=_authorize(request)),
        )
    except (ValueError, LookupError) as exc:
        _catalog_error(exc)


@router.put("/api/load-cases/{load_case_id}/variables/{variable_key}")
def update_variable(load_case_id: str, variable_key: str, payload: VariableUpdate, request: Request) -> dict[str, Any]:
    try:
        return update_variable_command(
            load_case_id,
            variable_key,
            payload.model_dump(),
            SQLVariableCatalogRepositoryProvider(authorize=_authorize(request)),
        )
    except (ValueError, LookupError) as exc:
        _catalog_error(exc)


@router.delete("/api/load-cases/{load_case_id}/variables/{variable_key}")
def delete_variable(
    load_case_id: str,
    variable_key: str,
    request: Request,
    updated_by: str = Query(default="관리자", min_length=2, max_length=60),
) -> dict[str, Any]:
    try:
        return delete_variable_command(
            load_case_id,
            variable_key,
            updated_by,
            SQLVariableCatalogRepositoryProvider(authorize=_authorize(request)),
        )
    except DashboardVariableReferenceError as error:
        raise HTTPException(
            409,
            detail={"message": "대시보드에서 사용 중인 변수는 삭제할 수 없습니다.", "dashboard_ids": error.dashboard_ids},
        ) from error
    except LookupError as exc:
        _catalog_error(exc)
