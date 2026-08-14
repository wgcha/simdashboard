from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.adapters.persistence import requests as request_persistence
from app.application.requests.commands import reassign_request
from app.database import initialize_database
from app.database_connection import connect
from app.domains.requests.models import RequestNotFoundError, RequestReassignmentAudit, StoredAnalysisRequest
from app.domains.requests.ports import RequestReassignmentUnitOfWork
from app.main import app


AUDIT: RequestReassignmentAudit = {
    "user_id": "user-a",
    "username": "alice",
    "role": "admin",
    "method": "PATCH",
    "path": "/api/requests/request-a/assignee",
    "status_code": 200,
    "request_id": "request-audit",
    "client_ip": "127.0.0.1",
    "user_agent": "pytest",
}


class FakeRequestUnitOfWork:
    def __init__(self, events: list[str], current: StoredAnalysisRequest | None) -> None:
        self.events = events
        self.current = current

    def find_request(self, request_id: str) -> StoredAnalysisRequest | None:
        self.events.append(f"find:{request_id}")
        return self.current

    def authorize_request_edit(self, project_id: str) -> object:
        self.events.append(f"authorize:{project_id}")
        return object()

    def resolve_assignee(self, project_id: str, owner_user_id: str) -> dict[str, str]:
        self.events.append(f"resolve:{project_id}:{owner_user_id}")
        return {"user_id": owner_user_id, "display_name": "New owner"}

    def update_assignee(self, request_id: str, assignee: dict[str, str]) -> None:
        self.events.append(f"update:{request_id}:{assignee['user_id']}")
        assert self.current is not None
        self.current = {**self.current, "owner": assignee["display_name"], "owner_user_id": assignee["user_id"]}

    def add_reassignment_audit(
        self,
        audit: RequestReassignmentAudit,
        request: StoredAnalysisRequest,
        assignee: dict[str, str],
    ) -> None:
        self.events.append(f"audit:{audit['request_id']}:{request['owner_user_id']}:{assignee['user_id']}")


@pytest.mark.unit
def test_reassign_request_reads_then_authorizes_before_resolution_or_mutation() -> None:
    events: list[str] = []
    unit_of_work = FakeRequestUnitOfWork(
        events,
        {"id": "request-a", "project_id": "project-a", "owner": "Old", "owner_user_id": "user-old"},
    )

    @contextmanager
    def provider() -> Iterator[RequestReassignmentUnitOfWork]:
        events.append("open")
        try:
            yield unit_of_work
        finally:
            events.append("close")

    result = reassign_request("request-a", "user-new", AUDIT, provider)

    assert result["owner_user_id"] == "user-new"
    assert events == [
        "open",
        "find:request-a",
        "authorize:project-a",
        "resolve:project-a:user-new",
        "update:request-a:user-new",
        "audit:request-audit:user-old:user-new",
        "find:request-a",
        "close",
    ]


@pytest.mark.unit
def test_reassign_request_not_found_does_not_authorize_or_mutate() -> None:
    events: list[str] = []
    unit_of_work = FakeRequestUnitOfWork(events, None)

    @contextmanager
    def provider() -> Iterator[RequestReassignmentUnitOfWork]:
        events.append("open")
        try:
            yield unit_of_work
        finally:
            events.append("close")

    with pytest.raises(RequestNotFoundError) as error:
        reassign_request("missing", "user-new", AUDIT, provider)

    assert error.value.request_id == "missing"
    assert events == ["open", "find:missing", "close"]


@pytest.mark.contract
def test_reassign_request_rolls_back_update_and_audit_when_post_audit_write_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initialize_database()
    request_id = "request-drop-001"
    with connect() as connection:
        owner_before = connection.execute(
            "SELECT owner, owner_user_id FROM analysis_requests WHERE id=?", [request_id]
        ).fetchone()
        audit_count_before = connection.execute(
            "SELECT count(*) FROM audit_events WHERE action='REQUEST_ASSIGNEE_CHANGED' AND "
            "json_extract_string(detail_json, '$.request_id')=?",
            [request_id],
        ).fetchone()[0]
    assert owner_before is not None

    original_add_audit = request_persistence.SQLRequestReassignmentUnitOfWork.add_reassignment_audit

    def fail_after_audit(
        unit_of_work: request_persistence.SQLRequestReassignmentUnitOfWork,
        audit: RequestReassignmentAudit,
        current: StoredAnalysisRequest,
        assignee: dict[str, str],
    ) -> None:
        original_add_audit(unit_of_work, audit, current, assignee)
        raise RuntimeError("inject failure after audit")

    monkeypatch.setattr(request_persistence.SQLRequestReassignmentUnitOfWork, "add_reassignment_audit", fail_after_audit)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.patch(f"/api/requests/{request_id}/assignee", json={"owner_user_id": "local-admin"})
    assert response.status_code == 500

    with connect() as connection:
        assert connection.execute(
            "SELECT owner, owner_user_id FROM analysis_requests WHERE id=?", [request_id]
        ).fetchone() == owner_before
        assert connection.execute(
            "SELECT count(*) FROM audit_events WHERE action='REQUEST_ASSIGNEE_CHANGED' AND "
            "json_extract_string(detail_json, '$.request_id')=?",
            [request_id],
        ).fetchone()[0] == audit_count_before
