from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol

from .models import RequestAssignee, RequestReassignmentAudit, StoredAnalysisRequest


class RequestQueryRepository(Protocol):
    """Read port for project-scoped analysis requests."""

    def authorize_project(self, project_id: str) -> bool: ...
    def list_requests(self, project_id: str) -> list[StoredAnalysisRequest]: ...


class RequestQueryRepositoryProvider(Protocol):
    def __call__(self) -> AbstractContextManager[RequestQueryRepository]: ...


class RequestReassignmentUnitOfWork(Protocol):
    """Atomic request ownership change plus its audit record."""

    def find_request(self, request_id: str) -> StoredAnalysisRequest | None: ...
    def authorize_request_edit(self, project_id: str) -> object: ...
    def resolve_assignee(self, project_id: str, owner_user_id: str) -> RequestAssignee: ...
    def update_assignee(self, request_id: str, assignee: RequestAssignee) -> None: ...
    def add_reassignment_audit(
        self,
        audit: RequestReassignmentAudit,
        request: StoredAnalysisRequest,
        assignee: RequestAssignee,
    ) -> None: ...
