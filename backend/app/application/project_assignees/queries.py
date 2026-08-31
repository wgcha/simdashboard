from __future__ import annotations

from ...domains.project_assignees.models import ProjectAssigneeCandidate, ProjectNotFoundError
from ...domains.project_assignees.ports import ProjectAssigneeReaderProvider


def list_assignee_candidates(
    project_id: str,
    q: str | None,
    exclude_local_admin: bool,
    reader_provider: ProjectAssigneeReaderProvider,
) -> list[ProjectAssigneeCandidate]:
    """List eligible members after project validation and request-edit authorization."""

    search_term = q.strip().lower() if q and q.strip() else None
    with reader_provider() as reader:
        if not reader.project_exists(project_id):
            raise ProjectNotFoundError(project_id)
        reader.authorize_request_edit(project_id)
        return reader.list_candidates(project_id, search_term, exclude_local_admin)
