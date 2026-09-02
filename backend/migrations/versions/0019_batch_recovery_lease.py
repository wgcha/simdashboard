"""Add internal ownership leases for exact batch-recovery crash windows.

Revision ID: 0019_batch_recovery_lease
Revises: 0018_batch_attempt_run_identity

Release gate: run this migration and concurrent claim/takeover verification on
the production-like PostgreSQL topology before enabling any multi-worker
recovery worker. DuckDB is deliberately limited to serialized single-process
development semantics.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0019_batch_recovery_lease"
down_revision = "0018_batch_attempt_run_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, definition in (
        ("recovery_lease_owner_id", "VARCHAR"),
        ("recovery_lease_token", "VARCHAR"),
        ("recovery_lease_generation", "BIGINT DEFAULT 0"),
        ("recovery_lease_acquired_at", "TIMESTAMP"),
        ("recovery_lease_expires_at", "TIMESTAMP"),
    ):
        op.execute(sa.text(f"ALTER TABLE batch_execution_attempts ADD COLUMN IF NOT EXISTS {name} {definition}"))
    op.execute(sa.text("UPDATE batch_execution_attempts SET recovery_lease_generation=0 WHERE recovery_lease_generation IS NULL"))
    op.execute(sa.text("ALTER TABLE batch_execution_attempts ALTER COLUMN recovery_lease_generation SET DEFAULT 0"))
    op.execute(sa.text("ALTER TABLE batch_execution_attempts ALTER COLUMN recovery_lease_generation SET NOT NULL"))
    op.execute(
        sa.text(
            """DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM batch_execution_attempts
                    WHERE (recovery_lease_owner_id IS NULL) <> (recovery_lease_token IS NULL)
                       OR (recovery_lease_owner_id IS NULL) <> (recovery_lease_acquired_at IS NULL)
                       OR (recovery_lease_owner_id IS NULL) <> (recovery_lease_expires_at IS NULL)
                       OR recovery_lease_generation < 0
                       OR (recovery_lease_owner_id IS NOT NULL
                           AND (
                               recovery_lease_expires_at <= recovery_lease_acquired_at
                               OR recovery_lease_generation <= 0
                               OR status <> 'QUEUED'
                               OR workflow_run_id IS NULL
                               OR completed_at IS NOT NULL
                               OR NOT EXISTS (
                                   SELECT 1
                                   FROM workflow_runs AS run
                                   JOIN request_work_items AS item ON item.id=batch_execution_attempts.work_item_id
                                   WHERE run.id=batch_execution_attempts.workflow_run_id
                                     AND run.batch_attempt_id=batch_execution_attempts.id
                                     AND run.request_id=item.request_id
                                     AND run.execution_mode='DEMO_ONLY'
                                     AND run.status='SUCCEEDED'
                                     AND run.created_by=batch_execution_attempts.created_by
                                     AND jsonb_array_length(COALESCE(run.definition_json->'nodes', '[]'::jsonb))=1
                                     AND run.definition_json->'nodes'->0->>'node_key'=item.node_key
                                     AND run.definition_json->'nodes'->0->>'task_type_id'=item.task_type_id
                                     AND (run.definition_json->'nodes'->0->>'task_type_version')::integer=item.task_type_version
                                     AND jsonb_array_length(COALESCE(run.definition_json->'nodes'->0->'depends_on', '[]'::jsonb))=0
                               )
                               OR EXISTS (SELECT 1 FROM batch_dispatches AS dispatch
                                          WHERE dispatch.workflow_run_id=batch_execution_attempts.workflow_run_id)
                           ))
                ) THEN
                    RAISE EXCEPTION 'batch recovery lease identity is malformed; remediate before lease migration';
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname='ck_batch_attempts_recovery_lease_state'
                      AND conrelid='batch_execution_attempts'::regclass
                ) THEN
                    ALTER TABLE batch_execution_attempts
                    ADD CONSTRAINT ck_batch_attempts_recovery_lease_state
                    CHECK (recovery_lease_token IS NULL
                           OR (recovery_lease_generation > 0 AND status='QUEUED'
                               AND workflow_run_id IS NOT NULL AND completed_at IS NULL));
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname='ck_batch_attempts_recovery_lease_generation'
                      AND conrelid='batch_execution_attempts'::regclass
                ) THEN
                    ALTER TABLE batch_execution_attempts
                    ADD CONSTRAINT ck_batch_attempts_recovery_lease_generation
                    CHECK (recovery_lease_generation >= 0);
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname='ck_batch_attempts_recovery_lease_identity'
                      AND conrelid='batch_execution_attempts'::regclass
                ) THEN
                    ALTER TABLE batch_execution_attempts
                    ADD CONSTRAINT ck_batch_attempts_recovery_lease_identity
                    CHECK (
                        (recovery_lease_owner_id IS NULL AND recovery_lease_token IS NULL
                         AND recovery_lease_acquired_at IS NULL AND recovery_lease_expires_at IS NULL)
                        OR
                        (recovery_lease_owner_id IS NOT NULL AND recovery_lease_token IS NOT NULL
                         AND recovery_lease_acquired_at IS NOT NULL AND recovery_lease_expires_at IS NOT NULL
                         AND recovery_lease_expires_at > recovery_lease_acquired_at)
                    );
                END IF;
            END $$"""
        )
    )
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_batch_attempts_recovery_candidates ON batch_execution_attempts(status, recovery_lease_expires_at, id)"))
    op.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS ux_batch_attempts_recovery_lease_token ON batch_execution_attempts(recovery_lease_token)"))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ux_batch_attempts_recovery_lease_token"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_batch_attempts_recovery_candidates"))
    op.execute(sa.text("ALTER TABLE batch_execution_attempts DROP CONSTRAINT IF EXISTS ck_batch_attempts_recovery_lease_identity"))
    op.execute(sa.text("ALTER TABLE batch_execution_attempts DROP CONSTRAINT IF EXISTS ck_batch_attempts_recovery_lease_generation"))
    op.execute(sa.text("ALTER TABLE batch_execution_attempts DROP CONSTRAINT IF EXISTS ck_batch_attempts_recovery_lease_state"))
    for name in (
        "recovery_lease_expires_at",
        "recovery_lease_acquired_at",
        "recovery_lease_generation",
        "recovery_lease_token",
        "recovery_lease_owner_id",
    ):
        op.execute(sa.text(f"ALTER TABLE batch_execution_attempts DROP COLUMN IF EXISTS {name}"))
