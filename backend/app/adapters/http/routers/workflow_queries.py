from __future__ import annotations

from typing import Any, NoReturn

from fastapi import APIRouter, HTTPException

from ....adapters.persistence.workflow_queries import SQLWorkflowQueriesRepositoryProvider
from ....application.workflow_queries.queries import (
    get_workflow as get_workflow_query,
    get_workflows as get_workflows_query,
)
from ....domains.workflow_queries.errors import AnalysisRequestNotFoundError, WorkflowQueryError


router = APIRouter()


def _raise_mapped(error: WorkflowQueryError) -> NoReturn:
    if isinstance(error, AnalysisRequestNotFoundError):
        raise HTTPException(404, error.message) from error
    raise error


@router.get("/api/requests/{request_id}/workflow")
def get_workflow(request_id: str) -> dict[str, Any]:
    try:
        return get_workflow_query(request_id, SQLWorkflowQueriesRepositoryProvider())
    except WorkflowQueryError as error:
        _raise_mapped(error)


@router.get("/api/workflows")
def get_workflows() -> list[dict[str, Any]]:
    return get_workflows_query(SQLWorkflowQueriesRepositoryProvider())
