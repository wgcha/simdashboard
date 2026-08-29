from __future__ import annotations

from datetime import datetime

import pytest

from app.adapters.persistence import workbench as workbench_persistence
from app.adapters.persistence.workbench import SQLWorkbenchWorkItemCompleteCommand
from app.application.workbench.commands import complete_workbench_work_item
from app.domains.workbench.models import (
    DemoRunInvalidError,
    WorkItemCompleteCommand,
    WorkItemLifecycleState,
    WorkItemNotCurrentError,
    WorkItemNotFoundError,
    WorkItemNotStartedError,
    WorkItemPrerequisiteIncompleteError,
    WorkPlanMonitoringSummaryRead,
)


def _summary() -> WorkPlanMonitoringSummaryRead:
    return {
        "status": "IN_PROGRESS",
        "progress": 42,
        "current_step": "해석",
        "current_step_id": "item-1",
        "completed_count": 1,
        "total_count": 3,
        "work_plan": {"id": "plan-1"},
        "steps": [],
        "latest_demo_run": None,
        "request_type_assignment": None,
    }


def _command(demo_run_id: str | None = None) -> WorkItemCompleteCommand:
    return WorkItemCompleteCommand(completed_by="principal actor", demo_run_id=demo_run_id)


def _running() -> WorkItemLifecycleState:
    return WorkItemLifecycleState("item-1", "request-1", "IN_PROGRESS", 40, 2)


class _CompletePort:
    def __init__(
        self,
        item: WorkItemLifecycleState | None,
        *,
        current_item_id: str | None = "item-1",
        incomplete_prior_count: int = 0,
        demo_valid: bool = True,
        next_item_id: str | None = "item-2",
    ) -> None:
        self.item = item
        self.current_item_id_value = current_item_id
        self.incomplete_prior_count_value = incomplete_prior_count
        self.demo_valid = demo_valid
        self.next_item_id = next_item_id
        self.events: list[str] = []

    def work_item(self, item_id: str) -> WorkItemLifecycleState | None:
        assert item_id == "item-1"
        self.events.append("read")
        return self.item

    def request_monitoring_summary(self, request_id: str) -> WorkPlanMonitoringSummaryRead:
        assert request_id == "request-1"
        self.events.append("summary")
        return _summary()

    def current_work_item_id(self, request_id: str) -> str | None:
        assert request_id == "request-1"
        self.events.append("current")
        return self.current_item_id_value

    def incomplete_prior_count(self, request_id: str, sequence_no: int) -> int:
        assert (request_id, sequence_no) == ("request-1", 2)
        self.events.append("prior")
        return self.incomplete_prior_count_value

    def demo_run_is_succeeded_for_request(self, demo_run_id: str, request_id: str) -> bool:
        assert (demo_run_id, request_id) == ("run-1", "request-1")
        self.events.append("demo")
        return self.demo_valid

    def complete_work_item(self, item_id: str, command: WorkItemCompleteCommand) -> None:
        assert item_id == "item-1"
        assert command.completed_by == "principal actor"
        self.events.append("complete")

    def next_waiting_work_item_id(self, request_id: str, sequence_no: int) -> str | None:
        assert (request_id, sequence_no) == ("request-1", 2)
        self.events.append("next")
        return self.next_item_id

    def mark_work_item_ready(self, item_id: str) -> None:
        assert item_id == "item-2"
        self.events.append("ready")

    def sync_request_status(self, request_id: str) -> WorkPlanMonitoringSummaryRead:
        assert request_id == "request-1"
        self.events.append("sync")
        return _summary()


def _run(port: _CompletePort, command: WorkItemCompleteCommand = _command("run-1")):
    return complete_workbench_work_item(
        port,
        "item-1",
        command,
        authorize=lambda: port.events.append("authorize"),
        audit=lambda: port.events.append("audit"),
    )


def test_complete_command_preserves_read_authorize_audit_validation_completion_next_ready_sync_order() -> None:
    port = _CompletePort(_running())

    result = _run(port)

    assert result.changed is True
    assert result.summary == _summary()
    assert port.events == ["read", "authorize", "audit", "current", "prior", "demo", "complete", "next", "ready", "sync"]


def test_complete_command_is_idempotent_after_authorization_and_audit() -> None:
    port = _CompletePort(WorkItemLifecycleState("item-1", "request-1", "COMPLETED", 100, 2))

    result = _run(port)

    assert result.changed is False
    assert port.events == ["read", "authorize", "audit", "summary"]


def test_complete_command_stops_before_authorization_when_missing() -> None:
    port = _CompletePort(None)

    with pytest.raises(WorkItemNotFoundError):
        _run(port)

    assert port.events == ["read"]


@pytest.mark.parametrize(
    ("port", "error", "events"),
    [
        (
            _CompletePort(WorkItemLifecycleState("item-1", "request-1", "READY", 0, 2)),
            WorkItemNotStartedError,
            ["read", "authorize", "audit", "current"],
        ),
        (
            _CompletePort(_running(), current_item_id="other-item"),
            WorkItemNotCurrentError,
            ["read", "authorize", "audit", "current"],
        ),
        (
            _CompletePort(_running(), incomplete_prior_count=1),
            WorkItemPrerequisiteIncompleteError,
            ["read", "authorize", "audit", "current", "prior"],
        ),
        (
            _CompletePort(_running(), demo_valid=False),
            DemoRunInvalidError,
            ["read", "authorize", "audit", "current", "prior", "demo"],
        ),
    ],
)
def test_complete_command_stops_at_each_existing_guard(
    port: _CompletePort,
    error: type[Exception],
    events: list[str],
) -> None:
    with pytest.raises(error):
        _run(port)

    assert port.events == events


def test_complete_command_skips_demo_lookup_and_ready_update_when_not_applicable() -> None:
    port = _CompletePort(_running(), next_item_id=None)

    _run(port, _command())

    assert port.events == ["read", "authorize", "audit", "current", "prior", "complete", "next", "sync"]


def test_sql_complete_adapter_uses_one_connection_for_uow_completion_next_ready_and_monitoring(monkeypatch) -> None:
    calls: list[tuple[object, ...]] = []

    class _Result:
        def __init__(self, row: tuple[object, ...] | None) -> None:
            self.row = row

        def fetchone(self) -> tuple[object, ...] | None:
            return self.row

    class _Connection:
        def execute(self, statement: str, values: list[object]) -> _Result:
            calls.append(("sql", statement, values))
            if "sequence_no >" in statement:
                return _Result(("item-2",))
            if "SELECT id FROM request_work_items" in statement:
                return _Result(("item-1",))
            if "SELECT count(*)" in statement:
                return _Result((0,))
            if "SELECT request_id, status" in statement:
                return _Result(("request-1", "SUCCEEDED"))
            return _Result(None)

    connection = _Connection()

    class _Repository:
        def __init__(self, actual_connection: object) -> None:
            assert actual_connection is connection

        def begin_transaction(self) -> None:
            calls.append(("begin",))

        def commit_transaction(self) -> None:
            calls.append(("commit",))

        def rollback_transaction(self) -> None:
            calls.append(("rollback",))

        def work_item(self, item_id: str) -> dict[str, object]:
            calls.append(("read", item_id))
            return {"id": item_id, "request_id": "request-1", "status": "IN_PROGRESS", "progress": 40, "sequence_no": 2}

    monkeypatch.setattr(workbench_persistence, "WorkbenchRepository", _Repository)
    monkeypatch.setattr(workbench_persistence, "_utcnow_naive", lambda: datetime(2026, 8, 30, 10, 0, 0))
    monkeypatch.setattr(workbench_persistence, "request_monitoring_summary", lambda actual, request_id: _record_summary(calls, "summary", actual, request_id))
    monkeypatch.setattr(workbench_persistence, "sync_request_status", lambda actual, request_id: _record_summary(calls, "sync", actual, request_id))

    adapter = SQLWorkbenchWorkItemCompleteCommand(connection)  # type: ignore[arg-type]
    adapter.begin_transaction()
    assert adapter.work_item("item-1") == _running()
    assert adapter.current_work_item_id("request-1") == "item-1"
    assert adapter.incomplete_prior_count("request-1", 2) == 0
    assert adapter.demo_run_is_succeeded_for_request("run-1", "request-1") is True
    adapter.complete_work_item("item-1", _command("run-1"))
    assert adapter.next_waiting_work_item_id("request-1", 2) == "item-2"
    adapter.mark_work_item_ready("item-2")
    assert adapter.request_monitoring_summary("request-1") == _summary()
    assert adapter.sync_request_status("request-1") == _summary()
    adapter.commit_transaction()
    adapter.rollback_transaction()

    assert calls[0:2] == [("begin",), ("read", "item-1")]
    assert calls[-4:] == [("summary", connection, "request-1"), ("sync", connection, "request-1"), ("commit",), ("rollback",)]
    assert any("status = 'COMPLETED'" in call[1] for call in calls if call[0] == "sql")
    assert any("SET status = 'READY'" in call[1] for call in calls if call[0] == "sql")


def _record_summary(
    calls: list[tuple[object, ...]],
    event: str,
    connection: object,
    request_id: str,
) -> WorkPlanMonitoringSummaryRead:
    calls.append((event, connection, request_id))
    return _summary()
