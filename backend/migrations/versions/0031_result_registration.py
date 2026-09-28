"""Persist isolated result-registration drafts, approvals, and captured paths."""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0031_result_registration"
down_revision = "0030_folder_environment_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
    CREATE TABLE IF NOT EXISTS result_registration_paths (
        id VARCHAR PRIMARY KEY, root_key VARCHAR(64) NOT NULL, project_id VARCHAR NOT NULL,
        request_id VARCHAR NOT NULL, environment VARCHAR NOT NULL CHECK(environment IN ('USAGE','DISTRIBUTION')),
        relative_path VARCHAR(2048) NOT NULL, path_key VARCHAR(2048) NOT NULL, parent_relative_path VARCHAR(2048) NOT NULL,
        role_kind VARCHAR(32) NOT NULL, target_id VARCHAR(256) NOT NULL, raw_name VARCHAR(512) NOT NULL,
        option_status VARCHAR CHECK(option_status IN ('PRESENT','ABSENT','UNRESOLVED')),
        created_by VARCHAR NOT NULL, created_at TIMESTAMP NOT NULL,
        UNIQUE(root_key,path_key)
    );
    CREATE TABLE IF NOT EXISTS result_registration_drafts (
        id VARCHAR PRIMARY KEY, project_id VARCHAR NOT NULL, request_id VARCHAR NOT NULL,
        environment VARCHAR NOT NULL CHECK(environment IN ('USAGE','DISTRIBUTION')),
        storage_root_id VARCHAR(128) NOT NULL, case_relative_path VARCHAR(2048) NOT NULL,
        result_relative_path VARCHAR(2048) NOT NULL, context_json JSONB NOT NULL,
        manifest_json JSONB NOT NULL, inspection_json JSONB, source_revision CHAR(64) NOT NULL,
        inspection_revision CHAR(64), approval_json JSONB, publish_idempotency_key VARCHAR UNIQUE,
        case_id VARCHAR(128), capture_id VARCHAR(128), status VARCHAR(32) NOT NULL,
        mirror_status VARCHAR(32), error_json JSONB, revision INTEGER NOT NULL DEFAULT 1,
        created_by VARCHAR NOT NULL, updated_by VARCHAR NOT NULL, approved_by VARCHAR,
        published_at TIMESTAMP, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL
    );
    CREATE TABLE IF NOT EXISTS result_registration_files (
        draft_id VARCHAR NOT NULL REFERENCES result_registration_drafts(id), relative_path VARCHAR(2048) NOT NULL,
        sha256 CHAR(64) NOT NULL, size_bytes BIGINT NOT NULL CHECK(size_bytes >= 0 AND size_bytes <= 33554432),
        media_type VARCHAR(128) NOT NULL, content BYTEA NOT NULL, uploaded_by VARCHAR NOT NULL,
        uploaded_at TIMESTAMP NOT NULL, PRIMARY KEY(draft_id,relative_path)
    );
    CREATE TABLE IF NOT EXISTS result_registration_events (
        id VARCHAR PRIMARY KEY, draft_id VARCHAR NOT NULL REFERENCES result_registration_drafts(id),
        action VARCHAR(64) NOT NULL, detail_json JSONB NOT NULL, actor VARCHAR NOT NULL, occurred_at TIMESTAMP NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ix_result_registration_paths_target ON result_registration_paths(root_key,project_id,request_id,environment,relative_path);
    CREATE INDEX IF NOT EXISTS ix_result_registration_drafts_request ON result_registration_drafts(project_id,request_id,status,created_at DESC);
    CREATE INDEX IF NOT EXISTS ix_result_registration_files_draft ON result_registration_files(draft_id,relative_path);
    CREATE INDEX IF NOT EXISTS ix_result_registration_events_draft ON result_registration_events(draft_id,occurred_at DESC);
    """))
    role = os.getenv("SIM_DASH_APP_ROLE", "simdashboard_app").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", role):
        raise ValueError("SIM_DASH_APP_ROLE is not a safe PostgreSQL identifier")
    op.execute(sa.text(f'''DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON result_registration_paths,result_registration_drafts,result_registration_files,result_registration_events TO "{role}";
    END IF; END $$;'''))


def downgrade() -> None:
    raise RuntimeError("Result-registration review history is durable; destructive downgrade is not supported.")
