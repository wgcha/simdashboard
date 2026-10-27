"""Materials prepared ahead of time per request Scene (issue #55, SCX drive: 소재물성 from the DB).

* ``materials_preparations``: one row per (root, request, environment, Scene folder).  The background
  materials pass (``services.materials_preparation``, run by the capture worker after the Case results)
  lists the Scene's deck folders, downloads the decks (distribution: Radioss ``.rad``/``.inc`` with their
  ``/INCLUDE`` files; usage: the OptiStruct ``.fem``), parses them and stores the result here
  (distribution: ``deck_json``; usage: the parse is in ``materials_deck_cache`` and this row names the
  file and its version).  ``fingerprint`` is built from the drive version tokens of the deck folders'
  listings and of every file read, so a changed drive version prepares the Scene again.  The 소재물성
  screen reads only these rows (``status`` READY / PENDING = 준비 중 / FAILED / NO_DECK).

Additive only: no existing table or row is changed.  Rows are derived data (the SPDM files are the
source) and may be deleted at any time; the next pass prepares again.  ``project_id``/``request_id``
are plain labels (no foreign key) removed with their project/request by the project cleanup.
"""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0046_materials_preparations"
down_revision = "0045_folder_discovery_links"
branch_labels = None
depends_on = None

TABLES = ("materials_preparations",)


def upgrade() -> None:
    op.execute(sa.text("""
    CREATE TABLE IF NOT EXISTS materials_preparations (
        id VARCHAR(80) PRIMARY KEY,
        root_key VARCHAR(64) NOT NULL,
        project_id VARCHAR NOT NULL,
        request_id VARCHAR NOT NULL,
        environment VARCHAR(16) NOT NULL CHECK (environment IN ('DISTRIBUTION', 'USAGE')),
        scene_key VARCHAR NOT NULL,
        relative_path VARCHAR NOT NULL,
        status VARCHAR(16) NOT NULL CHECK (status IN ('PENDING', 'READY', 'NO_DECK', 'FAILED')),
        needs_check BOOLEAN NOT NULL DEFAULT FALSE,
        fingerprint VARCHAR(80),
        parser_version VARCHAR(40),
        source_rel_path VARCHAR,
        files_json TEXT NOT NULL,
        warnings_json TEXT NOT NULL,
        deck_json TEXT,
        error_code VARCHAR(80),
        error_message VARCHAR(400),
        attempts INTEGER NOT NULL DEFAULT 0,
        checked_at TIMESTAMP,
        prepared_at TIMESTAMP,
        created_at TIMESTAMP NOT NULL,
        updated_at TIMESTAMP NOT NULL,
        CONSTRAINT materials_preparations_scene UNIQUE (root_key, request_id, environment, scene_key)
    )
    """))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS materials_preparations_work ON materials_preparations (status, needs_check)"))
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
    op.execute(sa.text("DROP INDEX IF EXISTS materials_preparations_work"))
    for table in reversed(TABLES):
        op.execute(sa.text(f"DROP TABLE IF EXISTS {table}"))
