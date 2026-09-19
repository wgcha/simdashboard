"""Persist environment-specific folder plans and capture retries."""
from __future__ import annotations
import os, re
import sqlalchemy as sa
from alembic import op

revision = "0030_folder_environment_profiles"
down_revision = "0029_dashboard_captures"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute(sa.text("""
    CREATE TABLE IF NOT EXISTS folder_environment_profiles (id VARCHAR PRIMARY KEY, environment VARCHAR NOT NULL CHECK(environment IN ('USAGE','DISTRIBUTION')), name VARCHAR NOT NULL, revision INTEGER NOT NULL, rules_json JSONB NOT NULL, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL, UNIQUE(environment,name));
    CREATE TABLE IF NOT EXISTS folder_environment_scans (id VARCHAR PRIMARY KEY, root_key VARCHAR NOT NULL, relative_path VARCHAR NOT NULL, environment VARCHAR NOT NULL CHECK(environment IN ('USAGE','DISTRIBUTION')), profile_id VARCHAR NOT NULL REFERENCES folder_environment_profiles(id), profile_revision INTEGER NOT NULL, project_id VARCHAR REFERENCES projects(id), request_id VARCHAR REFERENCES analysis_requests(id), status VARCHAR NOT NULL CHECK(status IN ('COMPLETE','INCOMPLETE')), tree_json JSONB NOT NULL, issues_json JSONB NOT NULL, created_by VARCHAR NOT NULL, created_at TIMESTAMP NOT NULL);
    CREATE TABLE IF NOT EXISTS folder_environment_previews (id VARCHAR PRIMARY KEY, scan_id VARCHAR NOT NULL REFERENCES folder_environment_scans(id), rows_json JSONB NOT NULL, can_apply BOOLEAN NOT NULL, created_by VARCHAR NOT NULL, created_at TIMESTAMP NOT NULL);
    CREATE TABLE IF NOT EXISTS folder_environment_registrations (id VARCHAR PRIMARY KEY, preview_id VARCHAR NOT NULL REFERENCES folder_environment_previews(id), idempotency_key VARCHAR NOT NULL UNIQUE, environment VARCHAR NOT NULL CHECK(environment IN ('USAGE','DISTRIBUTION')), project_id VARCHAR, request_id VARCHAR, status VARCHAR NOT NULL CHECK(status IN ('REGISTERED','CAPTURING','COMPLETED','FAILED')), created_by VARCHAR NOT NULL, created_at TIMESTAMP NOT NULL);
    CREATE TABLE IF NOT EXISTS folder_environment_registry (id VARCHAR PRIMARY KEY, registration_id VARCHAR NOT NULL REFERENCES folder_environment_registrations(id), root_key VARCHAR NOT NULL, relative_path VARCHAR NOT NULL, role_kind VARCHAR NOT NULL, parent_context_id VARCHAR, target_id VARCHAR NOT NULL, raw_name VARCHAR NOT NULL, option_status VARCHAR CHECK(option_status IN ('PRESENT','ABSENT','UNRESOLVED')), created_at TIMESTAMP NOT NULL, UNIQUE(registration_id,relative_path,role_kind));
    CREATE TABLE IF NOT EXISTS folder_environment_capture_jobs (id VARCHAR PRIMARY KEY, registration_id VARCHAR NOT NULL REFERENCES folder_environment_registrations(id), case_id VARCHAR NOT NULL, load_case_id VARCHAR, run_case_id VARCHAR, run_option_id VARCHAR, option_label VARCHAR, option_status VARCHAR CHECK(option_status IN ('PRESENT','ABSENT','UNRESOLVED')), status VARCHAR NOT NULL CHECK(status IN ('PENDING','RUNNING','COMPLETED','FAILED')), capture_id VARCHAR REFERENCES dashboard_captures(id), error_code VARCHAR, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL);
    CREATE INDEX IF NOT EXISTS ix_folder_environment_scans_root ON folder_environment_scans(root_key,created_at DESC);
    CREATE INDEX IF NOT EXISTS ix_folder_environment_jobs_registration ON folder_environment_capture_jobs(registration_id,status,created_at);
    INSERT INTO folder_environment_profiles(id,environment,name,revision,rules_json,created_at,updated_at) VALUES
      ('environment-profile-usage-default','USAGE','기본 사용환경 규칙',1,'{"roles":["PROJECT","REQUEST","SIMULATION_CASE","EVALUATION"]}'::JSONB,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP),
      ('environment-profile-distribution-default','DISTRIBUTION','기본 유통환경 규칙',1,'{"roles":["PROJECT","REQUEST","SIMULATION_CASE","LOAD_CASE","EXECUTION_RUN","RUN_OPTION"]}'::JSONB,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
    ON CONFLICT(id) DO NOTHING;
    """))
    role=os.getenv("SIM_DASH_APP_ROLE","simdashboard_app").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}",role): raise ValueError("SIM_DASH_APP_ROLE is not a safe PostgreSQL identifier")
    op.execute(sa.text(f'''DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN GRANT SELECT, INSERT, UPDATE ON folder_environment_profiles,folder_environment_scans,folder_environment_previews,folder_environment_registrations,folder_environment_registry,folder_environment_capture_jobs TO "{role}"; END IF; END $$;'''))
def downgrade() -> None:
    raise RuntimeError("Environment registrations are durable user history; destructive downgrade is not supported.")
