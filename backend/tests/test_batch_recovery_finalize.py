from datetime import datetime

import duckdb
import pytest

from app.adapters.persistence import batch_recovery as batch_recovery_adapter
from app.application.workbench.batch_recovery import (
    claim_workbench_batch_recovery_lease,
    finalize_workbench_batch_recovery_attempt,
)
from app.database import connect
from app.domains.workbench.models import (
    BatchRecoveryFinalizeCommand,
    BatchRecoveryFinalizeRead,
    BatchRecoveryLeaseClaimCommand,
    BatchRecoveryLeaseCommandInvalidError,
    BatchRecoveryLeaseLostError,
)
from app.repositories.workbench import WorkbenchRepository
from app.schemas.workbench import DemoRunCreate
from app.services.demo_runner import DemoRunnerService


_SENTINEL_OCCURRED_AT = datetime(2026, 8, 31, 9, 30)


class _FinalizePort:
    def __init__(self, result: BatchRecoveryFinalizeRead | None) -> None:
        self.result = result
        self.calls: list[tuple[object, ...]] = []

    def begin_transaction(self) -> None:
        self.calls.append(("begin",))

    def commit_transaction(self) -> None:
        self.calls.append(("commit",))

    def rollback_transaction(self) -> None:
        self.calls.append(("rollback",))

    def finalize_batch_recovery_attempt(self, attempt_id: str, command: BatchRecoveryFinalizeCommand):
        self.calls.append(("finalize", attempt_id, command))
        return self.result


def _result() -> BatchRecoveryFinalizeRead:
    return BatchRecoveryFinalizeRead(
        attempt_id="attempt-1",
        workflow_run_id="run-1",
        work_item_id="work-item-1",
        request_id="request-1",
        dispatch_id="dispatch-recovery-attempt-1",
        completed_at=_SENTINEL_OCCURRED_AT,
    )


@pytest.mark.unit
def test_finalize_validates_before_port_and_commits_only_after_fenced_write() -> None:
    command = BatchRecoveryFinalizeCommand("runner-a", "token-a", 1)
    port = _FinalizePort(_result())

    assert finalize_workbench_batch_recovery_attempt(port, "attempt-1", command) == _result()
    assert port.calls == [("begin",), ("finalize", "attempt-1", command), ("commit",)]

    for invalid in (
        BatchRecoveryFinalizeCommand(" runner-a", "token-a", 1),
        BatchRecoveryFinalizeCommand("runner-a", " token-a", 1),
        BatchRecoveryFinalizeCommand("runner-a", "token-a", 0),
    ):
        with pytest.raises(BatchRecoveryLeaseCommandInvalidError):
            finalize_workbench_batch_recovery_attempt(port, "attempt-1", invalid)
    assert len(port.calls) == 3


@pytest.mark.unit
def test_finalize_lost_or_driver_failure_rolls_back_once_and_fails_closed() -> None:
    command = BatchRecoveryFinalizeCommand("runner-a", "token-a", 1)
    lost_port = _FinalizePort(None)
    with pytest.raises(BatchRecoveryLeaseLostError):
        finalize_workbench_batch_recovery_attempt(lost_port, "attempt-1", command)
    assert lost_port.calls == [("begin",), ("finalize", "attempt-1", command), ("rollback",)]

    error = RuntimeError("write failure")

    class _ExplodingPort(_FinalizePort):
        def finalize_batch_recovery_attempt(self, attempt_id: str, command: BatchRecoveryFinalizeCommand):
            self.calls.append(("finalize", attempt_id, command))
            raise error

    exploding_port = _ExplodingPort(_result())
    with pytest.raises(RuntimeError) as raised:
        finalize_workbench_batch_recovery_attempt(exploding_port, "attempt-1", command)
    assert raised.value is error
    assert exploding_port.calls == [("begin",), ("finalize", "attempt-1", command), ("rollback",)]


@pytest.mark.unit
def test_finalize_commit_failure_preserves_driver_error_and_rolls_back_once() -> None:
    command = BatchRecoveryFinalizeCommand("runner-a", "token-a", 1)
    error = RuntimeError("commit failure")

    class _CommitExplodesPort(_FinalizePort):
        def commit_transaction(self) -> None:
            self.calls.append(("commit",))
            raise error

    port = _CommitExplodesPort(_result())
    with pytest.raises(RuntimeError) as raised:
        finalize_workbench_batch_recovery_attempt(port, "attempt-1", command)
    assert raised.value is error
    assert port.calls == [("begin",), ("finalize", "attempt-1", command), ("commit",), ("rollback",)]


def _recoverable_attempt() -> tuple[str, str]:
    """Persist an exact runner crash-window identity without a dispatch record."""
    with connect() as conn:
        repository = WorkbenchRepository(conn)
        item = next(item for item in repository.work_items("request-drop-001") if item["status"] == "IN_PROGRESS")
        profile = repository.get_batch_profile_for_task(item["task_type_id"], int(item["task_type_version"]))
        assert profile is not None
        attempt_id = "recovery-finalize-attempt"
        created_at = datetime(2026, 8, 31, 9, 0)
        repository.insert_batch_attempt({
            "id": attempt_id, "work_item_id": item["id"], "workflow_run_id": None,
            "batch_profile_id": profile["id"], "batch_profile_version": int(profile["version"]),
            "profile_snapshot_json": "{}", "command_preview": "demo --case crash-window",
            "idempotency_key": "recovery-finalize-key", "execution_mode": "DEMO_ONLY",
            "status": "QUEUED", "progress": 10, "last_message": "runner committed",
            "created_by": "runner-a", "created_at": created_at, "started_at": created_at,
            "completed_at": None,
        })
        run = DemoRunnerService(repository).create_run(
            DemoRunCreate(
                name="recovery finalize test", request_id=item["request_id"], execution_mode="DEMO_ONLY",
                nodes=[{
                    "node_key": item["node_key"], "task_type_id": item["task_type_id"],
                    "task_type_version": int(item["task_type_version"]), "depends_on": [],
                }],
                created_by="runner-a",
            ),
            run_id="demo-recovery-finalize", batch_attempt_id=attempt_id,
        )
        return attempt_id, str(run["id"])


def _claim(adapter: batch_recovery_adapter.SQLWorkbenchBatchRecoveryLease, attempt_id: str):
    return claim_workbench_batch_recovery_lease(
        adapter, attempt_id, BatchRecoveryLeaseClaimCommand("runner-a", "token-a", 0, 60)
    )


@pytest.mark.duckdb_integration
def test_sql_finalize_records_exact_provenance_and_clears_lease_atomically() -> None:
    attempt_id, workflow_run_id = _recoverable_attempt()
    with connect() as conn:
        adapter = batch_recovery_adapter.SQLWorkbenchBatchRecoveryLease(conn)
        lease = _claim(adapter, attempt_id)
        request_id = conn.execute(
            """SELECT item.request_id FROM request_work_items AS item
               JOIN batch_execution_attempts AS attempt ON attempt.work_item_id=item.id
               WHERE attempt.id=?""",
            [attempt_id],
        ).fetchone()[0]
        conn.execute("UPDATE analysis_requests SET status='READY' WHERE id=?", [request_id])
        assert conn.execute("SELECT status FROM analysis_requests WHERE id=?", [request_id]).fetchone() == ("READY",)
        before_clock = conn.execute("SELECT CURRENT_TIMESTAMP AT TIME ZONE 'UTC'").fetchone()[0]
        result = finalize_workbench_batch_recovery_attempt(
            adapter, attempt_id,
            BatchRecoveryFinalizeCommand("runner-a", "token-a", lease.generation),
        )
        after_clock = conn.execute("SELECT CURRENT_TIMESTAMP AT TIME ZONE 'UTC'").fetchone()[0]

        assert result.attempt_id == attempt_id
        assert result.workflow_run_id == workflow_run_id
        assert isinstance(result.completed_at, datetime)
        assert result.completed_at != _SENTINEL_OCCURRED_AT
        assert before_clock <= result.completed_at <= after_clock
        assert result.dispatch_id == f"dispatch-recovery-{attempt_id}"
        assert conn.execute("SELECT status FROM analysis_requests WHERE id=?", [request_id]).fetchone() == ("IN_PROGRESS",)
        attempt = conn.execute(
            """SELECT status, progress, completed_at, recovery_lease_owner_id,
                      recovery_lease_token, recovery_lease_generation,
                      recovery_lease_acquired_at, recovery_lease_expires_at
               FROM batch_execution_attempts WHERE id=?""",
            [attempt_id],
        ).fetchone()
        assert attempt == ("SUCCEEDED", 100, result.completed_at, None, None, lease.generation, None, None)
        assert conn.execute(
            """SELECT event_index, event_type, level, progress, occurred_at
               FROM batch_execution_events WHERE attempt_id=?""", [attempt_id]
        ).fetchall() == [(2, "SUCCEEDED", "INFO", 100, result.completed_at)]
        assert conn.execute(
            """SELECT id, workflow_run_id, work_item_id, batch_profile_id, attempt_id,
                      command_preview, status, created_by, created_at
               FROM batch_dispatches WHERE attempt_id=?""", [attempt_id]
        ).fetchone() == (
            result.dispatch_id, workflow_run_id, result.work_item_id,
            conn.execute("SELECT batch_profile_id FROM batch_execution_attempts WHERE id=?", [attempt_id]).fetchone()[0],
            attempt_id, "demo --case crash-window", "RECORDED_DEMO", "runner-a", result.completed_at,
        )
        assert conn.execute(
            "SELECT progress, progress_updated_by, progress_updated_at FROM request_work_items WHERE id=?",
            [result.work_item_id],
        ).fetchone() == (90, "runner-a", result.completed_at)
        public_attempt = WorkbenchRepository(conn).batch_attempt(attempt_id)
        assert public_attempt is not None
        assert not any(key.startswith("recovery_lease_") for key in public_attempt)
        after_finalize = conn.execute(
            "SELECT status, progress, completed_at FROM batch_execution_attempts WHERE id=?", [attempt_id]
        ).fetchone()
        with pytest.raises(BatchRecoveryLeaseLostError):
            finalize_workbench_batch_recovery_attempt(
                adapter, attempt_id, BatchRecoveryFinalizeCommand("runner-a", "token-a", lease.generation)
            )
        assert conn.execute(
            "SELECT status, progress, completed_at FROM batch_execution_attempts WHERE id=?", [attempt_id]
        ).fetchone() == after_finalize
        assert conn.execute("SELECT count(*) FROM batch_dispatches WHERE attempt_id=?", [attempt_id]).fetchone() == (1,)


@pytest.mark.duckdb_integration
def test_sql_finalize_stale_or_expired_lease_never_mutates_crash_window() -> None:
    attempt_id, _ = _recoverable_attempt()
    with connect() as conn:
        adapter = batch_recovery_adapter.SQLWorkbenchBatchRecoveryLease(conn)
        lease = _claim(adapter, attempt_id)
        snapshot_sql = """SELECT status, progress, completed_at, recovery_lease_owner_id,
                                  recovery_lease_token, recovery_lease_generation,
                                  recovery_lease_acquired_at, recovery_lease_expires_at
                           FROM batch_execution_attempts WHERE id=?"""
        original = conn.execute(snapshot_sql, [attempt_id]).fetchone()
        with pytest.raises(BatchRecoveryLeaseLostError):
            finalize_workbench_batch_recovery_attempt(
                adapter, attempt_id,
                BatchRecoveryFinalizeCommand("runner-a", "token-a", lease.generation + 1),
            )
        assert conn.execute(snapshot_sql, [attempt_id]).fetchone() == original
        assert conn.execute("SELECT count(*) FROM batch_execution_events WHERE attempt_id=?", [attempt_id]).fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM batch_dispatches WHERE attempt_id=?", [attempt_id]).fetchone() == (0,)
        with pytest.raises(BatchRecoveryLeaseLostError):
            finalize_workbench_batch_recovery_attempt(
                adapter, attempt_id,
                BatchRecoveryFinalizeCommand("runner-a", "wrong-token", lease.generation),
            )
        assert conn.execute(snapshot_sql, [attempt_id]).fetchone() == original
        assert conn.execute("SELECT count(*) FROM batch_execution_events WHERE attempt_id=?", [attempt_id]).fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM batch_dispatches WHERE attempt_id=?", [attempt_id]).fetchone() == (0,)

        conn.execute(
            """UPDATE batch_execution_attempts
            SET recovery_lease_acquired_at=(CURRENT_TIMESTAMP AT TIME ZONE 'UTC') - INTERVAL '2 seconds',
                recovery_lease_expires_at=(CURRENT_TIMESTAMP AT TIME ZONE 'UTC') - INTERVAL '1 second'
            WHERE id=?""", [attempt_id],
        )
        expired = conn.execute(snapshot_sql, [attempt_id]).fetchone()
        with pytest.raises(BatchRecoveryLeaseLostError):
            finalize_workbench_batch_recovery_attempt(
                adapter, attempt_id,
                BatchRecoveryFinalizeCommand("runner-a", "token-a", lease.generation),
            )
        assert conn.execute(snapshot_sql, [attempt_id]).fetchone() == expired
        assert conn.execute("SELECT count(*) FROM batch_execution_events WHERE attempt_id=?", [attempt_id]).fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM batch_dispatches WHERE attempt_id=?", [attempt_id]).fetchone() == (0,)


@pytest.mark.duckdb_integration
def test_sql_finalize_missing_in_progress_work_item_rolls_back_fenced_cas() -> None:
    attempt_id, _ = _recoverable_attempt()
    with connect() as conn:
        adapter = batch_recovery_adapter.SQLWorkbenchBatchRecoveryLease(conn)
        lease = _claim(adapter, attempt_id)
        work_item_id = conn.execute(
            "SELECT work_item_id FROM batch_execution_attempts WHERE id=?", [attempt_id]
        ).fetchone()[0]
        conn.execute("UPDATE request_work_items SET status='READY' WHERE id=?", [work_item_id])
        before = conn.execute(
            """SELECT status, progress, completed_at, recovery_lease_owner_id, recovery_lease_token,
                      recovery_lease_generation, recovery_lease_acquired_at, recovery_lease_expires_at
               FROM batch_execution_attempts WHERE id=?""", [attempt_id]
        ).fetchone()
        with pytest.raises(RuntimeError, match="could not advance"):
            finalize_workbench_batch_recovery_attempt(
                adapter, attempt_id, BatchRecoveryFinalizeCommand("runner-a", "token-a", lease.generation)
            )
        assert conn.execute(
            """SELECT status, progress, completed_at, recovery_lease_owner_id, recovery_lease_token,
                      recovery_lease_generation, recovery_lease_acquired_at, recovery_lease_expires_at
               FROM batch_execution_attempts WHERE id=?""", [attempt_id]
        ).fetchone() == before
        assert conn.execute("SELECT count(*) FROM batch_execution_events WHERE attempt_id=?", [attempt_id]).fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM batch_dispatches WHERE attempt_id=?", [attempt_id]).fetchone() == (0,)


@pytest.mark.duckdb_integration
def test_sql_finalize_downstream_conflict_rolls_back_cas_and_preserves_lease() -> None:
    attempt_id, _ = _recoverable_attempt()
    with connect() as conn:
        adapter = batch_recovery_adapter.SQLWorkbenchBatchRecoveryLease(conn)
        lease = _claim(adapter, attempt_id)
        conn.execute(
            """INSERT INTO batch_execution_events
                (id, attempt_id, event_index, event_type, level, message, progress, occurred_at)
            VALUES ('preexisting-recovery-event', ?, 2, 'TEST', 'INFO', 'force rollback', 10, ?)""",
            [attempt_id, _SENTINEL_OCCURRED_AT],
        )
        before = conn.execute(
            """SELECT status, progress, completed_at, recovery_lease_owner_id, recovery_lease_token,
                      recovery_lease_generation, recovery_lease_acquired_at, recovery_lease_expires_at
               FROM batch_execution_attempts WHERE id=?""", [attempt_id]
        ).fetchone()
        with pytest.raises(duckdb.ConstraintException):
            finalize_workbench_batch_recovery_attempt(
                adapter, attempt_id,
                BatchRecoveryFinalizeCommand("runner-a", "token-a", lease.generation),
            )
        assert conn.execute(
            """SELECT status, progress, completed_at, recovery_lease_owner_id, recovery_lease_token,
                      recovery_lease_generation, recovery_lease_acquired_at, recovery_lease_expires_at
               FROM batch_execution_attempts WHERE id=?""", [attempt_id]
        ).fetchone() == before
        assert conn.execute("SELECT count(*) FROM batch_execution_events WHERE attempt_id=?", [attempt_id]).fetchone() == (1,)
        assert conn.execute("SELECT count(*) FROM batch_dispatches WHERE attempt_id=?", [attempt_id]).fetchone() == (0,)
