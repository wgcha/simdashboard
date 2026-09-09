"""Add immutable product/load-case CSV model template snapshots."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0021_modeling_templates"
down_revision = "0020_spdm_storage"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS modeling_templates (
            id VARCHAR PRIMARY KEY,
            name VARCHAR NOT NULL,
            product_name VARCHAR NOT NULL,
            load_case_name VARCHAR NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            latest_version INTEGER NOT NULL CHECK (latest_version >= 1),
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
    """))
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS modeling_template_versions (
            template_id VARCHAR NOT NULL REFERENCES modeling_templates(id) ON DELETE CASCADE,
            version INTEGER NOT NULL CHECK (version >= 1),
            created_at TIMESTAMPTZ NOT NULL,
            file_count INTEGER NOT NULL CHECK (file_count >= 0 AND file_count <= 200),
            total_bytes BIGINT NOT NULL CHECK (total_bytes >= 0 AND total_bytes <= 26214400),
            PRIMARY KEY (template_id, version)
        )
    """))
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS modeling_template_files (
            id VARCHAR PRIMARY KEY,
            template_id VARCHAR NOT NULL,
            version INTEGER NOT NULL,
            relative_path TEXT NOT NULL,
            size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
            checksum CHAR(64) NOT NULL CHECK (checksum ~ '^[0-9a-f]{64}$'),
            content BYTEA NOT NULL,
            UNIQUE (template_id, version, relative_path),
            FOREIGN KEY (template_id, version) REFERENCES modeling_template_versions(template_id, version) ON DELETE CASCADE
        )
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_modeling_templates_catalog ON modeling_templates(product_name, load_case_name, name)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_modeling_template_files_version ON modeling_template_files(template_id, version, relative_path)"))

def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS modeling_template_files"))
    op.execute(sa.text("DROP TABLE IF EXISTS modeling_template_versions"))
    op.execute(sa.text("DROP TABLE IF EXISTS modeling_templates"))
