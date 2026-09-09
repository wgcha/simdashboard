"""Add bindings and file index for the separate SPDM workspace root."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0020_spdm_storage"
down_revision = "0019_batch_recovery_lease"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS spdm_storage_settings (
            setting_key VARCHAR PRIMARY KEY,
            setting_value VARCHAR NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
    """))
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS spdm_storage_project_parents (
            project_folder VARCHAR PRIMARY KEY,
            project_id VARCHAR NOT NULL UNIQUE REFERENCES projects(id),
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
    """))
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS spdm_storage_request_parents (
            request_folder VARCHAR PRIMARY KEY,
            project_folder VARCHAR NOT NULL REFERENCES spdm_storage_project_parents(project_folder),
            project_id VARCHAR NOT NULL REFERENCES projects(id),
            request_id VARCHAR NOT NULL UNIQUE REFERENCES analysis_requests(id),
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
    """))
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS spdm_storage_bindings (
            load_case_id VARCHAR PRIMARY KEY REFERENCES load_cases(id),
            project_id VARCHAR NOT NULL REFERENCES projects(id),
            request_id VARCHAR NOT NULL REFERENCES analysis_requests(id),
            relative_path VARCHAR NOT NULL UNIQUE,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
    """))
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS spdm_storage_files (
            id VARCHAR PRIMARY KEY,
            load_case_id VARCHAR NOT NULL REFERENCES load_cases(id),
            relative_path VARCHAR NOT NULL,
            name VARCHAR NOT NULL,
            kind VARCHAR NOT NULL,
            size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
            checksum VARCHAR,
            status VARCHAR NOT NULL,
            run_id VARCHAR REFERENCES analysis_runs(id),
            message VARCHAR,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL,
            UNIQUE(load_case_id, relative_path)
        )
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_spdm_storage_bindings_request ON spdm_storage_bindings(request_id, load_case_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_spdm_storage_files_load_case ON spdm_storage_files(load_case_id, status, relative_path)"))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS spdm_storage_files"))
    op.execute(sa.text("DROP TABLE IF EXISTS spdm_storage_bindings"))
    op.execute(sa.text("DROP TABLE IF EXISTS spdm_storage_request_parents"))
    op.execute(sa.text("DROP TABLE IF EXISTS spdm_storage_project_parents"))
    op.execute(sa.text("DROP TABLE IF EXISTS spdm_storage_settings"))
