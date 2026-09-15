"""PostgreSQL fresh-install counterpart of folder discovery migrations 0027-0028."""

FOLDER_DISCOVERY_DDL = """
CREATE TABLE IF NOT EXISTS folder_discovery_scans (
 id VARCHAR PRIMARY KEY, root_key VARCHAR(64) NOT NULL, root_path VARCHAR(2048) NOT NULL, relative_path VARCHAR(1024) NOT NULL,
 status VARCHAR(16) NOT NULL CHECK(status IN ('COMPLETE','INCOMPLETE')), tree_json JSONB NOT NULL, issues_json JSONB NOT NULL, created_by VARCHAR NOT NULL, created_at TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS folder_discovery_previews (
 id VARCHAR PRIMARY KEY, scan_id VARCHAR NOT NULL REFERENCES folder_discovery_scans(id), rules_json JSONB NOT NULL, rules_revision INTEGER NOT NULL CHECK(rules_revision >= 0),
 catalog_revision INTEGER NOT NULL DEFAULT 1, rows_json JSONB NOT NULL, can_apply BOOLEAN NOT NULL, applied_json JSONB, created_by VARCHAR NOT NULL, created_at TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS folder_discovery_rules (
 root_key VARCHAR(64) NOT NULL, relative_path VARCHAR(1024) NOT NULL, rules_json JSONB NOT NULL, revision INTEGER NOT NULL CHECK(revision >= 1), updated_at TIMESTAMP NOT NULL, updated_by VARCHAR NOT NULL,
 PRIMARY KEY(root_key, relative_path)
);
CREATE TABLE IF NOT EXISTS folder_discovery_catalog (
 id SMALLINT PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL CHECK(revision >= 1), roles_json JSONB NOT NULL, analysis_types_json JSONB NOT NULL,
 updated_at TIMESTAMP NOT NULL, updated_by VARCHAR NOT NULL
);
INSERT INTO folder_discovery_catalog(id,revision,roles_json,analysis_types_json,updated_at,updated_by) VALUES
(1,1,
'[{"key":"PROJECT","label":"프로젝트","kind":"PROJECT","active": true},{"key":"REQUEST","label":"의뢰","kind":"REQUEST","active": true},{"key":"LOAD_CASE","label":"하중 경우","kind":"LOAD_CASE","active": true},{"key":"RESULTS","label":"결과 폴더","kind":"RESULTS","active": true},{"key":"INPUT","label":"입력 폴더","kind":"INPUT","active": true}]'::JSONB,
'[{"key":"DROP","label":"DROP","active": true},{"key":"SIDE_CLAMP","label":"SIDE_CLAMP","active": true},{"key":"SPDM_CMS","label":"SPDM_CMS","active": true},{"key":"SPDM_MODAL","label":"SPDM_MODAL","active": true},{"key":"SPDM_DEFLECTION","label":"SPDM_DEFLECTION","active": true},{"key":"SPDM_STIFFNESS","label":"SPDM_STIFFNESS","active": true},{"key":"SPDM_VIBRATION","label":"SPDM_VIBRATION","active": true}]'::JSONB,
CURRENT_TIMESTAMP,'system') ON CONFLICT(id) DO NOTHING;
CREATE TABLE IF NOT EXISTS folder_discovery_registry (
 id VARCHAR PRIMARY KEY, root_key VARCHAR(64) NOT NULL, relative_path VARCHAR(1024) NOT NULL, role VARCHAR(64) NOT NULL, role_kind VARCHAR(16) NOT NULL CHECK(role_kind IN ('PROJECT','REQUEST','LOAD_CASE','RESULTS','INPUT')), scope_key VARCHAR(128) NOT NULL,
 code VARCHAR(256), name VARCHAR(512) NOT NULL CHECK(length(trim(name)) > 0), analysis_type VARCHAR(128) NOT NULL DEFAULT '', parent_target_id VARCHAR(128), target_id VARCHAR(128) NOT NULL UNIQUE,
 created_at TIMESTAMP NOT NULL, UNIQUE(root_key, relative_path, role)
);
CREATE INDEX IF NOT EXISTS ix_folder_discovery_scans_root ON folder_discovery_scans(root_key, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_folder_discovery_registry_root_path ON folder_discovery_registry(root_key, relative_path);
CREATE UNIQUE INDEX IF NOT EXISTS ux_folder_discovery_registry_nonempty_code ON folder_discovery_registry(root_key,role_kind,scope_key,code) WHERE role_kind IN ('PROJECT','REQUEST') AND code IS NOT NULL AND length(trim(code)) > 0;
""".strip()
