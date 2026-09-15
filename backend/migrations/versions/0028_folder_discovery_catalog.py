"""Make folder-discovery roles and analysis types administrator-managed."""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op


revision = "0028_folder_catalog"
down_revision = "0027_folder_discovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS folder_discovery_catalog (
          id SMALLINT PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL CHECK(revision >= 1),
          roles_json JSONB NOT NULL, analysis_types_json JSONB NOT NULL,
          updated_at TIMESTAMP NOT NULL, updated_by VARCHAR NOT NULL
        );
        INSERT INTO folder_discovery_catalog(id,revision,roles_json,analysis_types_json,updated_at,updated_by)
        VALUES(1,1,'[{"key":"PROJECT","label":"프로젝트","kind":"PROJECT","active": true},{"key":"REQUEST","label":"의뢰","kind":"REQUEST","active": true},{"key":"LOAD_CASE","label":"하중 경우","kind":"LOAD_CASE","active": true},{"key":"RESULTS","label":"결과 폴더","kind":"RESULTS","active": true},{"key":"INPUT","label":"입력 폴더","kind":"INPUT","active": true}]'::JSONB,
        '[{"key":"DROP","label":"DROP","active": true},{"key":"SIDE_CLAMP","label":"SIDE_CLAMP","active": true},{"key":"SPDM_CMS","label":"SPDM_CMS","active": true},{"key":"SPDM_MODAL","label":"SPDM_MODAL","active": true},{"key":"SPDM_DEFLECTION","label":"SPDM_DEFLECTION","active": true},{"key":"SPDM_STIFFNESS","label":"SPDM_STIFFNESS","active": true},{"key":"SPDM_VIBRATION","label":"SPDM_VIBRATION","active": true}]'::JSONB,CURRENT_TIMESTAMP,'system') ON CONFLICT(id) DO NOTHING;
        ALTER TABLE folder_discovery_previews ADD COLUMN IF NOT EXISTS catalog_revision INTEGER NOT NULL DEFAULT 1;
        ALTER TABLE folder_discovery_registry ADD COLUMN IF NOT EXISTS role_kind VARCHAR(16);
        UPDATE folder_discovery_registry SET role_kind=role WHERE role_kind IS NULL;
        ALTER TABLE folder_discovery_registry ALTER COLUMN role_kind SET NOT NULL;
        ALTER TABLE folder_discovery_registry ALTER COLUMN role TYPE VARCHAR(64);
        ALTER TABLE folder_discovery_registry ALTER COLUMN code DROP NOT NULL;
    """))
    # 0027 used generated PostgreSQL names. Guard each removal so deployments
    # from an earlier hand-built schema can still proceed.
    op.execute(sa.text("""
        ALTER TABLE folder_discovery_registry DROP CONSTRAINT IF EXISTS folder_discovery_registry_role_check;
        ALTER TABLE folder_discovery_registry DROP CONSTRAINT IF EXISTS folder_discovery_registry_code_check;
        ALTER TABLE folder_discovery_registry DROP CONSTRAINT IF EXISTS folder_discovery_registry_root_key_role_scope_key_code_key;
        DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='folder_discovery_registry_role_kind_check' AND conrelid='folder_discovery_registry'::regclass) THEN
          ALTER TABLE folder_discovery_registry ADD CONSTRAINT folder_discovery_registry_role_kind_check CHECK(role_kind IN ('PROJECT','REQUEST','LOAD_CASE','RESULTS','INPUT'));
        END IF; END $$;
        CREATE UNIQUE INDEX IF NOT EXISTS ux_folder_discovery_registry_nonempty_code
          ON folder_discovery_registry(root_key,role_kind,scope_key,code) WHERE role_kind IN ('PROJECT','REQUEST') AND code IS NOT NULL AND length(trim(code)) > 0;
    """))
    role = os.getenv("SIM_DASH_APP_ROLE", "simdashboard_app").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", role):
        raise ValueError("SIM_DASH_APP_ROLE is not a safe PostgreSQL identifier")
    op.execute(sa.text(f'''DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN
      GRANT SELECT, INSERT, UPDATE, DELETE ON folder_discovery_catalog TO "{role}";
    END IF; END $$;'''))


def downgrade() -> None:
    raise RuntimeError("0028_folder_catalog cannot be downgraded safely after catalog metadata has been recorded.")
