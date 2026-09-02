from __future__ import annotations

from datetime import datetime

import pytest

from app.adapters.persistence import workbench as workbench_persistence
from app.adapters.persistence.workbench import SQLWorkbenchWorkItemProgressCommand
from app.application.workbench.commands import update_workbench_work_item_progress
from app.domains.workbench.models import (
    WorkItemNotFoundError,
    WorkItemNotInProgressError,
    WorkItemProgressCommand,
    WorkItemProgressNotMonotonicError,
    WorkItemLifecycleState,
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


def _command(progress: int = 60) -> WorkItemProgressCommand:
    return WorkItemProgressCommand(
        progress=progress,
        updated_by="principal actor",
    )


class _ProgressPort:
    def __init__(self, item: WorkItemLifecycleState | None) -> None:
        self.item = item
        self.events: list[str] = []

    def work_item(self, item_id: str) -> WorkItemLifecycleState | None:
        assert item_id == "item-1"
        self.events.append("read")
        return self.item

    def request_monitoring_summary(self, request_id: str) -> WorkPlanMonitoringSummaryRead:
        assert request_id == "request-1"
        self.events.append("summary")
        return _summary()

    def update_work_item_progress(self, item_id: str, command: WorkItemProgressCommand) -> None:
        assert item_id == "item-1"
        assert command == _command()
        self.events.append("update")

    def sync_request_status(self, request_id: str) -> WorkPlanMonitoringSummaryRead:
        assert request_id == "request-1"
        self.events.append("sync")
        return _summary()


def _in_progress(progress: int = 40) -> WorkItemLifecycleState:
    return WorkItemLifecycleState("item-1", "request-1", "IN_PROGRESS", progress, 1)


def test_work_item_progress_command_preserves_read_authorize_audit_update_sync_order() -> None:
    port = _ProgressPort(_in_progress())

    result = update_workbench_work_item_progress(
        port,
        "item-1",
        _command(),
        authorize=lambda: port.events.append("authorize"),
        audit=lambda: port.events.append("audit"),
    )

    assert result.request_id == "request-1"
    assert result.summary == _summary()
    assert result.changed is True
    assert port.events == ["read", "authorize", "audit", "update", "sync"]


def test_work_item_progress_noop_keeps_authorization_audit_and_uses_canonical_summary_without_sync() -> None:
    port = _ProgressPort(_in_progress(progress=60))

    result = update_workbench_work_item_progress(
        port,
        "item-1",
        _command(),
        authorize=lambda: port.events.append("authorize"),
        audit=lambda: port.events.append("audit"),
    )

    assert result.changed is False
    assert port.events == ["read", "authorize", "audit", "summary"]


def test_work_item_progress_stops_before_authorization_when_the_item_is_missing() -> None:
    port = _ProgressPort(None)

    with pytest.raises(WorkItemNotFoundError):
        update_workbench_work_item_progress(
            port,
            "item-1",
            _command(),
            authorize=lambda: port.events.append("authorize"),
            audit=lambda: port.events.append("audit"),
        )

    assert port.events == ["read"]


def test_work_item_progress_stops_after_authorization_failure_before_audit_or_write() -> None:
    port = _ProgressPort(_in_progress())

    with pytest.raises(PermissionError):
        update_workbench_work_item_progress(
            port,
            "item-1",
            _command(),
            authorize=lambda: (_ for _ in ()).throw(PermissionError()),
            audit=lambda: port.events.append("audit"),
        )

    assert port.events == ["read"]


@pytest.mark.parametrize(
    ("item", "command", "error", "events"),
    [
        (_in_progress(), _command(30), WorkItemProgressNotMonotonicError, ["read", "authorize", "audit"]),
        (WorkItemLifecycleState("item-1", "request-1", "READY", 0, 1), _command(), WorkItemNotInProgressError, ["read", "authorize", "audit"]),
    ],
)
def test_work_item_progress_rejects_invalid_state_before_any_summary_or_update(
    item: WorkItemLifecycleState,
    command: WorkItemProgressCommand,
    error: type[Exception],
    events: list[str],
) -> None:
    port = _ProgressPort(item)

    with pytest.raises(error):
        update_workbench_work_item_progress(
            port,
            "item-1",
            command,
            authorize=lambda: port.events.append("authorize"),
            audit=lambda: port.events.append("audit"),
        )

    assert port.events == events


def test_sql_progress_adapter_uses_one_connection_for_uow_read_update_and_monitoring(monkeypatch) -> None:
    calls: list[tuple[object, ...]] = []

    class _Connection:
        def execute(self, statement: str, values: list[object]) -> None:
            calls.append(("update", statement, values))

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
            return {"id": item_id, "request_id": "request-1", "status": "IN_PROGRESS", "progress": 40, "sequence_no": 1}

    monkeypatch.setattr(workbench_persistence, "WorkbenchRepository", _Repository)
    monkeypatch.setattr(workbench_persistence, "_utcnow_naive", lambda: datetime(2026, 8, 30, 10, 0, 0))
    monkeypatch.setattr(workbench_persistence, "request_monitoring_summary", lambda actual, request_id: _record_summary(calls, "summary", actual, request_id))
    monkeypatch.setattr(workbench_persistence, "sync_request_status", lambda actual, request_id: _record_summary(calls, "sync", actual, request_id))

    adapter = SQLWorkbenchWorkItemProgressCommand(connection)  # type: ignore[arg-type]
    adapter.begin_transaction()
    assert adapter.work_item("item-1") == _in_progress()
    adapter.update_work_item_progress("item-1", _command())
    assert adapter.request_monitoring_summary("request-1") == _summary()
    assert adapter.sync_request_status("request-1") == _summary()
    adapter.commit_transaction()
    adapter.rollback_transaction()

    assert calls == [
        ("begin",),
        ("read", "item-1"),
        (
            "update",
            "UPDATE request_work_items SET progress=?, progress_updated_by=?, progress_updated_at=? WHERE id=?",
            [60, "principal actor", datetime(2026, 8, 30, 10, 0, 0), "item-1"],
        ),
        ("summary", connection, "request-1"),
        ("sync", connection, "request-1"),
        ("commit",),
        ("rollback",),
    ]


def _record_summary(
    calls: list[tuple[object, ...]],
    event: str,
    connection: object,
    request_id: str,
) -> WorkPlanMonitoringSummaryRead:
    calls.append((event, connection, request_id))
    return _summary()
