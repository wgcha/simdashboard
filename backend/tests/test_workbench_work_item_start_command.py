from __future__ import annotations

from datetime import datetime

import pytest

from app.adapters.persistence import workbench as workbench_persistence
from app.adapters.persistence.workbench import SQLWorkbenchWorkItemStartCommand
from app.application.workbench.commands import start_workbench_work_item
from app.domains.workbench.models import (
    WorkItemLifecycleState,
    WorkItemNotFoundError,
    WorkItemNotReadyError,
    WorkItemPrerequisiteIncompleteError,
    WorkItemStartCommand,
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


def _command() -> WorkItemStartCommand:
    return WorkItemStartCommand(started_by="principal actor")


class _StartPort:
    def __init__(
        self,
        item: WorkItemLifecycleState | None,
        *,
        current_item_id: str | None = "item-1",
        incomplete_prior_count: int = 0,
    ) -> None:
        self.item = item
        self.current_item_id_value = current_item_id
        self.incomplete_prior_count_value = incomplete_prior_count
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

    def start_work_item(self, item_id: str, command: WorkItemStartCommand) -> None:
        assert item_id == "item-1"
        assert command == _command()
        self.events.append("start")

    def sync_request_status(self, request_id: str) -> WorkPlanMonitoringSummaryRead:
        assert request_id == "request-1"
        self.events.append("sync")
        return _summary()


def _ready() -> WorkItemLifecycleState:
    return WorkItemLifecycleState("item-1", "request-1", "READY", 0, 2)


def _run(port: _StartPort):
    return start_workbench_work_item(
        port,
        "item-1",
        _command(),
        authorize=lambda: port.events.append("authorize"),
        audit=lambda: port.events.append("audit"),
    )


def test_start_command_preserves_read_authorize_audit_current_prior_update_sync_order() -> None:
    port = _StartPort(_ready())

    result = _run(port)

    assert result.request_id == "request-1"
    assert result.summary == _summary()
    assert result.changed is True
    assert port.events == ["read", "authorize", "audit", "current", "prior", "start", "sync"]


@pytest.mark.parametrize("status", ["IN_PROGRESS", "COMPLETED"])
def test_start_command_is_idempotent_after_authorization_and_audit(status: str) -> None:
    port = _StartPort(WorkItemLifecycleState("item-1", "request-1", status, 1, 2))

    result = _run(port)

    assert result.changed is False
    assert port.events == ["read", "authorize", "audit", "summary"]


def test_start_command_stops_before_authorization_when_missing() -> None:
    port = _StartPort(None)

    with pytest.raises(WorkItemNotFoundError):
        _run(port)

    assert port.events == ["read"]


def test_start_command_stops_after_authorization_failure_before_audit_or_state_reads() -> None:
    port = _StartPort(_ready())

    with pytest.raises(PermissionError):
        start_workbench_work_item(
            port,
            "item-1",
            _command(),
            authorize=lambda: (_ for _ in ()).throw(PermissionError()),
            audit=lambda: port.events.append("audit"),
        )

    assert port.events == ["read"]


def test_start_command_rejects_not_ready_before_prerequisite_lookup() -> None:
    port = _StartPort(_ready(), current_item_id="another-item")

    with pytest.raises(WorkItemNotReadyError) as exc_info:
        _run(port)

    assert exc_info.value.item_id == "item-1"
    assert exc_info.value.current_item_id == "another-item"
    assert port.events == ["read", "authorize", "audit", "current"]


def test_start_command_rejects_incomplete_prerequisite_before_update() -> None:
    port = _StartPort(_ready(), incomplete_prior_count=1)

    with pytest.raises(WorkItemPrerequisiteIncompleteError) as exc_info:
        _run(port)

    assert exc_info.value.item_id == "item-1"
    assert port.events == ["read", "authorize", "audit", "current", "prior"]


def test_sql_start_adapter_uses_one_connection_for_uow_state_queries_update_and_monitoring(monkeypatch) -> None:
    calls: list[tuple[object, ...]] = []

    class _Result:
        def __init__(self, row: tuple[object, ...] | None) -> None:
            self._row = row

        def fetchone(self) -> tuple[object, ...] | None:
            return self._row

    class _Connection:
        def execute(self, statement: str, values: list[object]) -> _Result:
            calls.append(("sql", statement, values))
            if "SELECT id FROM request_work_items" in statement:
                return _Result(("item-1",))
            if "SELECT count(*)" in statement:
                return _Result((0,))
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
            return {"id": item_id, "request_id": "request-1", "status": "READY", "progress": 0, "sequence_no": 2}

    monkeypatch.setattr(workbench_persistence, "WorkbenchRepository", _Repository)
    monkeypatch.setattr(workbench_persistence, "_utcnow_naive", lambda: datetime(2026, 8, 30, 10, 0, 0))
    monkeypatch.setattr(workbench_persistence, "request_monitoring_summary", lambda actual, request_id: _record_summary(calls, "summary", actual, request_id))
    monkeypatch.setattr(workbench_persistence, "sync_request_status", lambda actual, request_id: _record_summary(calls, "sync", actual, request_id))

    adapter = SQLWorkbenchWorkItemStartCommand(connection)  # type: ignore[arg-type]
    adapter.begin_transaction()
    assert adapter.work_item("item-1") == _ready()
    assert adapter.current_work_item_id("request-1") == "item-1"
    assert adapter.incomplete_prior_count("request-1", 2) == 0
    adapter.start_work_item("item-1", _command())
    assert adapter.request_monitoring_summary("request-1") == _summary()
    assert adapter.sync_request_status("request-1") == _summary()
    adapter.commit_transaction()
    adapter.rollback_transaction()

    assert calls[0:2] == [("begin",), ("read", "item-1")]
    assert calls[2][0:1] == ("sql",)
    assert calls[3][0:1] == ("sql",)
    assert calls[4] == (
        "sql",
        """
            UPDATE request_work_items
            SET status = 'IN_PROGRESS', progress = 1, progress_updated_by = ?, progress_updated_at = ?, started_by = ?, started_at = ?
            WHERE id = ? AND status = 'READY'
            """,
        ["principal actor", datetime(2026, 8, 30, 10, 0, 0), "principal actor", datetime(2026, 8, 30, 10, 0, 0), "item-1"],
    )
    assert calls[5:] == [
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
