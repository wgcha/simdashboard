"""Add explicit, root-bound folder discovery state."""
from __future__ import annotations
import os, re
import sqlalchemy as sa
from alembic import op
revision = "0027_folder_discovery"
down_revision = "0026_semantic_import_review"
branch_labels = None
depends_on = None
def upgrade() -> None:
    op.execute(sa.text("""CREATE TABLE IF NOT EXISTS folder_discovery_scans (id VARCHAR PRIMARY KEY, root_key VARCHAR(64) NOT NULL, root_path VARCHAR(2048) NOT NULL, relative_path VARCHAR(1024) NOT NULL, status VARCHAR(16) NOT NULL CHECK(status IN ('COMPLETE','INCOMPLETE')), tree_json JSONB NOT NULL, issues_json JSONB NOT NULL, created_by VARCHAR NOT NULL, created_at TIMESTAMP NOT NULL);
    CREATE TABLE IF NOT EXISTS folder_discovery_previews (id VARCHAR PRIMARY KEY, scan_id VARCHAR NOT NULL REFERENCES folder_discovery_scans(id), rules_json JSONB NOT NULL, rules_revision INTEGER NOT NULL CHECK(rules_revision >= 0), rows_json JSONB NOT NULL, can_apply BOOLEAN NOT NULL, applied_json JSONB, created_by VARCHAR NOT NULL, created_at TIMESTAMP NOT NULL);
    CREATE TABLE IF NOT EXISTS folder_discovery_rules (root_key VARCHAR(64) NOT NULL, relative_path VARCHAR(1024) NOT NULL, rules_json JSONB NOT NULL, revision INTEGER NOT NULL CHECK(revision >= 1), updated_at TIMESTAMP NOT NULL, updated_by VARCHAR NOT NULL, PRIMARY KEY(root_key, relative_path));
    CREATE TABLE IF NOT EXISTS folder_discovery_registry (id VARCHAR PRIMARY KEY, root_key VARCHAR(64) NOT NULL, relative_path VARCHAR(1024) NOT NULL, role VARCHAR(16) NOT NULL CHECK(role IN ('PROJECT','REQUEST','LOAD_CASE')), scope_key VARCHAR(128) NOT NULL, code VARCHAR(256) NOT NULL CHECK(length(trim(code)) > 0), name VARCHAR(512) NOT NULL CHECK(length(trim(name)) > 0), analysis_type VARCHAR(128) NOT NULL DEFAULT '', parent_target_id VARCHAR(128), target_id VARCHAR(128) NOT NULL UNIQUE, created_at TIMESTAMP NOT NULL, UNIQUE(root_key, role, scope_key, code), UNIQUE(root_key, relative_path, role));
    CREATE INDEX IF NOT EXISTS ix_folder_discovery_scans_root ON folder_discovery_scans(root_key, created_at DESC); CREATE INDEX IF NOT EXISTS ix_folder_discovery_registry_root_path ON folder_discovery_registry(root_key, relative_path);"""))
    role = os.getenv("SIM_DASH_APP_ROLE", "simdashboard_app").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", role): raise ValueError("SIM_DASH_APP_ROLE is not a safe PostgreSQL identifier")
    op.execute(sa.text(f'''DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN GRANT SELECT, INSERT, UPDATE, DELETE ON folder_discovery_scans, folder_discovery_previews, folder_discovery_rules, folder_discovery_registry TO "{role}"; END IF; END $$;'''))
def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS folder_discovery_registry; DROP TABLE IF EXISTS folder_discovery_rules; DROP TABLE IF EXISTS folder_discovery_previews; DROP TABLE IF EXISTS folder_discovery_scans"))
