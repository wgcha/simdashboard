"""Add durable batch-attempt identity to DEMO_ONLY workflow runs.

Revision ID: 0018_batch_attempt_run_identity
Revises: 0017_run_identity_v2
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0018_batch_attempt_run_identity"
down_revision = "0017_run_identity_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A blank installation is constructed from migrations/schema.sql before
    # Alembic runs.  Keep these additions idempotent for both that route and
    # historical database upgrades.
    op.execute(sa.text("ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS batch_attempt_id VARCHAR"))
    op.execute(sa.text("ALTER TABLE batch_dispatches ADD COLUMN IF NOT EXISTS attempt_id VARCHAR"))

    # 0018 introduces one non-terminal linked state: a runner transaction may
    # commit its exact run identity while the attempt remains QUEUED.  Every
    # other non-SUCCEEDED link is malformed; queued legacy links without the
    # reverse identity are never guessed into validity.
    op.execute(
        sa.text(
            """DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM batch_execution_attempts
                    WHERE workflow_run_id IS NOT NULL
                      AND status NOT IN ('QUEUED', 'SUCCEEDED')
                ) THEN
                    RAISE EXCEPTION 'terminal or preflight batch attempt has workflow-run link; remediate before batch attempt identity migration';
                END IF;
                IF EXISTS (
                    SELECT 1
                    FROM batch_execution_attempts
                    WHERE workflow_run_id IS NOT NULL
                      AND status = 'SUCCEEDED'
                    GROUP BY workflow_run_id
                    HAVING count(*) > 1
                ) THEN
                    RAISE EXCEPTION 'ambiguous succeeded batch attempt workflow-run mapping; remediate before batch attempt identity migration';
                END IF;
                IF EXISTS (
                    SELECT 1
                    FROM workflow_runs AS run
                    LEFT JOIN batch_execution_attempts AS attempt ON attempt.id = run.batch_attempt_id
                    LEFT JOIN request_work_items AS item ON item.id = attempt.work_item_id
                    WHERE run.batch_attempt_id IS NOT NULL
                      AND (
                          attempt.id IS NULL
                          OR attempt.workflow_run_id IS DISTINCT FROM run.id
                          OR item.id IS NULL
                          OR item.request_id IS DISTINCT FROM run.request_id
                      )
                ) THEN
                    RAISE EXCEPTION 'workflow run batch attempt identity lacks an exact attempt/run/request relationship; remediate before batch attempt identity migration';
                END IF;
                IF EXISTS (
                    SELECT 1
                    FROM batch_execution_attempts AS attempt
                    LEFT JOIN workflow_runs AS run ON run.id = attempt.workflow_run_id
                    LEFT JOIN request_work_items AS item ON item.id = attempt.work_item_id
                    WHERE attempt.status = 'QUEUED'
                      AND attempt.workflow_run_id IS NOT NULL
                      AND (
                          run.id IS NULL
                          OR run.batch_attempt_id IS DISTINCT FROM attempt.id
                          OR run.request_id IS DISTINCT FROM item.request_id
                          OR run.execution_mode <> 'DEMO_ONLY'
                          OR run.status <> 'SUCCEEDED'
                          OR run.created_by IS DISTINCT FROM attempt.created_by
                          OR jsonb_array_length(COALESCE(run.definition_json->'nodes', '[]'::jsonb)) <> 1
                          OR run.definition_json->'nodes'->0->>'node_key' IS DISTINCT FROM item.node_key
                          OR run.definition_json->'nodes'->0->>'task_type_id' IS DISTINCT FROM item.task_type_id
                          OR (run.definition_json->'nodes'->0->>'task_type_version')::integer IS DISTINCT FROM item.task_type_version
                          OR jsonb_array_length(COALESCE(run.definition_json->'nodes'->0->'depends_on', '[]'::jsonb)) <> 0
                          OR attempt.completed_at IS NOT NULL
                          OR EXISTS (
                              SELECT 1 FROM batch_dispatches AS dispatch
                              WHERE dispatch.workflow_run_id = attempt.workflow_run_id
                          )
                      )
                ) THEN
                    RAISE EXCEPTION 'queued batch attempt link lacks exact runner crash-window identity; remediate before batch attempt identity migration';
                END IF;
                IF EXISTS (
                    SELECT 1
                    FROM batch_dispatches AS dispatch
                    LEFT JOIN batch_execution_attempts AS attempt ON attempt.id = dispatch.attempt_id
                    WHERE dispatch.attempt_id IS NOT NULL
                      AND (
                          attempt.id IS NULL
                          OR attempt.workflow_run_id IS DISTINCT FROM dispatch.workflow_run_id
                          OR attempt.work_item_id IS DISTINCT FROM dispatch.work_item_id
                          OR attempt.batch_profile_id IS DISTINCT FROM dispatch.batch_profile_id
                      )
                ) THEN
                    RAISE EXCEPTION 'batch dispatch attempt identity lacks exact run/work-item/profile provenance; remediate before batch attempt identity migration';
                END IF;
                IF EXISTS (
                    SELECT 1
                    FROM batch_execution_attempts AS attempt
                    WHERE attempt.status = 'SUCCEEDED'
                      AND NOT EXISTS (
                          SELECT 1 FROM batch_dispatches AS dispatch
                          WHERE dispatch.workflow_run_id = attempt.workflow_run_id
                      )
                ) THEN
                    RAISE EXCEPTION 'succeeded batch attempt has no batch dispatch; remediate before batch attempt identity migration';
                END IF;
                IF EXISTS (
                    SELECT 1
                    FROM batch_execution_attempts AS attempt
                    JOIN batch_dispatches AS dispatch ON dispatch.workflow_run_id = attempt.workflow_run_id
                    WHERE attempt.status = 'SUCCEEDED'
                      AND (dispatch.work_item_id <> attempt.work_item_id
                           OR dispatch.batch_profile_id <> attempt.batch_profile_id)
                ) THEN
                    RAISE EXCEPTION 'succeeded batch attempt and dispatch provenance disagree; remediate before batch attempt identity migration';
                END IF;
            END $$"""
        )
    )
    op.execute(
        sa.text(
            """UPDATE workflow_runs AS run
            SET batch_attempt_id = attempt.id
            FROM batch_execution_attempts AS attempt
            WHERE attempt.workflow_run_id = run.id
              AND attempt.workflow_run_id IS NOT NULL
              AND attempt.status = 'SUCCEEDED'
              AND run.batch_attempt_id IS NULL"""
        )
    )
    op.execute(
        sa.text(
            """UPDATE batch_dispatches AS dispatch
            SET attempt_id = attempt.id
            FROM batch_execution_attempts AS attempt
            WHERE attempt.workflow_run_id = dispatch.workflow_run_id
              AND attempt.workflow_run_id IS NOT NULL
              AND attempt.status = 'SUCCEEDED'
              AND dispatch.attempt_id IS NULL"""
        )
    )
    # Recheck the completed historical rows after their exact reverse
    # identities have been backfilled above.
    op.execute(
        sa.text(
            """DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM batch_execution_attempts AS attempt
                    LEFT JOIN workflow_runs AS run ON run.id = attempt.workflow_run_id
                    LEFT JOIN request_work_items AS item ON item.id = attempt.work_item_id
                    WHERE attempt.status = 'SUCCEEDED'
                      AND (
                          run.id IS NULL
                          OR run.batch_attempt_id IS DISTINCT FROM attempt.id
                          OR run.request_id IS DISTINCT FROM item.request_id
                          OR run.execution_mode <> 'DEMO_ONLY'
                          OR run.status <> 'SUCCEEDED'
                          OR run.created_by IS DISTINCT FROM attempt.created_by
                          OR jsonb_array_length(COALESCE(run.definition_json->'nodes', '[]'::jsonb)) <> 1
                          OR run.definition_json->'nodes'->0->>'node_key' IS DISTINCT FROM item.node_key
                          OR run.definition_json->'nodes'->0->>'task_type_id' IS DISTINCT FROM item.task_type_id
                          OR (run.definition_json->'nodes'->0->>'task_type_version')::integer IS DISTINCT FROM item.task_type_version
                          OR jsonb_array_length(COALESCE(run.definition_json->'nodes'->0->'depends_on', '[]'::jsonb)) <> 0
                      )
                ) THEN
                    RAISE EXCEPTION 'succeeded batch attempt lacks exact runner identity; remediate before batch attempt identity migration';
                END IF;
            END $$"""
        )
    )

    # PostgreSQL UNIQUE indexes allow multiple null values, which preserves
    # unrelated legacy workflow runs while making every new non-null identity
    # one-to-one.
    op.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS ux_workflow_runs_batch_attempt_id ON workflow_runs(batch_attempt_id)"))
    op.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS ux_batch_dispatches_attempt_id ON batch_dispatches(attempt_id)"))
    for table_name, constraint_name, column_name, target_table in (
        ("workflow_runs", "fk_workflow_runs_batch_attempt", "batch_attempt_id", "batch_execution_attempts"),
        ("batch_dispatches", "fk_batch_dispatches_attempt", "attempt_id", "batch_execution_attempts"),
    ):
        op.execute(
            sa.text(
                f"""DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = '{constraint_name}'
                          AND conrelid = '{table_name}'::regclass
                    ) THEN
                        ALTER TABLE {table_name}
                        ADD CONSTRAINT {constraint_name}
                        FOREIGN KEY ({column_name}) REFERENCES {target_table}(id);
                    END IF;
                END $$"""
            )
        )


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE batch_dispatches DROP CONSTRAINT IF EXISTS fk_batch_dispatches_attempt"))
    op.execute(sa.text("ALTER TABLE workflow_runs DROP CONSTRAINT IF EXISTS fk_workflow_runs_batch_attempt"))
    op.execute(sa.text("DROP INDEX IF EXISTS ux_batch_dispatches_attempt_id"))
    op.execute(sa.text("DROP INDEX IF EXISTS ux_workflow_runs_batch_attempt_id"))
    op.execute(sa.text("ALTER TABLE batch_dispatches DROP COLUMN IF EXISTS attempt_id"))
    op.execute(sa.text("ALTER TABLE workflow_runs DROP COLUMN IF EXISTS batch_attempt_id"))
