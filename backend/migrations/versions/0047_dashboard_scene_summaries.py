"""Per-Scene value summaries of DISTRIBUTION captures (issue #55, stage 2).

* ``dashboard_scene_summaries``: one row per (capture, Run, Run option, Scene).  A DISTRIBUTION capture
  (recipe ``dashboard-v4``) no longer keeps every csv value as a JSON observation in
  ``dashboard_captures.payload_json`` (a Case reached PostgreSQL's 256 MB jsonb limit).  The capture header
  keeps the Run/Scene index and each Scene's csv list (asset ids); this table keeps, per Scene, the maxima
  the screens use (per component / basis / kind / position / line: value count, maximum, peak locations).
  Detail values are parsed on demand from the original csv bytes already stored in ``dashboard_assets``.

Additive only: no existing table or row is changed, and old captures keep their observations and are read
as before.  Rows are derived from their capture and are deleted with it (project cleanup, registration
deletion and reset); ``capture_id`` is a plain reference (no foreign key), like migration 0046.
"""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0047_dashboard_scene_summaries"
down_revision = "0046_materials_preparations"
branch_labels = None
depends_on = None

TABLES = ("dashboard_scene_summaries",)


def upgrade() -> None:
    op.execute(sa.text("""
    CREATE TABLE IF NOT EXISTS dashboard_scene_summaries (
        id VARCHAR(80) PRIMARY KEY,
        capture_id VARCHAR NOT NULL,
        scene_id VARCHAR NOT NULL,
        run_id VARCHAR NOT NULL,
        run_option_id VARCHAR NOT NULL,
        format_version INTEGER NOT NULL,
        observation_count INTEGER NOT NULL,
        summary_json TEXT NOT NULL,
        created_at TIMESTAMP NOT NULL,
        CONSTRAINT dashboard_scene_summaries_scene UNIQUE (capture_id, scene_id, run_id, run_option_id)
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
