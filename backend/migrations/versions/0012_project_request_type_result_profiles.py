"""Add project-scoped result-profile bindings without mutating work types."""

from __future__ import annotations

from alembic import op


revision = "0012_project_result_profiles"
down_revision = "0011_result_layout_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS project_request_type_result_profiles (
            project_id VARCHAR NOT NULL REFERENCES projects(id),
            request_type_id VARCHAR NOT NULL,
            request_type_version INTEGER NOT NULL,
            template_id VARCHAR NOT NULL,
            template_version INTEGER NOT NULL,
            overrides_json JSONB NOT NULL,
            required_data_contracts_json JSONB NOT NULL,
            bound_by VARCHAR NOT NULL,
            bound_at TIMESTAMP NOT NULL,
            PRIMARY KEY (project_id, request_type_id, request_type_version),
            FOREIGN KEY (request_type_id, request_type_version) REFERENCES request_type_versions(id, version),
            FOREIGN KEY (template_id, template_version) REFERENCES analysis_template_versions(template_id, version)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_project_result_profiles_template ON project_request_type_result_profiles(template_id, template_version)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS project_request_type_result_profiles")
