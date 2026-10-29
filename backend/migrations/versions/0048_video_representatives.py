"""Representative video per (Case, Scene) and the per-project keyword rule (issue #55, GUI update).

* ``case_scene_video_choices``: the video a user chose as the representative of one Scene of one Case.  The key
  is ``(case_id, scene_key)``: ``case_id`` is the stable ``dashboard_cases.id`` and ``scene_key`` is
  ``<run id>|<run option id>|<Scene folder name, case-folded>`` -- both survive a re-capture (capture and asset
  ids do not).  ``relative_path`` is the chosen file's Case-relative path; when that file is gone the screens
  fall back to the default (rule, then first file name).
* ``project_video_rules``: one ordered keyword list per project (``keywords_json``, a JSON array of strings).
  The first video whose file name contains a keyword is the default representative, before the alphabetical
  fallback; a user's explicit choice still wins.

Additive only; no existing table or row is changed.  Both tables are derived from their Case / project and are
deleted with it (project cleanup, registration deletion and reset); ids are plain references (no foreign key),
like migrations 0046 and 0047.
"""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0048_video_representatives"
down_revision = "0047_dashboard_scene_summaries"
branch_labels = None
depends_on = None

TABLES = ("case_scene_video_choices", "project_video_rules")


def upgrade() -> None:
    op.execute(sa.text("""
    CREATE TABLE IF NOT EXISTS case_scene_video_choices (
        id VARCHAR(80) PRIMARY KEY,
        case_id VARCHAR NOT NULL,
        scene_key VARCHAR NOT NULL,
        relative_path VARCHAR NOT NULL,
        chosen_by VARCHAR,
        chosen_at TIMESTAMP NOT NULL,
        CONSTRAINT case_scene_video_choices_scene UNIQUE (case_id, scene_key)
    )
    """))
    op.execute(sa.text("""
    CREATE TABLE IF NOT EXISTS project_video_rules (
        project_id VARCHAR PRIMARY KEY,
        keywords_json TEXT NOT NULL,
        updated_by VARCHAR,
        updated_at TIMESTAMP NOT NULL
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
