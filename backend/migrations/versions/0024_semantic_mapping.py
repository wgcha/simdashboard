"""Persist versioned semantic result mappings and their import provenance."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
import os
import re


revision = "0024_semantic_mapping"
down_revision = "0023_voc_posts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS semantic_result_items (
            id VARCHAR PRIMARY KEY, key VARCHAR NOT NULL UNIQUE, latest_version INTEGER NOT NULL,
            active_version INTEGER, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL,
            updated_by VARCHAR NOT NULL
        );
        CREATE TABLE IF NOT EXISTS semantic_result_item_versions (
            item_id VARCHAR NOT NULL REFERENCES semantic_result_items(id), version INTEGER NOT NULL,
            definition_json JSONB NOT NULL, item_snapshot_json JSONB NOT NULL DEFAULT '[]'::jsonb, lifecycle_status VARCHAR NOT NULL,
            created_at TIMESTAMP NOT NULL, created_by VARCHAR NOT NULL,
            PRIMARY KEY(item_id, version)
        );
        CREATE TABLE IF NOT EXISTS semantic_recipes (
            id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL, latest_version INTEGER NOT NULL,
            active_version INTEGER, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL,
            updated_by VARCHAR NOT NULL
        );
        CREATE TABLE IF NOT EXISTS semantic_recipe_versions (
            recipe_id VARCHAR NOT NULL REFERENCES semantic_recipes(id), version INTEGER NOT NULL,
            definition_json JSONB NOT NULL, item_snapshot_json JSONB NOT NULL DEFAULT '[]'::jsonb, lifecycle_status VARCHAR NOT NULL,
            sample_sha256 CHAR(64), sample_filename VARCHAR, sample_bytes BYTEA, created_at TIMESTAMP NOT NULL, created_by VARCHAR NOT NULL,
            PRIMARY KEY(recipe_id, version), CHECK(sample_bytes IS NULL OR octet_length(sample_bytes) <= 262144)
        );
        CREATE TABLE IF NOT EXISTS semantic_templates (
            id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL, latest_version INTEGER NOT NULL,
            active_version INTEGER, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL,
            updated_by VARCHAR NOT NULL
        );
        CREATE TABLE IF NOT EXISTS semantic_template_versions (
            template_id VARCHAR NOT NULL REFERENCES semantic_templates(id), version INTEGER NOT NULL,
            definition_json JSONB NOT NULL, item_snapshot_json JSONB NOT NULL DEFAULT '[]'::jsonb, lifecycle_status VARCHAR NOT NULL,
            created_at TIMESTAMP NOT NULL, created_by VARCHAR NOT NULL,
            PRIMARY KEY(template_id, version)
        );
        CREATE TABLE IF NOT EXISTS semantic_folder_bindings (
            id VARCHAR PRIMARY KEY, project_id VARCHAR NOT NULL REFERENCES projects(id),
            request_id VARCHAR REFERENCES analysis_requests(id), load_case_id VARCHAR REFERENCES load_cases(id),
            relative_path VARCHAR NOT NULL UNIQUE, role VARCHAR NOT NULL, recipe_ids_json JSONB NOT NULL,
            template_id VARCHAR REFERENCES semantic_templates(id), created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL, created_by VARCHAR NOT NULL, revision INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS semantic_import_provenance (
            analysis_run_id VARCHAR PRIMARY KEY REFERENCES analysis_runs(id), load_case_id VARCHAR NOT NULL REFERENCES load_cases(id),
            recipe_id VARCHAR NOT NULL, recipe_version INTEGER NOT NULL, template_id VARCHAR,
            template_version INTEGER, source_name VARCHAR NOT NULL, source_sha256 CHAR(64) NOT NULL,
            observations_json JSONB NOT NULL, sample_bytes BYTEA, created_at TIMESTAMP NOT NULL,
            CHECK(sample_bytes IS NULL OR octet_length(sample_bytes) <= 262144)
        );
        CREATE INDEX IF NOT EXISTS ix_semantic_folder_bindings_project ON semantic_folder_bindings(project_id, load_case_id);
        CREATE INDEX IF NOT EXISTS ix_semantic_import_provenance_lookup ON semantic_import_provenance(load_case_id, recipe_id, recipe_version);
    """))
    # Older databases may predate the owner's default privileges.  Grant CRUD
    # explicitly for the configured runtime role; this never grants DDL.
    app_role = os.getenv("SIM_DASH_APP_ROLE", "simdashboard_app").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", app_role):
        raise ValueError("SIM_DASH_APP_ROLE is not a safe PostgreSQL identifier")
    op.execute(sa.text(f"""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{app_role}') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON semantic_result_items, semantic_result_item_versions,
              semantic_recipes, semantic_recipe_versions, semantic_templates, semantic_template_versions,
              semantic_folder_bindings, semantic_import_provenance, spdm_storage_bindings TO "{app_role}";
          END IF;
        END $$;
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS semantic_import_provenance"))
    op.execute(sa.text("DROP TABLE IF EXISTS semantic_folder_bindings"))
    op.execute(sa.text("DROP TABLE IF EXISTS semantic_template_versions"))
    op.execute(sa.text("DROP TABLE IF EXISTS semantic_templates"))
    op.execute(sa.text("DROP TABLE IF EXISTS semantic_recipe_versions"))
    op.execute(sa.text("DROP TABLE IF EXISTS semantic_recipes"))
    op.execute(sa.text("DROP TABLE IF EXISTS semantic_result_item_versions"))
    op.execute(sa.text("DROP TABLE IF EXISTS semantic_result_items"))
