"""SQL adapter for internal batch-recovery leases and fenced finalization."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from ...database_connection import ConnectionLike
from ...domains.workbench.models import (
    BatchRecoveryLeaseClaimCommand,
    BatchRecoveryFinalizeCommand,
    BatchRecoveryFinalizeRead,
    BatchRecoveryLeaseRead,
    BatchRecoveryLeaseReleaseCommand,
    BatchRecoveryLeaseRenewCommand,
)
from ...services.request_monitoring import sync_request_status

_LEASE_COLUMNS = (
    "id", "workflow_run_id", "recovery_lease_owner_id", "recovery_lease_token",
    "recovery_lease_generation", "recovery_lease_acquired_at",
    "recovery_lease_expires_at",
)

_FINALIZE_COLUMNS = (
    "id", "workflow_run_id", "work_item_id", "batch_profile_id",
    "profile_snapshot_json", "command_preview", "created_by", "completed_at",
)


def _lease_read(row: Any) -> BatchRecoveryLeaseRead:
    item = dict(zip(_LEASE_COLUMNS, row))
    return BatchRecoveryLeaseRead(
        attempt_id=str(item["id"]), workflow_run_id=str(item["workflow_run_id"]), owner_id=str(item["recovery_lease_owner_id"]),
        token=str(item["recovery_lease_token"]), generation=int(item["recovery_lease_generation"]),
        acquired_at=item["recovery_lease_acquired_at"], expires_at=item["recovery_lease_expires_at"],
    )


def _finalize_read(row: Any, request_id: str, dispatch_id: str) -> BatchRecoveryFinalizeRead:
    item = dict(zip(_FINALIZE_COLUMNS, row))
    return BatchRecoveryFinalizeRead(
        attempt_id=str(item["id"]),
        workflow_run_id=str(item["workflow_run_id"]),
        work_item_id=str(item["work_item_id"]),
        request_id=request_id,
        dispatch_id=dispatch_id,
        completed_at=item["completed_at"],
    )


class SQLWorkbenchBatchRecoveryLease:
    """One-connection, internal-only lease and fenced-finalization adapter."""

    def __init__(self, connection: ConnectionLike) -> None:
        self._connection = connection

    def begin_transaction(self) -> None:
        self._connection.execute("BEGIN TRANSACTION")

    def commit_transaction(self) -> None:
        self._connection.execute("COMMIT")

    def rollback_transaction(self) -> None:
        self._connection.execute("ROLLBACK")

    def _exact_run_identity_predicate(self) -> str:
        if self._connection.backend == "postgresql":
            return """
                jsonb_array_length(COALESCE(run.definition_json->'nodes', '[]'::jsonb))=1
                AND run.definition_json->'nodes'->0->>'node_key'=item.node_key
                AND run.definition_json->'nodes'->0->>'task_type_id'=item.task_type_id
                AND (run.definition_json->'nodes'->0->>'task_type_version')::integer=item.task_type_version
                AND jsonb_array_length(COALESCE(run.definition_json->'nodes'->0->'depends_on', '[]'::jsonb))=0
            """
        return """
            json_array_length(json_extract(run.definition_json, '$.nodes'))=1
            AND json_extract_string(run.definition_json, '$.nodes[0].node_key')=item.node_key
            AND json_extract_string(run.definition_json, '$.nodes[0].task_type_id')=item.task_type_id
            AND CAST(json_extract_string(run.definition_json, '$.nodes[0].task_type_version') AS INTEGER)=item.task_type_version
            AND json_array_length(json_extract(run.definition_json, '$.nodes[0].depends_on'))=0
        """

    def _eligible_identity(self) -> str:
        return f"""
            attempt.status='QUEUED'
            AND attempt.completed_at IS NULL
            AND attempt.workflow_run_id IS NOT NULL
            AND EXISTS (
                SELECT 1 FROM workflow_runs AS run
                JOIN request_work_items AS item ON item.id=attempt.work_item_id
                WHERE run.id=attempt.workflow_run_id
                  AND run.batch_attempt_id=attempt.id
                  AND run.request_id=item.request_id
                  AND run.execution_mode='DEMO_ONLY'
                  AND run.status='SUCCEEDED'
                  AND run.created_by=attempt.created_by
                  AND {self._exact_run_identity_predicate()}
            )
            AND NOT EXISTS (
                SELECT 1 FROM batch_dispatches AS dispatch
                WHERE dispatch.workflow_run_id=attempt.workflow_run_id
            )
        """

    def claim_batch_recovery_lease(self, attempt_id: str, command: BatchRecoveryLeaseClaimCommand) -> BatchRecoveryLeaseRead | None:
        clock = "(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')"
        active_same = "attempt.recovery_lease_owner_id=? AND attempt.recovery_lease_token=? AND attempt.recovery_lease_expires_at > " + clock
        row = self._connection.execute(
            f"""UPDATE batch_execution_attempts AS attempt
            SET recovery_lease_owner_id=CASE WHEN {active_same} THEN attempt.recovery_lease_owner_id ELSE ? END,
                recovery_lease_token=CASE WHEN {active_same} THEN attempt.recovery_lease_token ELSE ? END,
                recovery_lease_generation=CASE WHEN {active_same} THEN attempt.recovery_lease_generation ELSE attempt.recovery_lease_generation + 1 END,
                recovery_lease_acquired_at=CASE WHEN {active_same} THEN attempt.recovery_lease_acquired_at ELSE {clock} END,
                recovery_lease_expires_at=CASE WHEN {active_same} THEN attempt.recovery_lease_expires_at ELSE {clock} + (? * INTERVAL '1 second') END
            WHERE attempt.id=? AND {self._eligible_identity()}
              AND (({active_same})
                   OR (attempt.recovery_lease_owner_id IS NULL AND attempt.recovery_lease_generation=?)
                   OR (attempt.recovery_lease_expires_at <= {clock}
                       AND attempt.recovery_lease_token IS DISTINCT FROM ?
                       AND attempt.recovery_lease_generation=?))
            RETURNING {', '.join(_LEASE_COLUMNS)}""",
            [
                command.owner_id, command.token, command.owner_id,
                command.owner_id, command.token, command.token,
                command.owner_id, command.token,
                command.owner_id, command.token,
                command.owner_id, command.token, command.ttl_seconds,
                attempt_id, command.owner_id, command.token, command.expected_generation,
                command.token, command.expected_generation,
            ],
        ).fetchone()
        return _lease_read(row) if row else None

    def renew_batch_recovery_lease(self, attempt_id: str, command: BatchRecoveryLeaseRenewCommand) -> BatchRecoveryLeaseRead | None:
        clock = "(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')"
        row = self._connection.execute(
            f"""UPDATE batch_execution_attempts AS attempt
            SET recovery_lease_expires_at={clock} + (? * INTERVAL '1 second')
            WHERE attempt.id=? AND {self._eligible_identity()}
              AND attempt.recovery_lease_owner_id=? AND attempt.recovery_lease_token=?
              AND attempt.recovery_lease_generation=? AND attempt.recovery_lease_expires_at > {clock}
            RETURNING {', '.join(_LEASE_COLUMNS)}""",
            [command.ttl_seconds, attempt_id, command.owner_id, command.token, command.generation],
        ).fetchone()
        return _lease_read(row) if row else None

    def release_batch_recovery_lease(self, attempt_id: str, command: BatchRecoveryLeaseReleaseCommand) -> bool:
        row = self._connection.execute(
            """UPDATE batch_execution_attempts
            SET recovery_lease_owner_id=NULL, recovery_lease_token=NULL,
                recovery_lease_acquired_at=NULL, recovery_lease_expires_at=NULL
            WHERE id=? AND recovery_lease_owner_id=? AND recovery_lease_token=?
              AND recovery_lease_generation=? RETURNING id""",
            [attempt_id, command.owner_id, command.token, command.generation],
        ).fetchone()
        return row is not None

    def finalize_batch_recovery_attempt(
        self,
        attempt_id: str,
        command: BatchRecoveryFinalizeCommand,
    ) -> BatchRecoveryFinalizeRead | None:
        """CAS the active lease, then write all recovery effects in this UoW.

        The first statement is deliberately the sole ownership and crash-window
        decision point.  Any later error is allowed to escape so the application
        layer rolls the entire transaction back, including this status change.
        """
        clock = "(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')"
        row = self._connection.execute(
            f"""UPDATE batch_execution_attempts AS attempt
            SET status='SUCCEEDED', progress=100,
                last_message='DEMO_ONLY 배치 실행 기록이 복구 완료되었습니다.',
                completed_at={clock},
                recovery_lease_owner_id=NULL, recovery_lease_token=NULL,
                recovery_lease_acquired_at=NULL, recovery_lease_expires_at=NULL
            WHERE attempt.id=? AND {self._eligible_identity()}
              AND attempt.recovery_lease_owner_id=? AND attempt.recovery_lease_token=?
              AND attempt.recovery_lease_generation=?
              AND attempt.recovery_lease_expires_at > {clock}
            RETURNING {', '.join(_FINALIZE_COLUMNS)}""",
            [
                attempt_id, command.owner_id, command.token, command.generation,
            ],
        ).fetchone()
        if row is None:
            return None

        result_row = dict(zip(_FINALIZE_COLUMNS, row))
        request_row = self._connection.execute(
            "SELECT request_id FROM request_work_items WHERE id=?",
            [result_row["work_item_id"]],
        ).fetchone()
        if request_row is None:
            raise RuntimeError("recovery finalization lost its work-item request identity")
        request_id = str(request_row[0])
        occurred_at = result_row["completed_at"]
        profile_snapshot_json = result_row["profile_snapshot_json"]
        if not isinstance(profile_snapshot_json, str):
            profile_snapshot_json = json.dumps(profile_snapshot_json, ensure_ascii=False)
        dispatch_id = f"dispatch-recovery-{result_row['id']}"
        self._connection.execute(
            """INSERT INTO batch_execution_events
                (id, attempt_id, event_index, event_type, level, message, progress, occurred_at)
            VALUES (?, ?, 2, 'SUCCEEDED', 'INFO', ?, 100, ?)""",
            [
                f"batch-recovery-event-{uuid4().hex[:12]}",
                result_row["id"],
                "검증된 DEMO_ONLY runner 결과를 recovery lease로 기록했습니다.",
                occurred_at,
            ],
        )
        self._connection.execute(
            """INSERT INTO batch_dispatches
                (id, work_item_id, workflow_run_id, batch_profile_id, profile_snapshot_json,
                 attempt_id, command_preview, status, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'RECORDED_DEMO', ?, ?)""",
            [
                dispatch_id,
                result_row["work_item_id"], result_row["workflow_run_id"],
                result_row["batch_profile_id"], profile_snapshot_json,
                result_row["id"], result_row["command_preview"],
                result_row["created_by"], occurred_at,
            ],
        )
        progressed = self._connection.execute(
            """UPDATE request_work_items
            SET progress=CASE WHEN progress < 90 THEN 90 ELSE progress END,
                progress_updated_by=?, progress_updated_at=?
            WHERE id=? AND status='IN_PROGRESS'
            RETURNING id""",
            [result_row["created_by"], occurred_at, result_row["work_item_id"]],
        ).fetchone()
        if progressed is None:
            raise RuntimeError("recovery finalization could not advance its in-progress work item")
        sync_request_status(self._connection, request_id)
        return _finalize_read(row, request_id, dispatch_id)
