from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from app.adapters.persistence import workbench as workbench_persistence
from app.adapters.persistence.workbench import SQLWorkbenchBatchDispatchCommand
from app.application.workbench.commands import dispatch_workbench_batch
from app.domains.workbench.models import (
    BatchAttemptAlreadyRejectedError,
    BatchDispatchCommand,
    BatchDispatchContext,
    BatchDispatchPreflight,
    BatchDispatchResult,
    BatchDemoRunValidationError,
    BatchPreflightFailedError,
    BatchPreflightRejectedError,
    BatchProfileNotConfiguredError,
    BatchProfileTaskMismatchError,
    BatchWorkItemNotInProgressError,
    WorkItemNotFoundError,
)
from app.repositories.workbench import WorkbenchRepository
from app.services.demo_runner import WorkbenchValidationError
pytestmark = pytest.mark.unit


def _command() -> BatchDispatchCommand:
    return BatchDispatchCommand("profile-1", "request-1:item-1:run-1", "principal actor")


def _item() -> dict[str, object]:
    return {"id": "item-1", "request_id": "request-1", "status": "IN_PROGRESS", "task_type_id": "task-1", "task_type_version": 1, "node_key": "solve", "display_name": "Solve"}


def _profile() -> dict[str, object]:
    return {"id": "profile-1", "version": 2, "is_active": True, "task_type_id": "task-1", "task_type_version": 1, "name": "Local solver", "solver_path": "/opt/solver", "working_directory": "/tmp/work", "arguments_template": "{input}"}


class _BatchPort:
    def __init__(self, *, preflight_error=None, runner_error=None) -> None:
        self.events: list[str] = []
        self.item = _item()
        self.profile = _profile()
        self.preflight_error = preflight_error
        self.runner_error = runner_error
        self.run = {"id": "run-1", "execution_mode": "DEMO_ONLY"}

    def work_item(self, item_id): self.events.append("read"); return self.item
    def existing_attempt(self, item_id, idempotency_key): self.events.append("existing_attempt"); return None
    def batch_profile(self, task_type_id, task_type_version): self.events.append("profile"); return self.profile
    def load_run(self, run_id): self.events.append("load_run"); return self.run if run_id == "run-1" else None
    def begin_transaction(self): self.events.append("begin")
    def commit_transaction(self): self.events.append("commit")
    def rollback_transaction(self): self.events.append("rollback")
    def insert_preflight_attempt(self, context, command): self.events.append("insert_attempt")
    def insert_preflight_event(self, context): self.events.append("insert_preflight_event")
    def preflight(self, context):
        self.events.append("preflight")
        if self.preflight_error: raise self.preflight_error
        return BatchDispatchPreflight('"/opt/solver" <input>', "/tmp/work")
    def reject_attempt(self, context, *, message, completed_at): self.events.append("reject_attempt")
    def insert_rejected_event(self, context, *, message, occurred_at): self.events.append("insert_rejected_event")
    def queue_attempt(self, context): self.events.append("queue_attempt")
    def insert_queued_event(self, context, *, occurred_at): self.events.append("insert_queued_event")
    def create_demo_run(self, context, command):
        self.events.append("create_demo_run")
        if self.runner_error: raise self.runner_error
        return self.run
    def fail_attempt(self, context, *, message, completed_at): self.events.append("fail_attempt")
    def insert_failed_event(self, context, *, message, occurred_at): self.events.append("insert_failed_event")
    def succeed_attempt(self, context, *, workflow_run_id, completed_at): self.events.append("succeed_attempt")
    def insert_succeeded_event(self, context, *, occurred_at): self.events.append("insert_succeeded_event")
    def insert_batch_dispatch(self, context, preflight, command, *, workflow_run_id, created_at): self.events.append("insert_dispatch")
    def update_work_item_progress(self, context, command, *, updated_at): self.events.append("progress")
    def sync_request_status(self, request_id): self.events.append("sync")


def _run(port, *, authorize=None, audit=None):
    authorize = authorize or (lambda: port.events.append("authorize"))
    audit = audit or (lambda: port.events.append("audit"))
    return dispatch_workbench_batch(port, "item-1", _command(), authorize=authorize, audit=audit)


def test_batch_dispatch_insert_race_rolls_back_then_replays_the_requeried_run():
    from app.domains.workbench.models import BatchAttemptInsertConflictError

    class _RacingPort(_BatchPort):
        def __init__(self):
            super().__init__()
            self.reads = 0

        def existing_attempt(self, item_id, idempotency_key):
            self.reads += 1
            self.events.append("existing_attempt")
            return None if self.reads == 1 else {"status": "SUCCEEDED", "workflow_run_id": "run-1"}

        def insert_preflight_attempt(self, context, command):
            self.events.append("insert_attempt")
            raise BatchAttemptInsertConflictError()

    port = _RacingPort()
    result = _run(port)
    assert result.run == port.run
    assert port.events == ["read", "authorize", "existing_attempt", "profile", "begin", "audit", "insert_attempt", "rollback", "existing_attempt", "load_run"]


def test_batch_dispatch_insert_race_propagates_requery_attempt_failure_after_one_rollback():
    from app.domains.workbench.models import BatchAttemptInsertConflictError

    original_error = RuntimeError("requery failed")

    class _RacingPort(_BatchPort):
        def __init__(self):
            super().__init__()
            self.reads = 0

        def existing_attempt(self, item_id, idempotency_key):
            self.reads += 1
            self.events.append("existing_attempt")
            if self.reads == 1:
                return None
            raise original_error

        def insert_preflight_attempt(self, context, command):
            self.events.append("insert_attempt")
            raise BatchAttemptInsertConflictError()

    port = _RacingPort()
    with pytest.raises(RuntimeError) as exc_info:
        _run(port)

    assert exc_info.value is original_error
    assert port.events == [
        "read",
        "authorize",
        "existing_attempt",
        "profile",
        "begin",
        "audit",
        "insert_attempt",
        "rollback",
        "existing_attempt",
    ]
    assert port.events.count("rollback") == 1


def test_batch_dispatch_insert_race_propagates_requery_run_failure_after_one_rollback():
    from app.domains.workbench.models import BatchAttemptInsertConflictError

    original_error = RuntimeError("run requery failed")

    class _RacingPort(_BatchPort):
        def __init__(self):
            super().__init__()
            self.reads = 0

        def existing_attempt(self, item_id, idempotency_key):
            self.reads += 1
            self.events.append("existing_attempt")
            return None if self.reads == 1 else {"status": "SUCCEEDED", "workflow_run_id": "run-1"}

        def insert_preflight_attempt(self, context, command):
            self.events.append("insert_attempt")
            raise BatchAttemptInsertConflictError()

        def load_run(self, run_id):
            self.events.append("load_run")
            raise original_error

    port = _RacingPort()
    with pytest.raises(RuntimeError) as exc_info:
        _run(port)

    assert exc_info.value is original_error
    assert port.events == [
        "read",
        "authorize",
        "existing_attempt",
        "profile",
        "begin",
        "audit",
        "insert_attempt",
        "rollback",
        "existing_attempt",
        "load_run",
    ]
    assert port.events.count("rollback") == 1


def test_batch_attempt_idempotency_conflict_accepts_direct_sqlstate() -> None:
    error = RuntimeError("database rejected the insert")
    error.sqlstate = "23505"  # type: ignore[attr-defined]
    error.constraint_name = "batch_execution_attempts_work_item_id_idempotency_key_key"  # type: ignore[attr-defined]

    assert workbench_persistence._is_batch_attempt_idempotency_conflict(error) is True


@pytest.mark.parametrize("code_attribute", ["sqlstate", "pgcode"])
def test_batch_attempt_idempotency_conflict_accepts_nested_driver_codes(code_attribute: str) -> None:
    original = SimpleNamespace(
        **{
            code_attribute: "23505",
            "constraint_name": "batch_execution_attempts_work_item_id_idempotency_key_key",
        }
    )
    error = RuntimeError("database rejected the insert")
    error.orig = original  # type: ignore[attr-defined]

    assert workbench_persistence._is_batch_attempt_idempotency_conflict(error) is True


def test_batch_attempt_idempotency_conflict_accepts_duckdb_structural_message() -> None:
    error = RuntimeError(
        'Constraint Error: Duplicate key "work_item_id: item-1, idempotency_key: request-1" '
        "violates unique constraint"
    )

    assert workbench_persistence._is_batch_attempt_idempotency_conflict(error) is True


def test_batch_attempt_idempotency_conflict_rejects_unrelated_unique_constraint() -> None:
    error = RuntimeError(
        'Constraint Error: Duplicate key "email: user@example.com" violates '
        'unique constraint "users_email_key"'
    )
    error.sqlstate = "23505"  # type: ignore[attr-defined]

    assert workbench_persistence._is_batch_attempt_idempotency_conflict(error) is False


def test_batch_attempt_idempotency_conflict_rejects_unrelated_error() -> None:
    assert workbench_persistence._is_batch_attempt_idempotency_conflict(RuntimeError("permission denied")) is False


@pytest.mark.parametrize(
    "attempt",
    [
        {"id": "attempt-1", "status": "REJECTED", "workflow_run_id": None},
        {"id": "attempt-1", "status": "QUEUED", "workflow_run_id": "missing-run"},
    ],
    ids=["rejected", "unavailable-run"],
)
def test_batch_dispatch_insert_race_rejects_requeried_attempt_after_one_rollback(
    attempt: dict[str, object],
) -> None:
    from app.domains.workbench.models import BatchAttemptInsertConflictError

    class _RacingPort(_BatchPort):
        def __init__(self):
            super().__init__()
            self.reads = 0

        def existing_attempt(self, item_id, idempotency_key):
            self.reads += 1
            self.events.append("existing_attempt")
            return None if self.reads == 1 else attempt

        def insert_preflight_attempt(self, context, command):
            self.events.append("insert_attempt")
            raise BatchAttemptInsertConflictError()

    port = _RacingPort()

    with pytest.raises(BatchAttemptAlreadyRejectedError) as exc_info:
        _run(port)

    assert exc_info.value.detail == {"attempt": attempt}
    expected_events = [
        "read",
        "authorize",
        "existing_attempt",
        "profile",
        "begin",
        "audit",
        "insert_attempt",
        "rollback",
        "existing_attempt",
    ]
    if attempt["status"] == "SUCCEEDED" and attempt["workflow_run_id"]:
        expected_events.append("load_run")
    assert port.events == expected_events
    assert port.events.count("rollback") == 1


def test_batch_dispatch_success_uses_three_committed_phases_in_order() -> None:
    port = _BatchPort()
    result = _run(port)
    assert isinstance(result, BatchDispatchResult)
    assert result.run == port.run
    assert port.events == ["read", "authorize", "existing_attempt", "profile", "begin", "audit", "insert_attempt", "insert_preflight_event", "preflight", "queue_attempt", "insert_queued_event", "commit", "create_demo_run", "begin", "succeed_attempt", "insert_succeeded_event", "insert_dispatch", "progress", "sync", "commit", "load_run"]


def test_batch_dispatch_preflight_rejection_commits_rejection_without_rollback() -> None:
    port = _BatchPort(preflight_error=BatchPreflightFailedError("BATCH_PATH_NOT_ABSOLUTE", "bad path"))
    with pytest.raises(BatchPreflightRejectedError): _run(port)
    assert port.events == ["read", "authorize", "existing_attempt", "profile", "begin", "audit", "insert_attempt", "insert_preflight_event", "preflight", "reject_attempt", "insert_rejected_event", "commit"]
    assert "rollback" not in port.events


def test_batch_dispatch_runner_validation_marks_failed_and_commits_without_rollback() -> None:
    port = _BatchPort(runner_error=BatchDemoRunValidationError("invalid workflow"))
    with pytest.raises(BatchDemoRunValidationError, match="invalid workflow"): _run(port)
    assert port.events == ["read", "authorize", "existing_attempt", "profile", "begin", "audit", "insert_attempt", "insert_preflight_event", "preflight", "queue_attempt", "insert_queued_event", "commit", "create_demo_run", "begin", "fail_attempt", "insert_failed_event", "commit"]
    assert "rollback" not in port.events


def test_batch_dispatch_missing_item_stops_before_authorization_or_transactions() -> None:
    port = _BatchPort()
    port.item = None
    with pytest.raises(WorkItemNotFoundError): _run(port, authorize=lambda: port.events.append("authorize"))
    assert port.events == ["read"]


def test_batch_dispatch_replays_an_existing_run_before_state_or_profile_checks() -> None:
    port = _BatchPort()
    port.existing_attempt = lambda _item_id, _key: port.events.append("existing_attempt") or {"status": "SUCCEEDED", "workflow_run_id": "run-1"}

    result = _run(port)

    assert result.run == port.run
    assert port.events == ["read", "authorize", "existing_attempt", "load_run"]


def test_batch_dispatch_does_not_replay_a_queued_attempt_even_when_its_runner_run_is_linked() -> None:
    port = _BatchPort()
    attempt = {"id": "attempt-queued", "status": "QUEUED", "workflow_run_id": "run-1"}
    port.existing_attempt = lambda _item_id, _key: port.events.append("existing_attempt") or attempt

    with pytest.raises(BatchAttemptAlreadyRejectedError) as exc_info:
        _run(port)

    assert exc_info.value.detail == {"attempt": attempt}
    assert port.events == ["read", "authorize", "existing_attempt"]


def test_runner_link_fails_closed_when_attempt_state_changed_before_the_link_update() -> None:
    class _Result:
        def fetchone(self):
            return (None,)

    class _Connection:
        def __init__(self) -> None:
            self.calls: list[tuple[str, list[object] | None]] = []

        def execute(self, sql: str, params=None):
            self.calls.append((sql, params))
            return _Result()

    connection = _Connection()
    repository = WorkbenchRepository(connection)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="conflicts"):
        repository.link_batch_attempt_workflow_run("attempt-1", "demo-attempt-1")

    update_sql, update_params = connection.calls[0]
    assert "status='QUEUED'" in update_sql
    assert "workflow_run_id IS NULL" in update_sql
    assert update_params == ["demo-attempt-1", "attempt-1"]


def test_batch_dispatch_rejects_an_existing_attempt_without_a_linked_run() -> None:
    port = _BatchPort()
    attempt = {"id": "attempt-1", "status": "REJECTED", "workflow_run_id": None}
    port.existing_attempt = lambda _item_id, _key: port.events.append("existing_attempt") or attempt

    with pytest.raises(BatchAttemptAlreadyRejectedError) as exc_info:
        _run(port)

    assert exc_info.value.detail == {"attempt": attempt}
    assert port.events == ["read", "authorize", "existing_attempt"]


@pytest.mark.parametrize(
    ("configure", "error"),
    [
        (lambda port: port.item.update(status="READY"), BatchWorkItemNotInProgressError),
        (lambda port: port.profile.update(id="profile-other"), BatchProfileTaskMismatchError),
        (lambda port: setattr(port, "profile", None), BatchProfileTaskMismatchError),
    ],
)
def test_batch_dispatch_stops_at_pre_transaction_guards(configure, error) -> None:
    port = _BatchPort()
    configure(port)

    with pytest.raises(error):
        _run(port)

    assert "begin" not in port.events


def test_batch_dispatch_reports_missing_profile_when_client_does_not_pin_one() -> None:
    port = _BatchPort()
    port.profile = None
    command = BatchDispatchCommand(None, "request-1:item-1:run-1", "principal actor")

    with pytest.raises(BatchProfileNotConfiguredError):
        dispatch_workbench_batch(
            port,
            "item-1",
            command,
            authorize=lambda: port.events.append("authorize"),
            audit=lambda: port.events.append("audit"),
        )

    assert "begin" not in port.events


def test_batch_dispatch_rolls_back_an_unexpected_first_phase_failure() -> None:
    port = _BatchPort()

    def fail_insert(_context, _command):
        port.events.append("insert_attempt")
        raise RuntimeError("insert failed")

    port.insert_preflight_attempt = fail_insert

    with pytest.raises(RuntimeError, match="insert failed"):
        _run(port)

    assert port.events[-2:] == ["insert_attempt", "rollback"]


def test_sql_batch_adapter_translates_demo_runner_validation_to_domain_error(monkeypatch) -> None:
    repository = object()

    class _Runner:
        def __init__(self, actual_repository: object) -> None:
            assert actual_repository is repository

        def create_run(self, _payload: object, *, run_id: str, batch_attempt_id: str) -> dict[str, object]:
            assert run_id == "demo-attempt-1"
            assert batch_attempt_id == "attempt-1"
            raise WorkbenchValidationError("invalid workflow")

    monkeypatch.setattr(workbench_persistence, "WorkbenchRepository", lambda _connection: repository)
    monkeypatch.setattr(workbench_persistence, "DemoRunnerService", _Runner)
    adapter = SQLWorkbenchBatchDispatchCommand(object())  # type: ignore[arg-type]
    context = BatchDispatchContext(
        work_item=_item(),
        profile=_profile(),
        attempt_id="attempt-1",
        started_at=datetime(2026, 8, 30, 12, 0, 0),
        profile_snapshot_json="{}",
        initial_command_preview='"/opt/solver" {input}',
    )

    with pytest.raises(BatchDemoRunValidationError, match="invalid workflow") as exc_info:
        adapter.create_demo_run(context, _command())

    assert isinstance(exc_info.value.__cause__, WorkbenchValidationError)
