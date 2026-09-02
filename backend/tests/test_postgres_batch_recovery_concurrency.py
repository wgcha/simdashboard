"""Opt-in PostgreSQL release gate for internal batch-recovery leases.

This module intentionally uses no router, scheduler, retry endpoint, or
production connection.  It is runnable only against the explicitly named
PostgreSQL test database under the least-privilege application role.  Each
test creates a UUID-scoped catalog and crash-window graph, then removes only
those rows in foreign-key order.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from threading import Barrier, BrokenBarrierError, Thread
from typing import Any, TypeVar
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.adapters.persistence.batch_recovery import SQLWorkbenchBatchRecoveryLease
from app.application.workbench.batch_recovery import (
    claim_workbench_batch_recovery_lease,
    finalize_workbench_batch_recovery_attempt,
    release_workbench_batch_recovery_lease,
    renew_workbench_batch_recovery_lease,
)
from app.config import database_settings
from app.database_connection import connect
from app.domains.workbench.models import (
    BatchRecoveryFinalizeCommand,
    BatchRecoveryFinalizeRead,
    BatchRecoveryLeaseClaimCommand,
    BatchRecoveryLeaseLostError,
    BatchRecoveryLeaseRead,
    BatchRecoveryLeaseReleaseCommand,
    BatchRecoveryLeaseRenewCommand,
    BatchRecoveryLeaseUnavailableError,
)
from app.repositories.workbench import WorkbenchRepository
from app.services.demo_runner import DemoRunnerService
from scripts.check_postgres_connection import _expected_head


pytestmark = pytest.mark.postgres_integration

T = TypeVar("T")
_FORBIDDEN_DATABASES = {"postgres", "template0", "template1", "simulation_dashboard"}
_RECOVERY_TABLES = (
    "projects",
    "analysis_requests",
    "task_type_versions",
    "request_type_versions",
    "batch_path_profiles",
    "batch_path_profile_versions",
    "request_work_plans",
    "request_work_items",
    "workflow_runs",
    "task_runs",
    "task_run_events",
    "batch_execution_attempts",
    "batch_execution_events",
    "batch_dispatches",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _is_dedicated_test_database(database: str) -> bool:
    """Keep this destructive release gate on a visibly named disposable target."""
    normalized = database.lower()
    return normalized == "simdashboard_recovery_test" or normalized.startswith(
        "simdashboard_recovery_test_"
    )


@pytest.fixture(autouse=True)
def postgres_release_gate() -> None:
    """Fail closed unless this is the dedicated, least-privilege PG test role."""
    enabled = (
        os.getenv("ANALYSIS_TEST_POSTGRES") == "1"
        and os.getenv("ANALYSIS_DB_BACKEND", "").strip().lower() == "postgresql"
    )
    if not enabled:
        pytest.skip("requires ANALYSIS_TEST_POSTGRES=1 and ANALYSIS_DB_BACKEND=postgresql")

    pool = database_settings().postgres_pool
    capacity = pool.request_pool_size + pool.request_max_overflow
    if capacity < 2:
        pytest.skip(
            "requires at least two independent request-pool connections "
            f"(POSTGRES_REQUEST_POOL_SIZE + POSTGRES_REQUEST_MAX_OVERFLOW = {capacity})"
        )

    expected_role = os.getenv("SIM_DASH_APP_ROLE", "simdashboard_app")
    owner_role = os.getenv("SIM_DASH_OWNER_ROLE", "simdashboard_owner")
    with connect() as connection:
        database, role = connection.execute("SELECT current_database(), current_user").fetchone()
        database_name = str(database)
        if database_name.lower() in _FORBIDDEN_DATABASES or not _is_dedicated_test_database(database_name):
            pytest.fail("batch-recovery concurrency tests require an explicitly named dedicated test database")
        if role != expected_role:
            pytest.fail("batch-recovery concurrency tests require the configured application role")
        if role == owner_role:
            pytest.fail("batch-recovery concurrency tests refuse the PostgreSQL owner role")
        attributes = connection.execute(
            "SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
        if attributes != (False, False, False):
            pytest.fail("batch-recovery concurrency tests require a non-privileged application role")
        ddl_privileges = connection.execute(
            "SELECT has_database_privilege(current_user, current_database(), 'CREATE'), "
            "has_schema_privilege(current_user, 'public', 'CREATE')"
        ).fetchone()
        if ddl_privileges != (False, False):
            pytest.fail("batch-recovery concurrency tests require an application role without DDL privilege")
        privileges = connection.execute(
            "SELECT " + ", ".join(
                "has_table_privilege(current_user, ?, 'SELECT,INSERT,UPDATE,DELETE')"
                for _ in _RECOVERY_TABLES
            ),
            [f"public.{table}" for table in _RECOVERY_TABLES],
        ).fetchone()
        if privileges != (True,) * len(_RECOVERY_TABLES):
            pytest.fail("batch-recovery concurrency tests require DML on every isolated recovery table")
        revisions = connection.execute("SELECT version_num FROM alembic_version").fetchall()
        if len(revisions) != 1 or revisions[0][0] != _expected_head():
            pytest.fail("batch-recovery concurrency tests require PostgreSQL at the code Alembic head")


@pytest.fixture
def recovery_target() -> Iterator[dict[str, str | int]]:
    """Create one isolated, exact DEMO_ONLY crash-window and owned catalog rows."""
    suffix = uuid4().hex
    target: dict[str, str | int] = {
        "project_id": f"postgres-recovery-project-{suffix}",
        "request_id": f"postgres-recovery-request-{suffix}",
        "request_type_id": f"postgres-recovery-request-type-{suffix}",
        "task_type_id": f"postgres-recovery-task-type-{suffix}",
        "profile_id": f"postgres-recovery-profile-{suffix}",
        "work_item_id": f"postgres-recovery-work-item-{suffix}",
        "attempt_id": f"postgres-recovery-attempt-{suffix}",
        "workflow_run_id": f"postgres-recovery-run-{suffix}",
        "task_run_id": f"postgres-recovery-task-run-{suffix}",
        "request_type_version": 1,
        "task_type_version": 1,
        "profile_version": 1,
    }
    now = _utcnow()
    node = {
        "node_key": "recovery-node",
        "task_type_id": target["task_type_id"],
        "task_type_version": 1,
        "depends_on": [],
    }
    try:
        with connect() as connection:
            connection.execute(
                "INSERT INTO projects (id, name, product_name, description, created_at) VALUES (?, ?, ?, ?, ?)",
                [target["project_id"], "PostgreSQL recovery concurrency", "pytest", "isolated release gate", now],
            )
            connection.execute(
                """INSERT INTO analysis_requests
                    (id, project_id, title, status, owner, requested_at, due_at, overall_note)
                VALUES (?, ?, ?, 'READY', ?, ?, NULL, ?)""",
                [target["request_id"], target["project_id"], "Recovery lease concurrency", "pytest", now, "isolated release gate"],
            )
            connection.execute(
                """INSERT INTO task_type_versions
                    (id, version, kind, display_name, description, supports_standalone,
                     input_artifact_types_json, output_artifact_types_json, parameter_schema_json,
                     demo_artifact_url, is_active, created_at)
                VALUES (?, 1, 'POST_PROCESS', ?, ?, true, ?, ?, ?, ?, true, ?)""",
                [target["task_type_id"], "Recovery task", "isolated recovery task", "[]", "[]", "{}", "demo://recovery", now],
            )
            connection.execute(
                """INSERT INTO request_type_versions
                    (id, version, display_name, description, allowed_task_types_json,
                     default_workflow_json, match_rules_json, is_active, created_at)
                VALUES (?, 1, ?, ?, ?, ?, ?, true, ?)""",
                [
                    target["request_type_id"], "Recovery request", "isolated recovery request",
                    json.dumps([{ "id": target["task_type_id"], "version": 1 }]),
                    json.dumps({"nodes": [node]}), "{}", now,
                ],
            )
            connection.execute(
                """INSERT INTO batch_path_profiles
                    (id, version, name, solver_path, working_directory, arguments_template,
                     environment_json, task_type_id, task_type_version, task_type_ids_json,
                     is_active, updated_by, created_at, updated_at)
                VALUES (?, 1, ?, ?, ?, ?, ?, ?, 1, ?, true, ?, ?, ?)""",
                [target["profile_id"], "Recovery profile", "/opt/pytest/solver", "/tmp", "--demo", "{}", target["task_type_id"], json.dumps([target["task_type_id"]]), "pytest", now, now],
            )
            connection.execute(
                """INSERT INTO batch_path_profile_versions
                    (id, version, name, solver_path, working_directory, arguments_template,
                     environment_json, task_type_id, task_type_version, task_type_ids_json,
                     is_active, created_by, created_at)
                VALUES (?, 1, ?, ?, ?, ?, ?, ?, 1, ?, true, ?, ?)""",
                [target["profile_id"], "Recovery profile", "/opt/pytest/solver", "/tmp", "--demo", "{}", target["task_type_id"], json.dumps([target["task_type_id"]]), "pytest", now],
            )
            connection.execute(
                """INSERT INTO request_work_plans
                    (request_id, request_type_id, request_type_version, scenario_name, source_type,
                     source_reference, requested_by, definition_snapshot_json, assigned_by, assigned_at)
                VALUES (?, ?, 1, ?, 'EXTERNAL_SYSTEM', ?, ?, ?, ?, ?)""",
                [target["request_id"], target["request_type_id"], "Recovery request", "pytest", "pytest", json.dumps({"nodes": [node]}), "pytest", now],
            )
            connection.execute(
                """INSERT INTO request_work_items
                    (id, request_id, node_key, task_type_id, task_type_version, sequence_no,
                     display_name, status, progress, owner, owner_user_id, started_by, started_at)
                VALUES (?, ?, 'recovery-node', ?, 1, 1, ?, 'IN_PROGRESS', 10, ?, NULL, ?, ?)""",
                [target["work_item_id"], target["request_id"], target["task_type_id"], "Recovery task", "pytest", "pytest", now],
            )
            connection.execute(
                """INSERT INTO batch_execution_attempts
                    (id, work_item_id, workflow_run_id, batch_profile_id, batch_profile_version,
                     profile_snapshot_json, command_preview, idempotency_key, execution_mode,
                     status, progress, last_message, created_by, created_at, started_at, completed_at)
                VALUES (?, ?, NULL, ?, 1, ?, ?, ?, 'DEMO_ONLY', 'QUEUED', 10, ?, ?, ?, ?, NULL)""",
                [target["attempt_id"], target["work_item_id"], target["profile_id"], "{}", "pytest recovery", f"recovery-{suffix}", "runner-pytest", now, now],
            )
            connection.execute(
                """INSERT INTO workflow_runs
                    (id, name, request_id, request_type_id, request_type_version, definition_json,
                     execution_mode, status, progress, created_by, created_at, started_at,
                     completed_at, batch_attempt_id)
                VALUES (?, ?, ?, ?, 1, ?, 'DEMO_ONLY', 'SUCCEEDED', 100, ?, ?, ?, ?, ?)""",
                [target["workflow_run_id"], "Recovery DEMO_ONLY run", target["request_id"], target["request_type_id"], json.dumps({"nodes": [node]}), "runner-pytest", now, now, now, target["attempt_id"]],
            )
            connection.execute(
                "UPDATE batch_execution_attempts SET workflow_run_id=? WHERE id=?",
                [target["workflow_run_id"], target["attempt_id"]],
            )
            connection.execute(
                """INSERT INTO task_runs
                    (id, workflow_run_id, node_key, task_type_id, task_type_version, status,
                     progress, depends_on_json, demo_artifact_url, started_at, completed_at)
                VALUES (?, ?, 'recovery-node', ?, 1, 'SUCCEEDED', 100, ?, ?, ?, ?)""",
                [target["task_run_id"], target["workflow_run_id"], target["task_type_id"], "[]", "demo://recovery", now, now],
            )
            connection.execute(
                """INSERT INTO task_run_events
                    (id, task_run_id, event_index, event_type, level, message, progress, occurred_at)
                VALUES (?, ?, 0, 'SUCCEEDED', 'INFO', ?, 100, ?)""",
                [f"postgres-recovery-task-event-{suffix}", target["task_run_id"], "runner result already exists", now],
            )
        yield target
    finally:
        _cleanup_target(target)


def _cleanup_target(target: dict[str, str | int]) -> None:
    """Remove only this fixture's rows, including both sides of its FK cycle."""
    with connect() as connection:
        connection.execute("DELETE FROM batch_dispatches WHERE attempt_id=?", [target["attempt_id"]])
        connection.execute("DELETE FROM batch_execution_events WHERE attempt_id=?", [target["attempt_id"]])
        connection.execute(
            """UPDATE batch_execution_attempts
            SET recovery_lease_owner_id=NULL, recovery_lease_token=NULL,
                recovery_lease_acquired_at=NULL, recovery_lease_expires_at=NULL,
                workflow_run_id=NULL
            WHERE id=?""",
            [target["attempt_id"]],
        )
        connection.execute("UPDATE workflow_runs SET batch_attempt_id=NULL WHERE id=?", [target["workflow_run_id"]])
        connection.execute("UPDATE request_work_items SET demo_run_id=NULL WHERE id=?", [target["work_item_id"]])
        connection.execute(
            "DELETE FROM task_run_events WHERE task_run_id IN (SELECT id FROM task_runs WHERE workflow_run_id=?)",
            [target["workflow_run_id"]],
        )
        connection.execute("DELETE FROM task_runs WHERE workflow_run_id=?", [target["workflow_run_id"]])
        connection.execute("DELETE FROM workflow_runs WHERE id=?", [target["workflow_run_id"]])
        connection.execute("DELETE FROM batch_execution_attempts WHERE id=?", [target["attempt_id"]])
        connection.execute("DELETE FROM request_work_items WHERE id=?", [target["work_item_id"]])
        connection.execute("DELETE FROM request_work_plans WHERE request_id=?", [target["request_id"]])
        connection.execute("DELETE FROM analysis_requests WHERE id=?", [target["request_id"]])
        connection.execute("DELETE FROM batch_path_profile_versions WHERE id=?", [target["profile_id"]])
        connection.execute("DELETE FROM batch_path_profiles WHERE id=?", [target["profile_id"]])
        connection.execute(
            "DELETE FROM request_type_versions WHERE id=? AND version=1", [target["request_type_id"]]
        )
        connection.execute(
            "DELETE FROM task_type_versions WHERE id=? AND version=1", [target["task_type_id"]]
        )
        connection.execute("DELETE FROM projects WHERE id=?", [target["project_id"]])


def _target_id(target: dict[str, str | int], key: str) -> str:
    value = target[key]
    assert isinstance(value, str)
    return value


def _claim(target: dict[str, str | int], owner_id: str, token: str, expected_generation: int) -> BatchRecoveryLeaseRead:
    with connect() as connection:
        return claim_workbench_batch_recovery_lease(
            SQLWorkbenchBatchRecoveryLease(connection),
            _target_id(target, "attempt_id"),
            BatchRecoveryLeaseClaimCommand(owner_id, token, expected_generation, 60),
        )


def _concurrently(
    operations: list[Callable[[SQLWorkbenchBatchRecoveryLease], T]],
) -> list[T]:
    """Run operations on distinct pooled PostgreSQL sessions without hiding hangs."""
    barrier = Barrier(len(operations))
    outcomes: list[T | None] = [None] * len(operations)
    failures: list[BaseException | None] = [None] * len(operations)
    pids: list[int | None] = [None] * len(operations)

    def worker(index: int, operation: Callable[[SQLWorkbenchBatchRecoveryLease], T]) -> None:
        try:
            with connect() as connection:
                pids[index] = int(connection.execute("SELECT pg_backend_pid()").fetchone()[0])
                barrier.wait(timeout=20)
                outcomes[index] = operation(SQLWorkbenchBatchRecoveryLease(connection))
        except BaseException as exc:  # preserve unexpected thread failures for the parent test
            failures[index] = exc
            try:
                barrier.abort()
            except BrokenBarrierError:
                pass

    threads = [Thread(target=worker, args=(index, operation)) for index, operation in enumerate(operations)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=45)
    assert not any(thread.is_alive() for thread in threads), "PostgreSQL recovery concurrency workers did not finish"
    assert failures == [None] * len(operations), f"unexpected worker failures: {failures!r}"
    assert all(pid is not None for pid in pids), "a worker did not acquire a PostgreSQL session"
    assert len(set(pids)) == len(pids), f"workers did not use independent PostgreSQL sessions: {pids!r}"
    assert all(outcome is not None for outcome in outcomes)
    return [outcome for outcome in outcomes if outcome is not None]


def _attempt_row(target: dict[str, str | int]) -> tuple[Any, ...]:
    with connect() as connection:
        row = connection.execute(
            """SELECT status, progress, completed_at, recovery_lease_owner_id, recovery_lease_token,
                      recovery_lease_generation, recovery_lease_acquired_at, recovery_lease_expires_at
            FROM batch_execution_attempts WHERE id=?""",
            [_target_id(target, "attempt_id")],
        ).fetchone()
    assert row is not None
    return row


def test_postgres_recovery_claim_race_has_one_winner(recovery_target: dict[str, str | int]) -> None:
    attempt_id = _target_id(recovery_target, "attempt_id")

    def claim(
        owner: str,
        token: str,
    ) -> Callable[[SQLWorkbenchBatchRecoveryLease], BatchRecoveryLeaseRead | BatchRecoveryLeaseUnavailableError]:
        def operation(port: SQLWorkbenchBatchRecoveryLease) -> BatchRecoveryLeaseRead | BatchRecoveryLeaseUnavailableError:
            try:
                return claim_workbench_batch_recovery_lease(
                    port, attempt_id, BatchRecoveryLeaseClaimCommand(owner, token, 0, 60)
                )
            except BatchRecoveryLeaseUnavailableError as exc:
                return exc
        return operation

    outcomes = _concurrently([claim("runner-a", "token-a"), claim("runner-b", "token-b")])
    winners = [outcome for outcome in outcomes if isinstance(outcome, BatchRecoveryLeaseRead)]
    unavailable = [outcome for outcome in outcomes if isinstance(outcome, BatchRecoveryLeaseUnavailableError)]
    assert len(winners) == len(unavailable) == 1
    winner = winners[0]
    assert winner.generation == 1
    assert {winner.owner_id, winner.token} in ({"runner-a", "token-a"}, {"runner-b", "token-b"})
    assert winner.acquired_at is not None
    assert winner.expires_at > winner.acquired_at
    row = _attempt_row(recovery_target)
    assert row[0] == "QUEUED"
    assert (row[3], row[4], row[5]) == (winner.owner_id, winner.token, 1)
    assert row[6] is not None and row[7] is not None and row[7] > row[6]
    with connect() as connection:
        assert connection.execute(
            "SELECT recovery_lease_expires_at > (CURRENT_TIMESTAMP AT TIME ZONE 'UTC') FROM batch_execution_attempts WHERE id=?",
            [attempt_id],
        ).fetchone()[0] is True


def test_postgres_same_owner_token_claim_is_idempotent_under_race(
    recovery_target: dict[str, str | int],
) -> None:
    attempt_id = _target_id(recovery_target, "attempt_id")
    command = BatchRecoveryLeaseClaimCommand("runner-idempotent", "token-idempotent", 0, 60)

    def claim(port: SQLWorkbenchBatchRecoveryLease) -> BatchRecoveryLeaseRead:
        return claim_workbench_batch_recovery_lease(port, attempt_id, command)

    leases = _concurrently([claim, claim])
    assert len(leases) == 2
    first, second = leases
    assert first == second
    assert first.attempt_id == attempt_id
    assert first.workflow_run_id == _target_id(recovery_target, "workflow_run_id")
    assert (first.owner_id, first.token, first.generation) == ("runner-idempotent", "token-idempotent", 1)
    assert first.acquired_at is not None
    assert first.expires_at > first.acquired_at
    row = _attempt_row(recovery_target)
    assert row == (
        "QUEUED", 10, None, "runner-idempotent", "token-idempotent", 1,
        first.acquired_at, first.expires_at,
    )


def test_postgres_recovery_finalization_race_is_fenced_and_private(recovery_target: dict[str, str | int]) -> None:
    lease = _claim(recovery_target, "runner-finalize", "token-finalize", 0)
    attempt_id = _target_id(recovery_target, "attempt_id")
    command = BatchRecoveryFinalizeCommand(lease.owner_id, lease.token, lease.generation)

    def finalize(port: SQLWorkbenchBatchRecoveryLease) -> BatchRecoveryFinalizeRead | BatchRecoveryLeaseLostError:
        try:
            return finalize_workbench_batch_recovery_attempt(port, attempt_id, command)
        except BatchRecoveryLeaseLostError as exc:
            return exc

    outcomes = _concurrently([finalize, finalize])
    completed = [outcome for outcome in outcomes if isinstance(outcome, BatchRecoveryFinalizeRead)]
    lost = [outcome for outcome in outcomes if isinstance(outcome, BatchRecoveryLeaseLostError)]
    assert len(completed) == len(lost) == 1
    row = _attempt_row(recovery_target)
    assert row[0] == "SUCCEEDED"
    assert row[1] == 100
    assert row[2] is not None
    assert row[3] is None
    assert row[4] is None
    assert row[5] == lease.generation
    assert row[6] is None
    assert row[7] is None
    with connect() as connection:
        assert connection.execute("SELECT count(*) FROM batch_execution_events WHERE attempt_id=? AND event_index=2", [attempt_id]).fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM batch_dispatches WHERE attempt_id=?", [attempt_id]).fetchone()[0] == 1
        assert connection.execute("SELECT progress FROM request_work_items WHERE id=?", [_target_id(recovery_target, "work_item_id")]).fetchone()[0] == 90
        assert connection.execute("SELECT status FROM analysis_requests WHERE id=?", [_target_id(recovery_target, "request_id")]).fetchone()[0] == "IN_PROGRESS"
        repository = WorkbenchRepository(connection)
        public_attempt = repository.batch_attempt(attempt_id)
        public_run = DemoRunnerService(repository).get_run(_target_id(recovery_target, "workflow_run_id"))
    assert public_attempt is not None
    assert not any(key.startswith("recovery_lease_") for key in public_attempt)
    assert public_run is not None
    assert "batch_attempt_id" not in public_run
    assert not any(key.startswith("recovery_lease_") for key in public_run["batch_attempt"])


def test_postgres_expired_predecessor_cannot_mutate_successor(recovery_target: dict[str, str | int]) -> None:
    predecessor = _claim(recovery_target, "runner-old", "token-old", 0)
    attempt_id = _target_id(recovery_target, "attempt_id")
    with connect() as connection:
        connection.execute(
            """UPDATE batch_execution_attempts
            SET recovery_lease_acquired_at=(CURRENT_TIMESTAMP AT TIME ZONE 'UTC') - INTERVAL '2 seconds',
                recovery_lease_expires_at=(CURRENT_TIMESTAMP AT TIME ZONE 'UTC') - INTERVAL '1 second'
            WHERE id=?""",
            [attempt_id],
        )
    successor = _claim(recovery_target, "runner-new", "token-new", predecessor.generation)
    successor_snapshot = _attempt_row(recovery_target)
    for action in (
        lambda port: renew_workbench_batch_recovery_lease(port, attempt_id, BatchRecoveryLeaseRenewCommand("runner-old", "token-old", predecessor.generation, 60)),
        lambda port: release_workbench_batch_recovery_lease(port, attempt_id, BatchRecoveryLeaseReleaseCommand("runner-old", "token-old", predecessor.generation)),
        lambda port: finalize_workbench_batch_recovery_attempt(port, attempt_id, BatchRecoveryFinalizeCommand("runner-old", "token-old", predecessor.generation)),
    ):
        with connect() as connection:
            with pytest.raises(BatchRecoveryLeaseLostError):
                action(SQLWorkbenchBatchRecoveryLease(connection))
        assert _attempt_row(recovery_target) == successor_snapshot
        with connect() as verification:
            assert verification.execute(
                "SELECT count(*) FROM batch_execution_events WHERE attempt_id=?", [attempt_id]
            ).fetchone()[0] == 0
            assert verification.execute(
                "SELECT count(*) FROM batch_dispatches WHERE attempt_id=?", [attempt_id]
            ).fetchone()[0] == 0
    assert successor_snapshot[3:6] == (successor.owner_id, successor.token, successor.generation)
    with connect() as connection:
        finalized = finalize_workbench_batch_recovery_attempt(
            SQLWorkbenchBatchRecoveryLease(connection),
            attempt_id,
            BatchRecoveryFinalizeCommand(successor.owner_id, successor.token, successor.generation),
        )
    assert isinstance(finalized, BatchRecoveryFinalizeRead)


def test_postgres_finalization_integrity_error_rolls_back_every_effect(recovery_target: dict[str, str | int]) -> None:
    attempt_id = _target_id(recovery_target, "attempt_id")
    now = _utcnow()
    with connect() as connection:
        connection.execute(
            """INSERT INTO batch_execution_events
                (id, attempt_id, event_index, event_type, level, message, progress, occurred_at)
            VALUES (?, ?, 2, 'SENTINEL', 'INFO', ?, 10, ?)""",
            [f"sentinel-{uuid4().hex}", attempt_id, "force event-index conflict", now],
        )
    lease = _claim(recovery_target, "runner-rollback", "token-rollback", 0)
    before = _attempt_row(recovery_target)
    with connect() as connection:
        with pytest.raises(IntegrityError):
            finalize_workbench_batch_recovery_attempt(
                SQLWorkbenchBatchRecoveryLease(connection), attempt_id,
                BatchRecoveryFinalizeCommand(lease.owner_id, lease.token, lease.generation),
            )
    assert _attempt_row(recovery_target) == before
    with connect() as connection:
        assert connection.execute("SELECT count(*) FROM batch_execution_events WHERE attempt_id=? AND event_index=2", [attempt_id]).fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM batch_dispatches WHERE attempt_id=?", [attempt_id]).fetchone()[0] == 0
        assert connection.execute("SELECT progress FROM request_work_items WHERE id=?", [_target_id(recovery_target, "work_item_id")]).fetchone()[0] == 10
        assert connection.execute("SELECT status FROM analysis_requests WHERE id=?", [_target_id(recovery_target, "request_id")]).fetchone()[0] == "READY"
