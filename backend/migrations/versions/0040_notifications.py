"""Per-user notifications (bell icon and notifications page).

* ``notifications``: one row per recipient user.  Rows are derived, user-facing
  messages written in the same transaction as the event they describe (drive
  queue pause, upload batch outcome, Final completion/failure, Final summary
  repair needed, drive source changes/MISSING sources, new results from the
  auto-sync, registration capture failures).  ``dedupe_key`` coalesces repeats
  while an earlier copy is still unread; ``read_at`` marks a read row.
  Retention (90 days, 1,000 rows per user) is enforced by the application on
  insert and read; ``docs/features/notifications.md``.

Additive only: no existing table or row is changed.  ``project_id``/``request_id``
are plain labels (no foreign key); a project cleanup deletes the rows of the
deleted project/request.
"""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0040_notifications"
down_revision = "0039_materials_deck_cache"
branch_labels = None
depends_on = None

TABLES = ("notifications",)


def upgrade() -> None:
    op.execute(sa.text("""
    CREATE TABLE IF NOT EXISTS notifications (
        id VARCHAR(64) PRIMARY KEY,
        user_id VARCHAR NOT NULL,
        type VARCHAR(40) NOT NULL,
        severity VARCHAR(16) NOT NULL CHECK (severity IN ('INFO', 'SUCCESS', 'WARNING', 'ERROR')),
        title VARCHAR(200) NOT NULL,
        body VARCHAR(1000),
        link VARCHAR(1000),
        project_id VARCHAR,
        request_id VARCHAR,
        dedupe_key VARCHAR(300),
        created_at TIMESTAMP NOT NULL,
        read_at TIMESTAMP
    )
    """))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS notifications_user_created ON notifications (user_id, created_at)"))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS notifications_user_dedupe ON notifications (user_id, dedupe_key)"))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS notifications_scope ON notifications (project_id, request_id)"))
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
