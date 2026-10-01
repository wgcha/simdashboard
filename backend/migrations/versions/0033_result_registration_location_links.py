"""Store editable result-location links separately from result history."""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op


revision = "0033_result_registration_location_links"
down_revision = "0032_materials_dashboard_menu"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Alembic's default version table uses VARCHAR(32), but this revision ID is
    # longer. Widen the existing value before Alembic writes the new revision.
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=32),
        type_=sa.String(length=64),
        existing_nullable=False,
    )
    op.execute(sa.text("""
    CREATE TABLE IF NOT EXISTS result_registration_location_links (
        id VARCHAR PRIMARY KEY,
        root_key VARCHAR(64) NOT NULL,
        project_id VARCHAR NOT NULL,
        request_id VARCHAR NOT NULL,
        environment VARCHAR NOT NULL CHECK (environment IN ('USAGE','DISTRIBUTION')),
        relative_path VARCHAR(2048) NOT NULL,
        path_key VARCHAR(2048) NOT NULL,
        schema_parent_path VARCHAR(2048) NOT NULL,
        schema_role_kind VARCHAR(32) NOT NULL CHECK (schema_role_kind IN ('EVALUATION','SCENE')),
        schema_target_id VARCHAR(256),
        schema_scan_id VARCHAR(128) NOT NULL,
        schema_profile_id VARCHAR(128) NOT NULL,
        schema_profile_revision INTEGER NOT NULL CHECK (schema_profile_revision >= 1),
        revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
        created_by VARCHAR NOT NULL,
        created_at TIMESTAMP NOT NULL,
        updated_by VARCHAR NOT NULL,
        updated_at TIMESTAMP NOT NULL,
        UNIQUE (root_key, project_id, request_id, environment, path_key)
    );
    CREATE INDEX IF NOT EXISTS ix_result_location_links_scope
        ON result_registration_location_links(root_key, project_id, request_id, environment, relative_path);
    """))
    role = os.getenv("SIM_DASH_APP_ROLE", "simdashboard_app").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", role):
        raise ValueError("SIM_DASH_APP_ROLE is not a safe PostgreSQL identifier")
    op.execute(sa.text(f'''DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON result_registration_location_links TO "{role}";
    END IF; END $$;'''))


def downgrade() -> None:
    raise RuntimeError("Saved result-location links are user-owned state; destructive downgrade is not supported.")
