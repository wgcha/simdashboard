"""PostgreSQL fresh-install counterpart of additive migration 0027."""

FOLDER_DISCOVERY_DDL = """
CREATE TABLE IF NOT EXISTS folder_discovery_scans (
 id VARCHAR PRIMARY KEY, root_key VARCHAR(64) NOT NULL, root_path VARCHAR(2048) NOT NULL, relative_path VARCHAR(1024) NOT NULL,
 status VARCHAR(16) NOT NULL CHECK(status IN ('COMPLETE','INCOMPLETE')), tree_json JSONB NOT NULL, issues_json JSONB NOT NULL, created_by VARCHAR NOT NULL, created_at TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS folder_discovery_previews (
 id VARCHAR PRIMARY KEY, scan_id VARCHAR NOT NULL REFERENCES folder_discovery_scans(id), rules_json JSONB NOT NULL, rules_revision INTEGER NOT NULL CHECK(rules_revision >= 0),
 rows_json JSONB NOT NULL, can_apply BOOLEAN NOT NULL, applied_json JSONB, created_by VARCHAR NOT NULL, created_at TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS folder_discovery_rules (
 root_key VARCHAR(64) NOT NULL, relative_path VARCHAR(1024) NOT NULL, rules_json JSONB NOT NULL, revision INTEGER NOT NULL CHECK(revision >= 1), updated_at TIMESTAMP NOT NULL, updated_by VARCHAR NOT NULL,
 PRIMARY KEY(root_key, relative_path)
);
CREATE TABLE IF NOT EXISTS folder_discovery_registry (
 id VARCHAR PRIMARY KEY, root_key VARCHAR(64) NOT NULL, relative_path VARCHAR(1024) NOT NULL, role VARCHAR(16) NOT NULL CHECK(role IN ('PROJECT','REQUEST','LOAD_CASE')), scope_key VARCHAR(128) NOT NULL,
 code VARCHAR(256) NOT NULL CHECK(length(trim(code)) > 0), name VARCHAR(512) NOT NULL CHECK(length(trim(name)) > 0), analysis_type VARCHAR(128) NOT NULL DEFAULT '', parent_target_id VARCHAR(128), target_id VARCHAR(128) NOT NULL UNIQUE,
 created_at TIMESTAMP NOT NULL, UNIQUE(root_key, role, scope_key, code), UNIQUE(root_key, relative_path, role)
);
CREATE INDEX IF NOT EXISTS ix_folder_discovery_scans_root ON folder_discovery_scans(root_key, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_folder_discovery_registry_root_path ON folder_discovery_registry(root_key, relative_path);
""".strip()
