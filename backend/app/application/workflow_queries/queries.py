from __future__ import annotations

from typing import Any

from ...domains.workflow_queries.errors import AnalysisRequestNotFoundError
from ...domains.workflow_queries.policies import workflow_payload
from ...domains.workflow_queries.ports import WorkflowQueriesRepositoryProvider


def get_workflow(
    request_id: str,
    repository_provider: WorkflowQueriesRepositoryProvider,
) -> dict[str, Any]:
    with repository_provider() as repository:
        request = repository.analysis_request(request_id)
        if request is None:
            raise AnalysisRequestNotFoundError()
        monitoring = repository.monitoring_summary(request_id)
    return workflow_payload(request, monitoring)


def get_workflows(repository_provider: WorkflowQueriesRepositoryProvider) -> list[dict[str, Any]]:
    with repository_provider() as repository:
        requests = repository.workflow_requests()
        result = []
        for request in requests:
            result.append(workflow_payload(request, repository.monitoring_summary(request["id"])))
    return result
