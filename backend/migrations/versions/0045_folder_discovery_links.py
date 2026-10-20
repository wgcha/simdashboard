"""Project folder links and administrator folder exclusions of the automatic discovery (issue #55).

* ``folder_project_links``: a project-level folder (per the depth schema) linked to a project
  without any registered request.  The automatic discovery adds a project for every project-level
  folder, also when none of its requests can be registered yet (``PROJ-000002_testProject`` whose
  requests all lack 사용/유통).  Folders whose names differ only by whitespace are the same project
  (``name_key``): one row per spelling, all with the same ``project_id``.  A project with a
  registered request is linked by the registration's ``PROJECT`` registry row as before.  The row
  is removed when the project is deleted (project cleanup).
* ``folder_discovery_exclusions``: request folders an administrator excluded from the rescan result
  ([저장 폴더 새로고침] "제외"); discovery lists them as ``ADMIN_EXCLUDED`` and never registers them.
  Removing the row ("다시 포함") makes the folder a candidate again.

Additive only: no existing table or row is changed.  ``project_id`` is a plain label (no foreign
key), like ``folder_link_reservations`` (0041).
"""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0045_folder_discovery_links"
down_revision = "0044_scx_relay_submissions"
branch_labels = None
depends_on = None

TABLES = ("folder_project_links", "folder_discovery_exclusions")


def upgrade() -> None:
    op.execute(sa.text("""
    CREATE TABLE IF NOT EXISTS folder_project_links (
        root_key VARCHAR NOT NULL,
        relative_path VARCHAR NOT NULL,
        path_key VARCHAR NOT NULL,
        project_id VARCHAR NOT NULL,
        created_by VARCHAR NOT NULL,
        created_at TIMESTAMP NOT NULL,
        PRIMARY KEY (root_key, relative_path)
    )
    """))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS folder_project_links_project ON folder_project_links (project_id)"))
    op.execute(sa.text("""
    CREATE TABLE IF NOT EXISTS folder_discovery_exclusions (
        root_key VARCHAR NOT NULL,
        path_key VARCHAR NOT NULL,
        relative_path VARCHAR NOT NULL,
        excluded_by VARCHAR NOT NULL,
        excluded_at TIMESTAMP NOT NULL,
        PRIMARY KEY (root_key, path_key)
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
    op.execute(sa.text("DROP INDEX IF EXISTS folder_project_links_project"))
    for table in reversed(TABLES):
        op.execute(sa.text(f"DROP TABLE IF EXISTS {table}"))
