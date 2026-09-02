from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.adapters.persistence import workbench as workbench_persistence
from app.adapters.persistence.workbench import SQLWorkbenchWorkItemReassignmentCommand
from app.application.workbench.commands import reassign_workbench_work_item
from app.domains.workbench.models import (
    ProjectAssigneeRead,
    WorkItemAssigneeAccountNotActiveError,
    WorkItemAssigneeMembershipRequiredError,
    WorkItemNotFoundError,
    WorkItemReassignmentCommand,
    WorkItemReassignmentFinalError,
    WorkItemReassignmentState,
)


def _state(status: str = "READY") -> WorkItemReassignmentState:
    return WorkItemReassignmentState(
        id="item-1",
        request_id="request-1",
        project_id="project-1",
        status=status,
        owner_user_id="owner-old",
    )


class _ReassignmentPort:
    def __init__(self, state: WorkItemReassignmentState | None, *, resolve_error: Exception | None = None) -> None:
        self.state = state
        self.resolve_error = resolve_error
        self.events: list[str] = []

    def reassignment_state(self, item_id: str) -> WorkItemReassignmentState | None:
        assert item_id == "item-1"
        self.events.append("read")
        return self.state

    def resolve_project_assignee(self, project_id: str, owner_user_id: str) -> ProjectAssigneeRead:
        assert (project_id, owner_user_id) == ("project-1", "owner-new")
        self.events.append("resolve")
        if self.resolve_error:
            raise self.resolve_error
        return ProjectAssigneeRead(user_id="owner-new", display_name="New owner")

    def update_work_item_assignee(self, item_id: str, assignee: ProjectAssigneeRead) -> None:
        assert (item_id, assignee.user_id) == ("item-1", "owner-new")
        self.events.append("update")

    def reassigned_work_item(self, item_id: str) -> dict[str, object]:
        assert item_id == "item-1"
        self.events.append("reread")
        return {"id": item_id, "owner": "New owner", "owner_user_id": "owner-new"}


def _run(port: _ReassignmentPort, *, audit_raises: bool = False) -> dict[str, object]:
    def audit(item: WorkItemReassignmentState, assignee: ProjectAssigneeRead) -> None:
        assert (item.owner_user_id, assignee.user_id) == ("owner-old", "owner-new")
        port.events.append("audit")
        if audit_raises:
            raise RuntimeError("audit failure")

    return reassign_workbench_work_item(
        port,
        "item-1",
        WorkItemReassignmentCommand(owner_user_id="owner-new"),
        authorize=lambda item: (assert_project(item, port)),
        audit=audit,
    )


def assert_project(item: WorkItemReassignmentState, port: _ReassignmentPort) -> None:
    assert item.project_id == "project-1"
    port.events.append("authorize")


def test_reassignment_command_preserves_read_authorize_target_update_audit_reread_order() -> None:
    port = _ReassignmentPort(_state())

    result = _run(port)

    assert result == {"id": "item-1", "owner": "New owner", "owner_user_id": "owner-new"}
    assert port.events == ["read", "authorize", "resolve", "update", "audit", "reread"]


def test_reassignment_missing_stops_before_authorization_or_resolution() -> None:
    port = _ReassignmentPort(None)

    with pytest.raises(WorkItemNotFoundError):
        _run(port)

    assert port.events == ["read"]


def test_reassignment_completed_guard_runs_after_authorization_and_before_target_resolution() -> None:
    port = _ReassignmentPort(_state("COMPLETED"))

    with pytest.raises(WorkItemReassignmentFinalError):
        _run(port)

    assert port.events == ["read", "authorize"]


def test_reassignment_target_resolution_failure_stops_before_update_or_audit() -> None:
    port = _ReassignmentPort(
        _state(),
        resolve_error=WorkItemAssigneeMembershipRequiredError(project_id="project-1", owner_user_id="owner-new"),
    )

    with pytest.raises(WorkItemAssigneeMembershipRequiredError):
        _run(port)

    assert port.events == ["read", "authorize", "resolve"]


def test_reassignment_audit_failure_happens_after_update_before_response_reread() -> None:
    port = _ReassignmentPort(_state())

    with pytest.raises(RuntimeError, match="audit failure"):
        _run(port, audit_raises=True)

    assert port.events == ["read", "authorize", "resolve", "update", "audit"]


@pytest.mark.parametrize(
    ("detail", "domain_error"),
    [
        (
            {"code": "ASSIGNEE_PROJECT_MEMBERSHIP_REQUIRED", "project_id": "project-1", "owner_user_id": "owner-new"},
            WorkItemAssigneeMembershipRequiredError,
        ),
        (
            {"code": "ASSIGNEE_ACCOUNT_NOT_ACTIVE", "project_id": "project-1", "owner_user_id": "owner-new"},
            WorkItemAssigneeAccountNotActiveError,
        ),
    ],
)
def test_sql_reassignment_adapter_translates_existing_assignee_http_contracts(
    monkeypatch: pytest.MonkeyPatch,
    detail: dict[str, str],
    domain_error: type[Exception],
) -> None:
    class _Repository:
        def __init__(self, _connection: object) -> None:
            pass

    error = HTTPException(422, detail=detail)
    monkeypatch.setattr(workbench_persistence, "WorkbenchRepository", _Repository)
    monkeypatch.setattr(workbench_persistence, "resolve_project_assignee", lambda *_args: (_ for _ in ()).throw(error))
    adapter = SQLWorkbenchWorkItemReassignmentCommand(object())  # type: ignore[arg-type]

    with pytest.raises(domain_error) as exc_info:
        adapter.resolve_project_assignee("project-1", "owner-new")

    assert exc_info.value.__cause__ is error


def test_sql_reassignment_adapter_uses_one_connection_for_state_owner_update_and_response(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[object, ...]] = []

    class _Connection:
        def execute(self, statement: str, values: list[object]) -> object:
            events.append(("sql", statement, values))
            return object()

    sql_connection = _Connection()

    class _Repository:
        def __init__(self, actual_connection: object) -> None:
            assert actual_connection is sql_connection

        def begin_transaction(self) -> None:
            events.append(("begin",))

        def commit_transaction(self) -> None:
            events.append(("commit",))

        def rollback_transaction(self) -> None:
            events.append(("rollback",))

    state_row = {
        "id": "item-1", "request_id": "request-1", "project_id": "project-1", "status": "READY", "owner_user_id": "owner-old"
    }
    response_row = {"id": "item-1", "owner": "New owner", "owner_user_id": "owner-new"}
    row_sets = [[state_row], [response_row]]
    monkeypatch.setattr(workbench_persistence, "WorkbenchRepository", _Repository)
    monkeypatch.setattr(workbench_persistence, "rows", lambda _cursor: row_sets.pop(0))
    monkeypatch.setattr(
        workbench_persistence,
        "resolve_project_assignee",
        lambda actual, project_id, owner_id: _assignee(events, actual, project_id, owner_id, sql_connection),
    )

    adapter = SQLWorkbenchWorkItemReassignmentCommand(sql_connection)  # type: ignore[arg-type]
    adapter.begin_transaction()
    assert adapter.reassignment_state("item-1") == _state()
    assert adapter.resolve_project_assignee("project-1", "owner-new") == ProjectAssigneeRead("owner-new", "New owner")
    adapter.update_work_item_assignee("item-1", ProjectAssigneeRead("owner-new", "New owner"))
    assert adapter.reassigned_work_item("item-1") == response_row
    adapter.commit_transaction()
    adapter.rollback_transaction()

    assert events[0] == ("begin",)
    assert events[2] == ("resolve", sql_connection, "project-1", "owner-new")
    assert events[-2:] == [("commit",), ("rollback",)]
    assert any("UPDATE request_work_items SET owner" in event[1] for event in events if event[0] == "sql")


def _assignee(
    events: list[tuple[object, ...]],
    connection: object,
    project_id: str,
    owner_user_id: str,
    expected_connection: object,
) -> object:
    assert connection is expected_connection
    events.append(("resolve", connection, project_id, owner_user_id))
    return type("Assignee", (), {"user_id": "owner-new", "display_name": "New owner"})()
