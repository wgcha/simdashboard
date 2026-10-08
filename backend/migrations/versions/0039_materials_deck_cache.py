"""Parse-result cache for large solver input decks (usage-environment OptiStruct materials).

* ``materials_deck_cache``: one row per (root, file, solver) with the parsed
  materials of that file, the fingerprint it was parsed at (local size+mtime or
  drive ``version_token``), the INCLUDE files it read with their fingerprints,
  and the drive blob sha256 that can be reused while the version is unchanged.
  The background parse writes the row; reads re-parse only when a fingerprint
  or the parser version changes.

Additive only: no existing table or row is changed.  Rows are derived data (the
SPDM file is the source), carry no project/request ids, and may be deleted at
any time; the next view parses again.
"""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0039_materials_deck_cache"
down_revision = "0038_drive_write_path"
branch_labels = None
depends_on = None

TABLES = ("materials_deck_cache",)


def upgrade() -> None:
    op.execute(sa.text("""
    CREATE TABLE IF NOT EXISTS materials_deck_cache (
        id VARCHAR(80) PRIMARY KEY,
        root_key VARCHAR(64) NOT NULL,
        rel_path VARCHAR NOT NULL,
        solver VARCHAR(20) NOT NULL CHECK (solver IN ('OPTISTRUCT')),
        parser_version VARCHAR(40) NOT NULL,
        fingerprint VARCHAR(200) NOT NULL,
        size_bytes BIGINT,
        status VARCHAR(16) NOT NULL CHECK (status IN ('READY', 'FAILED')),
        deck_json TEXT,
        dependencies_json TEXT NOT NULL,
        blob_sha256 VARCHAR(64),
        error_code VARCHAR(80),
        error_message VARCHAR(400),
        parse_seconds DOUBLE PRECISION,
        created_at TIMESTAMP NOT NULL,
        updated_at TIMESTAMP NOT NULL,
        CONSTRAINT materials_deck_cache_file UNIQUE (root_key, rel_path, solver)
    )
    """))
    role = os.getenv("SIM_DASH_APP_ROLE", "simdashboard_app").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", role):
        raise ValueError("SIM_DASH_APP_ROLE is not a safe PostgreSQL identifier")
    for table in TABLES:
        op.execute(sa.text(
            f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN "
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO "{role}"; '
            "END IF; END $$;"
        ))


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(sa.text(f"DROP TABLE IF EXISTS {table}"))
