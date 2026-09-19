"""Preserve dashboard Case identities, capture manifests and original assets."""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0029_dashboard_captures"
down_revision = "0028_folder_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS dashboard_cases (
            id VARCHAR PRIMARY KEY,
            project_id VARCHAR NOT NULL REFERENCES projects(id),
            request_id VARCHAR NOT NULL REFERENCES analysis_requests(id),
            storage_root_id VARCHAR NOT NULL,
            relative_path VARCHAR NOT NULL,
            environment VARCHAR NOT NULL CHECK(environment IN ('USAGE','DISTRIBUTION')),
            source_name VARCHAR NOT NULL,
            metadata_json JSONB NOT NULL,
            created_at TIMESTAMP NOT NULL,
            UNIQUE(storage_root_id, relative_path)
        );
        CREATE TABLE IF NOT EXISTS dashboard_captures (
            id VARCHAR PRIMARY KEY,
            case_id VARCHAR NOT NULL REFERENCES dashboard_cases(id),
            fingerprint VARCHAR NOT NULL,
            recipe_version VARCHAR NOT NULL,
            manifest_json JSONB NOT NULL,
            payload_json JSONB NOT NULL,
            created_by VARCHAR NOT NULL,
            created_at TIMESTAMP NOT NULL,
            UNIQUE(case_id, fingerprint)
        );
        CREATE TABLE IF NOT EXISTS dashboard_assets (
            id VARCHAR PRIMARY KEY,
            capture_id VARCHAR NOT NULL REFERENCES dashboard_captures(id),
            relative_path VARCHAR NOT NULL,
            sha256 VARCHAR NOT NULL,
            media_type VARCHAR NOT NULL,
            content BYTEA NOT NULL,
            metadata_json JSONB NOT NULL,
            UNIQUE(capture_id, relative_path)
        );
        CREATE INDEX IF NOT EXISTS ix_dashboard_cases_request ON dashboard_cases(request_id);
        CREATE INDEX IF NOT EXISTS ix_dashboard_captures_case ON dashboard_captures(case_id, created_at);
    """))
    role = os.getenv("SIM_DASH_APP_ROLE", "simdashboard_app").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", role):
        raise ValueError("SIM_DASH_APP_ROLE is not a safe PostgreSQL identifier")
    op.execute(sa.text(f'''DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN
        GRANT SELECT, INSERT ON dashboard_cases, dashboard_captures, dashboard_assets TO "{role}";
    END IF; END $$;'''))


def downgrade() -> None:
    raise RuntimeError("Dashboard captures contain user history; destructive downgrade is not supported.")
