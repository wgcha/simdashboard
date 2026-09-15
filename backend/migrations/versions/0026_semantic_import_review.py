"""Persist unresolved semantic refresh files for explicit review/import."""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0026_semantic_import_review"
down_revision = "0025_semantic_vocabulary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS semantic_import_review_items (
          id VARCHAR PRIMARY KEY, binding_id VARCHAR NOT NULL REFERENCES semantic_folder_bindings(id),
          binding_revision INTEGER NOT NULL CHECK(binding_revision >= 1), load_case_id VARCHAR NOT NULL REFERENCES load_cases(id),
          relative_path VARCHAR(1024) NOT NULL, source_sha256 CHAR(64), source_size BIGINT,
          scan_status VARCHAR(16) NOT NULL CHECK(scan_status IN ('UNMAPPED','AMBIGUOUS','INVALID','PENDING')),
          review_state VARCHAR(16) NOT NULL CHECK(review_state IN ('OPEN','SELECTED','READY','STALE','IMPORTED','SKIPPED')),
          candidates_json JSONB NOT NULL, selected_recipe_id VARCHAR, selected_recipe_version INTEGER,
          template_id VARCHAR, template_version INTEGER, validated_sha256 CHAR(64), validation_summary_json JSONB,
          error_json JSONB, revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
          previous_confirmed_analysis_run_id VARCHAR REFERENCES analysis_runs(id),
          confirmed_analysis_run_id VARCHAR REFERENCES analysis_runs(id),
          created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL, created_by VARCHAR NOT NULL, updated_by VARCHAR NOT NULL,
          validated_at TIMESTAMP, validated_by VARCHAR, confirmed_at TIMESTAMP, confirmed_by VARCHAR,
          UNIQUE(binding_id, load_case_id, relative_path),
          CHECK((selected_recipe_id IS NULL) = (selected_recipe_version IS NULL)),
          CHECK((template_id IS NULL) = (template_version IS NULL))
        );
        CREATE INDEX IF NOT EXISTS ix_semantic_review_binding_updated ON semantic_import_review_items(binding_id, updated_at DESC);
        CREATE INDEX IF NOT EXISTS ix_semantic_review_load_case_state ON semantic_import_review_items(load_case_id, review_state, updated_at DESC);
        CREATE TABLE IF NOT EXISTS semantic_import_review_events (
          id VARCHAR PRIMARY KEY, review_item_id VARCHAR NOT NULL REFERENCES semantic_import_review_items(id),
          old_state VARCHAR, new_state VARCHAR NOT NULL, revision INTEGER NOT NULL,
          prior_run_id VARCHAR REFERENCES analysis_runs(id), current_run_id VARCHAR REFERENCES analysis_runs(id),
          detail_json JSONB NOT NULL, occurred_at TIMESTAMP NOT NULL, actor VARCHAR NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_semantic_review_events_item ON semantic_import_review_events(review_item_id, occurred_at DESC);
    """))
    app_role = os.getenv("SIM_DASH_APP_ROLE", "simdashboard_app").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", app_role): raise ValueError("SIM_DASH_APP_ROLE is not a safe PostgreSQL identifier")
    op.execute(sa.text(f'''DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{app_role}') THEN
      GRANT SELECT, INSERT, UPDATE, DELETE ON semantic_import_review_items, semantic_import_review_events TO "{app_role}";
    END IF; END $$;'''))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS semantic_import_review_events"))
    op.execute(sa.text("DROP TABLE IF EXISTS semantic_import_review_items"))
