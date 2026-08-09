"""Add immutable batch profile versions and execution attempt/event history.

Revision ID: 0006_batch_attempts
Revises: 0005_batch_execution_profiles
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_batch_attempts"
down_revision = "0005_batch_execution_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("ALTER TABLE quality_thresholds DROP CONSTRAINT IF EXISTS quality_thresholds_pkey"))
    op.execute(sa.text("ALTER TABLE quality_thresholds ADD PRIMARY KEY (project_id, criterion_key)"))
    op.execute(sa.text("""
        INSERT INTO quality_thresholds
            (criterion_key, project_id, analysis_key, label, threshold_double, unit, updated_by, updated_at)
        SELECT defaults.criterion_key, projects.id, defaults.analysis_key, defaults.label,
               defaults.threshold_double, defaults.unit, 'system', CURRENT_TIMESTAMP
        FROM projects
        CROSS JOIN (
            VALUES
                ('chassis_rear_permanent_deformation_mm', 'CHASSIS_REAR_PERMANENT_DEFORMATION',
                 'Chassis Rear permanent deformation limit', 5.0, 'mm'),
                ('open_cell_stress_mpa', 'OPEN_CELL_STRESS', 'Open Cell stress limit', 75.0, 'MPa')
        ) AS defaults(criterion_key, analysis_key, label, threshold_double, unit)
        ON CONFLICT (project_id, criterion_key) DO NOTHING
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'ck_request_work_items_progress_range'
            ) THEN
                ALTER TABLE request_work_items
                ADD CONSTRAINT ck_request_work_items_progress_range CHECK (progress BETWEEN 0 AND 100);
            END IF;
        END $$
    """))
    op.execute(sa.text("ALTER TABLE batch_path_profiles ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1"))
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS batch_path_profile_versions (
            id VARCHAR NOT NULL,
            version INTEGER NOT NULL,
            name VARCHAR NOT NULL,
            solver_path VARCHAR NOT NULL,
            working_directory VARCHAR NOT NULL,
            arguments_template VARCHAR NOT NULL,
            environment_json JSONB NOT NULL,
            task_type_ids_json JSONB NOT NULL,
            is_active BOOLEAN NOT NULL,
            created_by VARCHAR NOT NULL,
            created_at TIMESTAMP NOT NULL,
            PRIMARY KEY (id, version)
        )
    """))
    op.execute(sa.text("""
        INSERT INTO batch_path_profile_versions
            (id, version, name, solver_path, working_directory, arguments_template,
             environment_json, task_type_ids_json, is_active, created_by, created_at)
        SELECT id, version, name, solver_path, working_directory, arguments_template,
               environment_json, task_type_ids_json, is_active, updated_by, updated_at
        FROM batch_path_profiles
        ON CONFLICT (id, version) DO NOTHING
    """))
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS batch_execution_attempts (
            id VARCHAR PRIMARY KEY,
            work_item_id VARCHAR NOT NULL REFERENCES request_work_items(id),
            workflow_run_id VARCHAR REFERENCES workflow_runs(id),
            batch_profile_id VARCHAR NOT NULL,
            batch_profile_version INTEGER NOT NULL,
            profile_snapshot_json JSONB NOT NULL,
            command_preview VARCHAR NOT NULL,
            idempotency_key VARCHAR NOT NULL,
            execution_mode VARCHAR NOT NULL CHECK (execution_mode = 'DEMO_ONLY'),
            status VARCHAR NOT NULL CHECK (status IN ('PREFLIGHT', 'QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'REJECTED')),
            progress INTEGER NOT NULL CHECK (progress BETWEEN 0 AND 100),
            last_message VARCHAR NOT NULL,
            created_by VARCHAR NOT NULL,
            created_at TIMESTAMP NOT NULL,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            UNIQUE (work_item_id, idempotency_key),
            FOREIGN KEY (batch_profile_id, batch_profile_version) REFERENCES batch_path_profile_versions(id, version)
        )
    """))
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS batch_execution_events (
            id VARCHAR PRIMARY KEY,
            attempt_id VARCHAR NOT NULL REFERENCES batch_execution_attempts(id),
            event_index INTEGER NOT NULL,
            event_type VARCHAR NOT NULL,
            level VARCHAR NOT NULL,
            message VARCHAR NOT NULL,
            progress INTEGER NOT NULL CHECK (progress BETWEEN 0 AND 100),
            occurred_at TIMESTAMP NOT NULL,
            UNIQUE (attempt_id, event_index)
        )
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_batch_profile_versions_id ON batch_path_profile_versions(id, version DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_batch_attempts_work_item ON batch_execution_attempts(work_item_id, created_at DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_batch_attempts_status ON batch_execution_attempts(status, created_at)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_batch_events_attempt ON batch_execution_events(attempt_id, event_index)"))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS batch_execution_events"))
    op.execute(sa.text("DROP TABLE IF EXISTS batch_execution_attempts"))
    op.execute(sa.text("DROP TABLE IF EXISTS batch_path_profile_versions"))
    op.execute(sa.text("ALTER TABLE batch_path_profiles DROP COLUMN IF EXISTS version"))
    op.execute(sa.text("ALTER TABLE request_work_items DROP CONSTRAINT IF EXISTS ck_request_work_items_progress_range"))
    op.execute(sa.text("""
        WITH ranked AS (
            SELECT project_id, criterion_key,
                   row_number() OVER (
                       PARTITION BY criterion_key
                       ORDER BY CASE WHEN project_id = 'project-tv-001' THEN 0 ELSE 1 END, project_id
                   ) AS position
            FROM quality_thresholds
        )
        DELETE FROM quality_thresholds target
        USING ranked
        WHERE target.project_id = ranked.project_id
          AND target.criterion_key = ranked.criterion_key
          AND ranked.position > 1
    """))
    op.execute(sa.text("ALTER TABLE quality_thresholds DROP CONSTRAINT IF EXISTS quality_thresholds_pkey"))
    op.execute(sa.text("ALTER TABLE quality_thresholds ADD PRIMARY KEY (criterion_key)"))
