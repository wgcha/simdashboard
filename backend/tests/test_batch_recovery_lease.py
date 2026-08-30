from datetime import datetime
from pathlib import Path

import duckdb
import pytest

from app.application.workbench.batch_recovery import (
    claim_workbench_batch_recovery_lease,
    release_workbench_batch_recovery_lease,
    renew_workbench_batch_recovery_lease,
)
from app.adapters.persistence import batch_recovery as batch_recovery_adapter
from app.database import connect, initialize_database
from app.repositories.workbench import WorkbenchRepository
from app.schemas.workbench import DemoRunCreate
from app.domains.workbench.models import (
    BatchRecoveryLeaseClaimCommand,
    BatchRecoveryLeaseCommandInvalidError,
    BatchRecoveryLeaseLostError,
    BatchRecoveryLeaseRead,
    BatchRecoveryLeaseReleaseCommand,
    BatchRecoveryLeaseRenewCommand,
    BatchRecoveryLeaseUnavailableError,
)


def _lease(*, generation: int = 1) -> BatchRecoveryLeaseRead:
    return BatchRecoveryLeaseRead(
        attempt_id="attempt-1",
        workflow_run_id="run-1",
        owner_id="runner-a",
        token="opaque-token",
        generation=generation,
        acquired_at=datetime(2026, 8, 30, 12, 0),
        expires_at=datetime(2026, 8, 30, 12, 30),
    )


class _Port:
    def __init__(self, *, claim=None, renew=None, release=True):
        self.claim_result = claim
        self.renew_result = renew
        self.release_result = release
        self.calls: list[tuple] = []

    def begin_transaction(self):
        self.calls.append(("begin",))

    def commit_transaction(self):
        self.calls.append(("commit",))

    def rollback_transaction(self):
        self.calls.append(("rollback",))

    def claim_batch_recovery_lease(self, attempt_id, command):
        self.calls.append(("claim", attempt_id, command))
        return self.claim_result

    def renew_batch_recovery_lease(self, attempt_id, command):
        self.calls.append(("renew", attempt_id, command))
        return self.renew_result

    def release_batch_recovery_lease(self, attempt_id, command):
        self.calls.append(("release", attempt_id, command))
        return self.release_result


@pytest.mark.unit
def test_claim_validates_before_touching_port_and_returns_lease() -> None:
    port = _Port(claim=_lease())
    command = BatchRecoveryLeaseClaimCommand("runner-a", "opaque-token", 0, 30)

    assert claim_workbench_batch_recovery_lease(port, "attempt-1", command) == _lease()
    assert port.calls == [("begin",), ("claim", "attempt-1", command), ("commit",)]

    for invalid in (
        BatchRecoveryLeaseClaimCommand(" runner-a", "opaque-token", 0, 30),
        BatchRecoveryLeaseClaimCommand("runner-a", " opaque-token", 0, 30),
        BatchRecoveryLeaseClaimCommand("runner-a", "opaque-token", 0, 0),
        BatchRecoveryLeaseClaimCommand("runner-a", "opaque-token", 0, 86_401),
    ):
        with pytest.raises(BatchRecoveryLeaseCommandInvalidError):
            claim_workbench_batch_recovery_lease(port, "attempt-1", invalid)
    assert len(port.calls) == 3


@pytest.mark.unit
def test_claim_and_renew_fail_closed_when_port_cannot_own_attempt() -> None:
    port = _Port(claim=None, renew=None)
    with pytest.raises(BatchRecoveryLeaseUnavailableError):
        claim_workbench_batch_recovery_lease(
            port, "attempt-1", BatchRecoveryLeaseClaimCommand("runner-a", "token", 0, 30)
        )
    with pytest.raises(BatchRecoveryLeaseLostError):
        renew_workbench_batch_recovery_lease(
            port, "attempt-1", BatchRecoveryLeaseRenewCommand("runner-a", "token", 1, 30)
        )


@pytest.mark.unit
def test_unavailable_claim_rolls_back_without_committing() -> None:
    class _OrderedPort(_Port):
        def begin_transaction(self): self.calls.append(("begin",))
        def commit_transaction(self): self.calls.append(("commit",))
        def rollback_transaction(self): self.calls.append(("rollback",))

    port = _OrderedPort(claim=None)
    with pytest.raises(BatchRecoveryLeaseUnavailableError):
        claim_workbench_batch_recovery_lease(
            port, "attempt-1", BatchRecoveryLeaseClaimCommand("runner-a", "token", 0, 30)
        )
    assert [call[0] for call in port.calls] == ["begin", "claim", "rollback"]


@pytest.mark.unit
def test_renew_preserves_generation_and_release_propagates_exact_cas_command() -> None:
    port = _Port(renew=_lease(generation=4), release=True)
    renew = BatchRecoveryLeaseRenewCommand("runner-a", "opaque-token", 4, 60)
    release = BatchRecoveryLeaseReleaseCommand("runner-a", "opaque-token", 4)

    assert renew_workbench_batch_recovery_lease(port, "attempt-1", renew) == _lease(generation=4)
    assert release_workbench_batch_recovery_lease(port, "attempt-1", release) is None
    assert port.calls == [("begin",), ("renew", "attempt-1", renew), ("commit",), ("begin",), ("release", "attempt-1", release), ("commit",)]

    losing_port = _Port(release=False)
    with pytest.raises(BatchRecoveryLeaseLostError):
        release_workbench_batch_recovery_lease(
            losing_port, "attempt-1", BatchRecoveryLeaseReleaseCommand("runner-a", "opaque-token", 4)
        )


@pytest.mark.unit
def test_lease_operation_raw_error_rolls_back_once_without_translation() -> None:
    error = ValueError("driver failure")

    class DriverExplodes(_Port):
        def claim_batch_recovery_lease(self, attempt_id, command):
            self.calls.append(("claim", attempt_id, command))
            raise error

    port = DriverExplodes()
    with pytest.raises(ValueError) as raised:
        claim_workbench_batch_recovery_lease(port, "attempt-1", BatchRecoveryLeaseClaimCommand("runner-a", "token", 0, 30))
    assert raised.value is error
    assert port.calls[-1] == ("rollback",)

    class ExplodingPort(_Port):
        def claim_batch_recovery_lease(self, attempt_id, command):
            self.calls.append(("claim", attempt_id, command))
            raise ValueError("driver failure")

        def rollback_transaction(self):
            self.calls.append(("rollback",))
            raise RuntimeError("rollback failure")

    port = ExplodingPort()
    with pytest.raises(RuntimeError, match="rollback failure"):
        claim_workbench_batch_recovery_lease(port, "attempt-1", BatchRecoveryLeaseClaimCommand("runner-a", "token", 0, 30))
    assert port.calls.count(("rollback",)) == 1


@pytest.mark.unit
def test_sql_adapter_maps_internal_columns_to_lease_read() -> None:
    row = (
        "attempt-1", "run-1", "runner-a", "token", 2,
        datetime(2026, 8, 30, 12, 0), datetime(2026, 8, 30, 12, 30),
    )
    assert batch_recovery_adapter._lease_read(row) == BatchRecoveryLeaseRead(
        attempt_id="attempt-1", workflow_run_id="run-1", owner_id="runner-a", token="token", generation=2,
        acquired_at=datetime(2026, 8, 30, 12, 0), expires_at=datetime(2026, 8, 30, 12, 30),
    )


@pytest.mark.duckdb_integration
def test_duckdb_recovery_lease_bootstrap_is_repeatable() -> None:
    initialize_database()
    initialize_database()
    with connect() as conn:
        columns = {
            row[0]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name='batch_execution_attempts'"
            ).fetchall()
        }
        assert {
            "recovery_lease_owner_id", "recovery_lease_token", "recovery_lease_generation",
            "recovery_lease_acquired_at", "recovery_lease_expires_at",
        } <= columns
        assert conn.execute(
            "SELECT count(*) FROM duckdb_indexes() WHERE index_name='ix_batch_attempts_recovery_candidates'"
        ).fetchone() == (1,)
        assert conn.execute(
            "SELECT count(*) FROM duckdb_indexes() WHERE index_name='ux_batch_attempts_recovery_lease_token'"
        ).fetchone() == (1,)


@pytest.mark.unit
def test_batch_recovery_migration_is_idempotent_by_contract() -> None:
    migration = Path(__file__).parents[1] / "migrations" / "versions" / "0019_batch_recovery_lease.py"
    source = migration.read_text(encoding="utf-8")
    assert 'revision = "0019_batch_recovery_lease"' in source
    assert 'down_revision = "0018_batch_attempt_run_identity"' in source
    assert "ADD COLUMN IF NOT EXISTS" in source
    assert "CREATE INDEX IF NOT EXISTS ix_batch_attempts_recovery_candidates" in source
    assert "CREATE UNIQUE INDEX IF NOT EXISTS ux_batch_attempts_recovery_lease_token" in source
    for constraint in (
        "ck_batch_attempts_recovery_lease_generation",
        "ck_batch_attempts_recovery_lease_state",
        "ck_batch_attempts_recovery_lease_identity",
    ):
        assert constraint in source
    downgrade = source[source.index("def downgrade") :]
    assert downgrade.index("DROP CONSTRAINT") < downgrade.index("DROP COLUMN")


def _recoverable_attempt() -> str:
    """Create the persisted runner crash-window identity used by SQL cases."""
    with connect() as conn:
        repository = WorkbenchRepository(conn)
        item = next(item for item in repository.work_items("request-drop-001") if item["status"] == "IN_PROGRESS")
        profile = repository.get_batch_profile_for_task(item["task_type_id"], int(item["task_type_version"]))
        assert profile
        attempt_id = "lease-test-attempt"
        now = datetime(2026, 8, 30, 12, 0)
        repository.insert_batch_attempt({
            "id": attempt_id, "work_item_id": item["id"], "workflow_run_id": None,
            "batch_profile_id": profile["id"], "batch_profile_version": int(profile["version"]),
            "profile_snapshot_json": "{}", "command_preview": "demo", "idempotency_key": "lease-test-key",
            "execution_mode": "DEMO_ONLY", "status": "QUEUED", "progress": 10,
            "last_message": "queued", "created_by": "runner-a", "created_at": now,
            "started_at": now, "completed_at": None,
        })
        payload = DemoRunCreate(
            name="lease test run", request_id=item["request_id"], execution_mode="DEMO_ONLY",
            nodes=[{"node_key": item["node_key"], "task_type_id": item["task_type_id"],
                    "task_type_version": int(item["task_type_version"]), "depends_on": []}],
            created_by="runner-a",
        )
        from app.services.demo_runner import DemoRunnerService
        DemoRunnerService(repository).create_run(payload, run_id="demo-lease-test", batch_attempt_id=attempt_id)
        return attempt_id


@pytest.mark.duckdb_integration
def test_sql_recovery_lease_claim_is_idempotent_and_expired_token_cannot_reclaim() -> None:
    attempt_id = _recoverable_attempt()
    with connect() as conn:
        adapter = batch_recovery_adapter.SQLWorkbenchBatchRecoveryLease(conn)
        first = claim_workbench_batch_recovery_lease(
            adapter, attempt_id, BatchRecoveryLeaseClaimCommand("runner-a", "token-a", 0, 30)
        )
        repeated = claim_workbench_batch_recovery_lease(
            adapter, attempt_id, BatchRecoveryLeaseClaimCommand("runner-a", "token-a", 0, 30)
        )
        assert repeated == first
        repository = WorkbenchRepository(conn)
        hidden = {"recovery_lease_owner_id", "recovery_lease_token", "recovery_lease_generation", "recovery_lease_acquired_at", "recovery_lease_expires_at"}
        assert hidden.isdisjoint(repository.batch_attempt(attempt_id))
        assert hidden.isdisjoint(repository.list_batch_attempts(repository.batch_attempt(attempt_id)["work_item_id"])[0])
        from app.services.demo_runner import DemoRunnerService
        assert hidden.isdisjoint(DemoRunnerService(repository).get_run(first.workflow_run_id))
        with pytest.raises(duckdb.ConstraintException):
            conn.execute("UPDATE batch_execution_attempts SET status='SUCCEEDED' WHERE id=?", [attempt_id])
        assert conn.execute(
            "SELECT status, recovery_lease_owner_id FROM batch_execution_attempts WHERE id=?", [attempt_id]
        ).fetchone() == ("QUEUED", "runner-a")
        with pytest.raises(BatchRecoveryLeaseLostError):
            renew_workbench_batch_recovery_lease(
                adapter, attempt_id, BatchRecoveryLeaseRenewCommand("runner-a", "token-a", first.generation + 1, 30)
            )
        with pytest.raises(BatchRecoveryLeaseUnavailableError):
            claim_workbench_batch_recovery_lease(
                adapter, attempt_id, BatchRecoveryLeaseClaimCommand("runner-b", "token-b", 1, 30)
            )

        conn.execute(
            """UPDATE batch_execution_attempts
            SET recovery_lease_acquired_at=(CURRENT_TIMESTAMP AT TIME ZONE 'UTC') - INTERVAL '2 seconds',
                recovery_lease_expires_at=(CURRENT_TIMESTAMP AT TIME ZONE 'UTC') - INTERVAL '1 second'
            WHERE id=?""",
            [attempt_id],
        )
        assert conn.execute(
            "SELECT recovery_lease_expires_at <= (CURRENT_TIMESTAMP AT TIME ZONE 'UTC') FROM batch_execution_attempts WHERE id=?",
            [attempt_id],
        ).fetchone() == (True,)
        expired_identity = conn.execute(
            """SELECT recovery_lease_owner_id, recovery_lease_token, recovery_lease_generation,
                      recovery_lease_acquired_at, recovery_lease_expires_at
               FROM batch_execution_attempts WHERE id=?""",
            [attempt_id],
        ).fetchone()
        with pytest.raises(BatchRecoveryLeaseUnavailableError):
            claim_workbench_batch_recovery_lease(
                adapter, attempt_id, BatchRecoveryLeaseClaimCommand("runner-a", "token-a", 1, 30)
            )
        with pytest.raises(BatchRecoveryLeaseUnavailableError):
            claim_workbench_batch_recovery_lease(
                adapter, attempt_id, BatchRecoveryLeaseClaimCommand("runner-other", "token-a", 1, 30)
            )
        assert conn.execute(
            """SELECT recovery_lease_owner_id, recovery_lease_token, recovery_lease_generation,
                      recovery_lease_acquired_at, recovery_lease_expires_at
               FROM batch_execution_attempts WHERE id=?""",
            [attempt_id],
        ).fetchone() == expired_identity
        successor = claim_workbench_batch_recovery_lease(
            adapter, attempt_id, BatchRecoveryLeaseClaimCommand("runner-b", "token-b", 1, 30)
        )
        assert successor.generation == first.generation + 1
        repository = WorkbenchRepository(conn)
        public_attempt = repository.batch_attempt(attempt_id)
        assert public_attempt is not None
        assert not any(key.startswith("recovery_lease_") for key in public_attempt)
        from app.services.demo_runner import DemoRunnerService
        public_run = DemoRunnerService(repository).get_run("demo-lease-test")
        assert public_run and public_run["batch_attempt"]
        assert not any(key.startswith("recovery_lease_") for key in public_run["batch_attempt"])
        with pytest.raises(BatchRecoveryLeaseLostError):
            release_workbench_batch_recovery_lease(
                adapter, attempt_id,
                BatchRecoveryLeaseReleaseCommand("runner-a", "token-a", first.generation),
            )
        release_workbench_batch_recovery_lease(
            adapter, attempt_id,
            BatchRecoveryLeaseReleaseCommand("runner-b", "token-b", successor.generation),
        )
        assert conn.execute(
            "SELECT recovery_lease_owner_id, recovery_lease_token FROM batch_execution_attempts WHERE id=?",
            [attempt_id],
        ).fetchone() == (None, None)


@pytest.mark.duckdb_integration
def test_sql_recovery_lease_rejects_unlinked_and_terminal_candidates_without_mutation() -> None:
    attempt_id = _recoverable_attempt()
    with connect() as conn:
        adapter = batch_recovery_adapter.SQLWorkbenchBatchRecoveryLease(conn)
        original = conn.execute(
            """SELECT workflow_run_id, status, recovery_lease_owner_id, recovery_lease_token,
                      recovery_lease_generation, recovery_lease_acquired_at, recovery_lease_expires_at
               FROM batch_execution_attempts WHERE id=?""",
            [attempt_id],
        ).fetchone()
        assert original and original[4] == 0

        # A QUEUED row without the reverse run identity is not recoverable.
        conn.execute("UPDATE batch_execution_attempts SET workflow_run_id=NULL WHERE id=?", [attempt_id])
        with pytest.raises(BatchRecoveryLeaseUnavailableError):
            claim_workbench_batch_recovery_lease(
                adapter, attempt_id, BatchRecoveryLeaseClaimCommand("runner-a", "token-a", 0, 30)
            )
        assert conn.execute(
            "SELECT recovery_lease_owner_id, recovery_lease_token, recovery_lease_generation, recovery_lease_acquired_at, recovery_lease_expires_at FROM batch_execution_attempts WHERE id=?",
            [attempt_id],
        ).fetchone() == (None, None, 0, None, None)

        # A terminal attempt remains unrecoverable even with a bidirectional run identity.
        conn.execute(
            "UPDATE batch_execution_attempts SET workflow_run_id=?, status='SUCCEEDED' WHERE id=?",
            [original[0], attempt_id],
        )
        with pytest.raises(BatchRecoveryLeaseUnavailableError):
            claim_workbench_batch_recovery_lease(
                adapter, attempt_id, BatchRecoveryLeaseClaimCommand("runner-a", "token-a", 0, 30)
            )
        assert conn.execute(
            "SELECT recovery_lease_owner_id, recovery_lease_token, recovery_lease_generation, recovery_lease_acquired_at, recovery_lease_expires_at FROM batch_execution_attempts WHERE id=?",
            [attempt_id],
        ).fetchone() == (None, None, 0, None, None)
