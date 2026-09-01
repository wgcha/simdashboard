from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ....adapters.persistence.request_load_cases import SQLRequestLoadCasesRepositoryProvider
from ....application.request_load_cases.queries import get_load_cases as get_load_cases_query


router = APIRouter()


@router.get("/api/requests/{request_id}/load-cases")
def get_load_cases(request_id: str) -> list[dict[str, Any]]:
    return get_load_cases_query(request_id, SQLRequestLoadCasesRepositoryProvider())
