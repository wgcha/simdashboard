from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ....adapters.persistence.request_load_cases import (
    SQLRequestLoadCasesRepositoryProvider,
    SQLRequestLoadCaseWriteRepositoryProvider,
)
from ....application.request_load_cases.commands import create_load_case as create_load_case_command
from ....application.request_load_cases.queries import get_load_cases as get_load_cases_query
from ....domains.request_load_cases.errors import RequestNotFoundError
from ....modules.access_control import REQUEST_EDIT, require_resource_permission
from ....schemas.api import LoadCaseCreate


router = APIRouter()
create_router = APIRouter()


@router.get("/api/requests/{request_id}/load-cases")
def get_load_cases(request_id: str) -> list[dict[str, Any]]:
    return get_load_cases_query(request_id, SQLRequestLoadCasesRepositoryProvider())


@create_router.post("/api/requests/{request_id}/load-cases", status_code=201)
def create_load_case(request_id: str, payload: LoadCaseCreate, request: Request) -> dict[str, Any]:
    try:
        return create_load_case_command(
            request_id=request_id,
            name=payload.name,
            analysis_type=payload.analysis_type,
            parameters=payload.parameters,
            authorize=lambda resource_id, connection: require_resource_permission(
                request,
                REQUEST_EDIT,
                "request",
                resource_id,
                conn=connection,
            ),
            repository_provider=SQLRequestLoadCaseWriteRepositoryProvider(),
        )
    except RequestNotFoundError as error:
        raise HTTPException(404, str(error)) from error
