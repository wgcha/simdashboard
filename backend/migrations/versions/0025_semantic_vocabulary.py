"""Add reusable user vocabulary entries independently of semantic mappings."""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op


revision = "0025_semantic_vocabulary"
down_revision = "0024_semantic_mapping"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS semantic_vocabulary_entries (
            id VARCHAR PRIMARY KEY, key VARCHAR NOT NULL CHECK(key ~ '^[a-z][a-z0-9_]{1,127}$'),
            label VARCHAR(200) NOT NULL, description VARCHAR(4000) NOT NULL DEFAULT '',
            target_kind VARCHAR(20) NOT NULL CHECK(target_kind IN ('FOLDER_ROLE', 'PROJECT', 'REQUEST', 'LOAD_CASE', 'RESULT_ITEM')),
            target_id VARCHAR(200) NOT NULL, scope_project_id VARCHAR(200) REFERENCES projects(id),
            aliases_json JSONB NOT NULL, revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
            enabled BOOLEAN NOT NULL DEFAULT true, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL,
            created_by VARCHAR NOT NULL, updated_by VARCHAR NOT NULL
        );
        CREATE TABLE IF NOT EXISTS semantic_vocabulary_terms (
            entry_id VARCHAR NOT NULL REFERENCES semantic_vocabulary_entries(id) ON DELETE CASCADE,
            scope_key VARCHAR(200) NOT NULL, target_kind VARCHAR(20) NOT NULL,
            normalized_term VARCHAR(200) NOT NULL,
            PRIMARY KEY(scope_key, target_kind, normalized_term)
        );
        CREATE INDEX IF NOT EXISTS ix_semantic_vocabulary_entries_scope
          ON semantic_vocabulary_entries(scope_project_id, target_kind, enabled);
    """))
    app_role = os.getenv("SIM_DASH_APP_ROLE", "simdashboard_app").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", app_role):
        raise ValueError("SIM_DASH_APP_ROLE is not a safe PostgreSQL identifier")
    op.execute(sa.text(f"""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{app_role}') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON semantic_vocabulary_entries, semantic_vocabulary_terms TO \"{app_role}\";
          END IF;
        END $$;
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS semantic_vocabulary_terms"))
    op.execute(sa.text("DROP TABLE IF EXISTS semantic_vocabulary_entries"))
