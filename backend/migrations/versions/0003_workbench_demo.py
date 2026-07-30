"""Add versioned workbench contracts and demo-only runs.

Revision ID: 0003_workbench_demo
Revises: 0002_security_audit
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_workbench_demo"
down_revision = "0002_security_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS task_type_versions (
            id VARCHAR NOT NULL,
            version INTEGER NOT NULL,
            kind VARCHAR NOT NULL,
            display_name VARCHAR NOT NULL,
            description VARCHAR NOT NULL,
            supports_standalone BOOLEAN NOT NULL DEFAULT true,
            input_artifact_types_json JSONB NOT NULL,
            output_artifact_types_json JSONB NOT NULL,
            parameter_schema_json JSONB NOT NULL,
            demo_artifact_url VARCHAR NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMP NOT NULL,
            PRIMARY KEY (id, version)
        )
    """))
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS request_type_versions (
            id VARCHAR NOT NULL,
            version INTEGER NOT NULL,
            display_name VARCHAR NOT NULL,
            description VARCHAR NOT NULL,
            allowed_task_types_json JSONB NOT NULL,
            default_workflow_json JSONB NOT NULL,
            match_rules_json JSONB NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMP NOT NULL,
            PRIMARY KEY (id, version)
        )
    """))
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS workflow_runs (
            id VARCHAR PRIMARY KEY,
            name VARCHAR NOT NULL,
            request_id VARCHAR,
            request_type_id VARCHAR,
            request_type_version INTEGER,
            definition_json JSONB NOT NULL,
            execution_mode VARCHAR NOT NULL,
            status VARCHAR NOT NULL,
            progress INTEGER NOT NULL,
            created_by VARCHAR NOT NULL,
            created_at TIMESTAMP NOT NULL,
            started_at TIMESTAMP NOT NULL,
            completed_at TIMESTAMP,
            CONSTRAINT ck_workflow_runs_demo_only CHECK (execution_mode = 'DEMO_ONLY'),
            CONSTRAINT ck_workflow_runs_progress CHECK (progress BETWEEN 0 AND 100)
        )
    """))
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS analysis_request_type_assignments (
            request_id VARCHAR PRIMARY KEY,
            request_type_id VARCHAR NOT NULL,
            request_type_version INTEGER NOT NULL,
            source VARCHAR NOT NULL,
            rule_snapshot_json JSONB NOT NULL,
            decided_by VARCHAR NOT NULL,
            decided_at TIMESTAMP NOT NULL,
            CONSTRAINT ck_request_type_assignment_source CHECK (source IN ('ADMIN', 'RULE', 'USER', 'DEFAULT'))
        )
    """))
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS task_runs (
            id VARCHAR PRIMARY KEY,
            workflow_run_id VARCHAR NOT NULL,
            node_key VARCHAR NOT NULL,
            task_type_id VARCHAR NOT NULL,
            task_type_version INTEGER NOT NULL,
            status VARCHAR NOT NULL,
            progress INTEGER NOT NULL,
            depends_on_json JSONB NOT NULL,
            demo_artifact_url VARCHAR NOT NULL,
            started_at TIMESTAMP NOT NULL,
            completed_at TIMESTAMP,
            UNIQUE (workflow_run_id, node_key),
            CONSTRAINT ck_task_runs_progress CHECK (progress BETWEEN 0 AND 100)
        )
    """))
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS task_run_events (
            id VARCHAR PRIMARY KEY,
            task_run_id VARCHAR NOT NULL,
            event_index INTEGER NOT NULL,
            event_type VARCHAR NOT NULL,
            level VARCHAR NOT NULL,
            message VARCHAR NOT NULL,
            progress INTEGER NOT NULL,
            occurred_at TIMESTAMP NOT NULL,
            UNIQUE (task_run_id, event_index),
            CONSTRAINT ck_task_run_events_progress CHECK (progress BETWEEN 0 AND 100)
        )
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_workflow_runs_request ON workflow_runs(request_id, created_at DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_request_type_assignments_type ON analysis_request_type_assignments(request_type_id, request_type_version)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_task_runs_workflow ON task_runs(workflow_run_id, started_at)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_task_run_events_task ON task_run_events(task_run_id, event_index)"))
    op.execute(sa.text("""
        DO $$ BEGIN
            ALTER TABLE workflow_runs ADD CONSTRAINT fk_workflow_runs_request FOREIGN KEY (request_id) REFERENCES analysis_requests(id);
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """))
    op.execute(sa.text("""
        DO $$ BEGIN
            ALTER TABLE analysis_request_type_assignments ADD CONSTRAINT fk_request_type_assignments_request FOREIGN KEY (request_id) REFERENCES analysis_requests(id);
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """))
    op.execute(sa.text("""
        DO $$ BEGIN
            ALTER TABLE analysis_request_type_assignments ADD CONSTRAINT fk_request_type_assignments_type FOREIGN KEY (request_type_id, request_type_version) REFERENCES request_type_versions(id, version);
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """))
    op.execute(sa.text("""
        DO $$ BEGIN
            ALTER TABLE workflow_runs ADD CONSTRAINT fk_workflow_runs_request_type FOREIGN KEY (request_type_id, request_type_version) REFERENCES request_type_versions(id, version);
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """))
    op.execute(sa.text("""
        DO $$ BEGIN
            ALTER TABLE task_runs ADD CONSTRAINT fk_task_runs_workflow FOREIGN KEY (workflow_run_id) REFERENCES workflow_runs(id);
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """))
    op.execute(sa.text("""
        DO $$ BEGIN
            ALTER TABLE task_runs ADD CONSTRAINT fk_task_runs_type FOREIGN KEY (task_type_id, task_type_version) REFERENCES task_type_versions(id, version);
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """))
    op.execute(sa.text("""
        DO $$ BEGIN
            ALTER TABLE task_run_events ADD CONSTRAINT fk_task_run_events_task FOREIGN KEY (task_run_id) REFERENCES task_runs(id);
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS task_run_events"))
    op.execute(sa.text("DROP TABLE IF EXISTS task_runs"))
    op.execute(sa.text("DROP TABLE IF EXISTS workflow_runs"))
    op.execute(sa.text("DROP TABLE IF EXISTS analysis_request_type_assignments"))
    op.execute(sa.text("DROP TABLE IF EXISTS request_type_versions"))
    op.execute(sa.text("DROP TABLE IF EXISTS task_type_versions"))
