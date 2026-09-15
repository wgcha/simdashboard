"""PostgreSQL bootstrap DDL for semantic migrations 0024 through 0026.

The DuckDB development initializer has intentionally looser equivalents in
``app.database``.  This PostgreSQL-only definition is therefore kept beside
the schema exporter, just like its other PostgreSQL-only table and constraint
definitions.  Alembic revisions 0024--0026 remain the upgrade path for
existing databases.
"""

SEMANTIC_DDL = """
CREATE TABLE IF NOT EXISTS semantic_result_items (
    id VARCHAR PRIMARY KEY, key VARCHAR NOT NULL UNIQUE, latest_version INTEGER NOT NULL,
    active_version INTEGER, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL, updated_by VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS semantic_result_item_versions (
    item_id VARCHAR NOT NULL, version INTEGER NOT NULL, definition_json JSONB NOT NULL, item_snapshot_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    lifecycle_status VARCHAR NOT NULL, created_at TIMESTAMP NOT NULL, created_by VARCHAR NOT NULL,
    PRIMARY KEY(item_id, version)
);
CREATE TABLE IF NOT EXISTS semantic_recipes (
    id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL, latest_version INTEGER NOT NULL,
    active_version INTEGER, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL, updated_by VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS semantic_recipe_versions (
    recipe_id VARCHAR NOT NULL, version INTEGER NOT NULL, definition_json JSONB NOT NULL, item_snapshot_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    lifecycle_status VARCHAR NOT NULL, sample_sha256 CHAR(64), sample_filename VARCHAR, sample_bytes BYTEA,
    created_at TIMESTAMP NOT NULL, created_by VARCHAR NOT NULL, PRIMARY KEY(recipe_id, version)
);
CREATE TABLE IF NOT EXISTS semantic_templates (
    id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL, latest_version INTEGER NOT NULL,
    active_version INTEGER, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL, updated_by VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS semantic_template_versions (
    template_id VARCHAR NOT NULL, version INTEGER NOT NULL, definition_json JSONB NOT NULL, item_snapshot_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    lifecycle_status VARCHAR NOT NULL, created_at TIMESTAMP NOT NULL, created_by VARCHAR NOT NULL,
    PRIMARY KEY(template_id, version)
);
CREATE TABLE IF NOT EXISTS semantic_folder_bindings (
    id VARCHAR PRIMARY KEY, project_id VARCHAR NOT NULL, request_id VARCHAR, load_case_id VARCHAR,
    relative_path VARCHAR NOT NULL UNIQUE, role VARCHAR NOT NULL, recipe_ids_json JSONB NOT NULL,
    template_id VARCHAR, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL, created_by VARCHAR NOT NULL, revision INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS semantic_import_provenance (
    analysis_run_id VARCHAR PRIMARY KEY, load_case_id VARCHAR NOT NULL, recipe_id VARCHAR NOT NULL,
    recipe_version INTEGER NOT NULL, template_id VARCHAR, template_version INTEGER, source_name VARCHAR NOT NULL,
    source_sha256 CHAR(64) NOT NULL, observations_json JSONB NOT NULL, sample_bytes BYTEA, created_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_semantic_folder_bindings_project ON semantic_folder_bindings(project_id, load_case_id);
CREATE INDEX IF NOT EXISTS ix_semantic_import_provenance_lookup ON semantic_import_provenance(load_case_id, recipe_id, recipe_version);
CREATE TABLE IF NOT EXISTS semantic_vocabulary_entries (
    id VARCHAR PRIMARY KEY, key VARCHAR NOT NULL CHECK(key ~ '^[a-z][a-z0-9_]{1,127}$'),
    label VARCHAR(200) NOT NULL, description VARCHAR(4000) NOT NULL DEFAULT '',
    target_kind VARCHAR(20) NOT NULL CHECK(target_kind IN ('FOLDER_ROLE', 'PROJECT', 'REQUEST', 'LOAD_CASE', 'RESULT_ITEM')),
    target_id VARCHAR(200) NOT NULL, scope_project_id VARCHAR(200) REFERENCES projects(id), aliases_json JSONB NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1), enabled BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL, created_by VARCHAR NOT NULL, updated_by VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS semantic_vocabulary_terms (
    entry_id VARCHAR NOT NULL REFERENCES semantic_vocabulary_entries(id) ON DELETE CASCADE,
    scope_key VARCHAR(200) NOT NULL, target_kind VARCHAR(20) NOT NULL,
    normalized_term VARCHAR(200) NOT NULL, PRIMARY KEY(scope_key, target_kind, normalized_term)
);
CREATE INDEX IF NOT EXISTS ix_semantic_vocabulary_entries_scope ON semantic_vocabulary_entries(scope_project_id, target_kind, enabled);
CREATE TABLE IF NOT EXISTS semantic_import_review_items (
  id VARCHAR PRIMARY KEY, binding_id VARCHAR NOT NULL REFERENCES semantic_folder_bindings(id),
  binding_revision INTEGER NOT NULL CHECK(binding_revision >= 1), load_case_id VARCHAR NOT NULL REFERENCES load_cases(id),
  relative_path VARCHAR(1024) NOT NULL, source_sha256 CHAR(64), source_size BIGINT,
  scan_status VARCHAR(16) NOT NULL CHECK(scan_status IN ('UNMAPPED','AMBIGUOUS','INVALID','PENDING')),
  review_state VARCHAR(16) NOT NULL CHECK(review_state IN ('OPEN','SELECTED','READY','STALE','IMPORTED','SKIPPED')),
  candidates_json JSONB NOT NULL, selected_recipe_id VARCHAR, selected_recipe_version INTEGER,
  template_id VARCHAR, template_version INTEGER, validated_sha256 CHAR(64), validation_summary_json JSONB,
  error_json JSONB, revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
  previous_confirmed_analysis_run_id VARCHAR REFERENCES analysis_runs(id), confirmed_analysis_run_id VARCHAR REFERENCES analysis_runs(id),
  created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL, created_by VARCHAR NOT NULL, updated_by VARCHAR NOT NULL,
  validated_at TIMESTAMP, validated_by VARCHAR, confirmed_at TIMESTAMP, confirmed_by VARCHAR,
  UNIQUE(binding_id, load_case_id, relative_path), CHECK((selected_recipe_id IS NULL) = (selected_recipe_version IS NULL)),
  CHECK((template_id IS NULL) = (template_version IS NULL))
);
CREATE INDEX IF NOT EXISTS ix_semantic_review_binding_updated ON semantic_import_review_items(binding_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS ix_semantic_review_load_case_state ON semantic_import_review_items(load_case_id, review_state, updated_at DESC);
CREATE TABLE IF NOT EXISTS semantic_import_review_events (
 id VARCHAR PRIMARY KEY, review_item_id VARCHAR NOT NULL REFERENCES semantic_import_review_items(id), old_state VARCHAR, new_state VARCHAR NOT NULL,
 revision INTEGER NOT NULL, prior_run_id VARCHAR REFERENCES analysis_runs(id), current_run_id VARCHAR REFERENCES analysis_runs(id), detail_json JSONB NOT NULL, occurred_at TIMESTAMP NOT NULL, actor VARCHAR NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_semantic_review_events_item ON semantic_import_review_events(review_item_id, occurred_at DESC);
""".strip()
