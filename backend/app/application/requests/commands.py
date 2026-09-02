from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager

from ...domains.requests.models import (
    RequestNotFoundError,
    RequestReassignmentAudit,
    StoredAnalysisRequest,
)
from ...domains.requests.ports import RequestReassignmentUnitOfWork


RequestReassignmentUnitOfWorkProvider = Callable[[], AbstractContextManager[RequestReassignmentUnitOfWork]]


def reassign_request(
    request_id: str,
    owner_user_id: str,
    audit: RequestReassignmentAudit,
    unit_of_work_provider: RequestReassignmentUnitOfWorkProvider,
) -> StoredAnalysisRequest:
    """Reassign an analysis request inside one persistence-owned transaction.

    The current request must be loaded to obtain its project scope.  The
    authorization port is therefore called immediately after that lookup and
    before assignee resolution or any mutation.
    """
    with unit_of_work_provider() as unit_of_work:
        current = unit_of_work.find_request(request_id)
        if current is None:
            raise RequestNotFoundError(request_id)
        unit_of_work.authorize_request_edit(current["project_id"])
        assignee = unit_of_work.resolve_assignee(current["project_id"], owner_user_id)
        unit_of_work.update_assignee(request_id, assignee)
        unit_of_work.add_reassignment_audit(audit, current, assignee)
        updated = unit_of_work.find_request(request_id)
        if updated is None:  # Defensive only: the update is in this same UoW.
            raise RequestNotFoundError(request_id)
        return updated
