"""Add immutable result layouts selected by immutable request-type versions."""

from __future__ import annotations

from alembic import op


revision = "0011_result_layout_snapshot"
down_revision = "0010_task_batch_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    print("SIMDASH_MIGRATION_STEP=0011_ANALYSIS_TEMPLATES", flush=True)
    op.execute("""
        CREATE TABLE IF NOT EXISTS analysis_template_versions (
            template_id VARCHAR NOT NULL,
            version INTEGER NOT NULL,
            scope_kind VARCHAR NOT NULL CHECK (scope_kind IN ('SYSTEM', 'PROJECT')),
            project_id VARCHAR REFERENCES projects(id),
            display_name VARCHAR NOT NULL,
            description VARCHAR NOT NULL,
            lifecycle_status VARCHAR NOT NULL CHECK (lifecycle_status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')),
            page_definitions_json JSONB NOT NULL,
            created_by VARCHAR NOT NULL,
            created_at TIMESTAMP NOT NULL,
            PRIMARY KEY (template_id, version)
        )
    """)
    print("SIMDASH_MIGRATION_STEP=0011_ANALYSIS_TEMPLATES_DONE", flush=True)
    print("SIMDASH_MIGRATION_STEP=0011_REQUEST_TYPE_PROFILES", flush=True)
    op.execute("""
        CREATE TABLE IF NOT EXISTS request_type_result_profiles (
            request_type_id VARCHAR NOT NULL,
            request_type_version INTEGER NOT NULL,
            template_id VARCHAR NOT NULL,
            template_version INTEGER NOT NULL,
            overrides_json JSONB NOT NULL,
            required_data_contracts_json JSONB NOT NULL,
            PRIMARY KEY (request_type_id, request_type_version),
            FOREIGN KEY (request_type_id, request_type_version) REFERENCES request_type_versions(id, version),
            FOREIGN KEY (template_id, template_version) REFERENCES analysis_template_versions(template_id, version)
        )
    """)
    print("SIMDASH_MIGRATION_STEP=0011_REQUEST_TYPE_PROFILES_DONE", flush=True)
    print("SIMDASH_MIGRATION_STEP=0011_SNAPSHOTS", flush=True)
    op.execute("""
        CREATE TABLE IF NOT EXISTS request_result_layout_snapshots (
            request_id VARCHAR PRIMARY KEY REFERENCES analysis_requests(id),
            source_request_type_id VARCHAR NOT NULL,
            source_request_type_version INTEGER NOT NULL,
            source_template_id VARCHAR NOT NULL,
            source_template_version INTEGER NOT NULL,
            snapshot_json JSONB NOT NULL,
            snapshot_reason VARCHAR NOT NULL CHECK (snapshot_reason IN ('REQUEST_CREATED', 'LEGACY_ASSIGNED', 'MIGRATED')),
            created_by VARCHAR NOT NULL,
            created_at TIMESTAMP NOT NULL
        )
    """)
    print("SIMDASH_MIGRATION_STEP=0011_SNAPSHOTS_DONE", flush=True)
    print("SIMDASH_MIGRATION_STEP=0011_ANALYSIS_TEMPLATE_STATUS_INDEX", flush=True)
    op.execute("CREATE INDEX IF NOT EXISTS ix_analysis_template_versions_status ON analysis_template_versions(template_id, lifecycle_status, version DESC)")
    print("SIMDASH_MIGRATION_STEP=0011_ANALYSIS_TEMPLATE_STATUS_INDEX_DONE", flush=True)
    print("SIMDASH_MIGRATION_STEP=0011_SNAPSHOT_TEMPLATE_INDEX", flush=True)
    op.execute("CREATE INDEX IF NOT EXISTS ix_request_result_layout_snapshots_template ON request_result_layout_snapshots(source_template_id, source_template_version)")
    print("SIMDASH_MIGRATION_STEP=0011_COMPLETE", flush=True)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS request_result_layout_snapshots")
    op.execute("DROP TABLE IF EXISTS request_type_result_profiles")
    op.execute("DROP TABLE IF EXISTS analysis_template_versions")
