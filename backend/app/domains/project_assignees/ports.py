from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Protocol

from .models import ProjectAssigneeCandidate


class ProjectAssigneeReader(Protocol):
    """Read-side contract for request assignee candidate lookup."""

    def project_exists(self, project_id: str) -> bool: ...

    def authorize_request_edit(self, project_id: str) -> None: ...

    def list_candidates(
        self,
        project_id: str,
        search_term: str | None,
        exclude_local_admin: bool,
    ) -> list[ProjectAssigneeCandidate]: ...


ProjectAssigneeReaderProvider = Callable[[], AbstractContextManager[ProjectAssigneeReader]]
