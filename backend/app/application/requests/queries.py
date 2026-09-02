from __future__ import annotations

from ...domains.requests.errors import ProjectMembershipRequiredError
from ...domains.requests.models import StoredAnalysisRequest
from ...domains.requests.ports import RequestQueryRepositoryProvider


def list_requests(
    project_id: str,
    repository_provider: RequestQueryRepositoryProvider,
) -> list[StoredAnalysisRequest]:
    """List requests through the project-scoped read port."""

    with repository_provider() as repository:
        if not repository.authorize_project(project_id):
            raise ProjectMembershipRequiredError(project_id)
        return repository.list_requests(project_id)
