"""Persistent link reservations for "폴더 구조 만들기" and unread-notification dedupe.

* ``folder_link_reservations``: a request folder whose skeleton "폴더 구조 만들기"
  (``result_folder_structure``) just created or queued for an existing dashboard
  request is reserved for that request, so the folder auto-discovery registers
  it as a LINK to the request instead of a new request.  One row per
  ``(root_key, path_key)`` (casefolded root-relative path); ``expires_at`` bounds
  it (24 h).  The row is removed when the link is registered, when the creation
  failed locally or when its drive batch failed/was cancelled, and when the
  project is deleted (project cleanup).  It used to live in process memory, which
  broke with several workers or a restart (review M3).
* ``notifications_unread_dedupe``: unique partial index on unread
  ``(user_id, dedupe_key)`` so two concurrent transactions cannot both insert the
  same unread notification (``INSERT ... ON CONFLICT DO NOTHING``).  Created only
  when the existing rows have no such duplicates; otherwise it is skipped with a
  NOTICE and the application keeps its lookup-based dedupe (no row is changed).

Additive only: no existing table or row is changed.  ``project_id``/``request_id``
are plain labels (no foreign key), like ``notifications`` (0040).
"""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0041_folder_link_reservations"
down_revision = "0040_notifications"
branch_labels = None
depends_on = None

TABLES = ("folder_link_reservations",)


def upgrade() -> None:
    op.execute(sa.text("""
    CREATE TABLE IF NOT EXISTS folder_link_reservations (
        root_key VARCHAR NOT NULL,
        path_key VARCHAR NOT NULL,
        project_id VARCHAR NOT NULL,
        request_id VARCHAR NOT NULL,
        environment VARCHAR(16) NOT NULL CHECK (environment IN ('USAGE', 'DISTRIBUTION')),
        created_by VARCHAR NOT NULL,
        created_at TIMESTAMP NOT NULL,
        expires_at TIMESTAMP NOT NULL,
        PRIMARY KEY (root_key, path_key)
    )
    """))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS folder_link_reservations_request "
        "ON folder_link_reservations (project_id, request_id, environment)"))
    op.execute(sa.text(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM notifications WHERE read_at IS NULL AND dedupe_key IS NOT NULL "
        "GROUP BY user_id, dedupe_key HAVING count(*) > 1) THEN "
        "CREATE UNIQUE INDEX IF NOT EXISTS notifications_unread_dedupe ON notifications (user_id, dedupe_key) "
        "WHERE read_at IS NULL AND dedupe_key IS NOT NULL; "
        "ELSE RAISE NOTICE 'notifications_unread_dedupe skipped: existing unread duplicates'; "
        "END IF; END $$;"
    ))
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
    op.execute(sa.text("DROP INDEX IF EXISTS notifications_unread_dedupe"))
    for table in reversed(TABLES):
        op.execute(sa.text(f"DROP TABLE IF EXISTS {table}"))
