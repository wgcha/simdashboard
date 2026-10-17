"""SCX relay submissions for PhysicsAI (docs/features/scx-relay.md §6).

* ``scx_relay_submissions``: one row per Radioss submission request of the relay
  (``request_id`` from PhysicsAI).  The row is written ``SUBMITTING`` before the adapter call
  and records the outcome; the same ``request_id`` replays the row instead of submitting
  again.  The application never deletes rows (the grant mirrors the other drive tables and the
  bootstrap default privileges).

Additive only: no existing table or row is changed.  The table stays empty while
``SIMDASH_SCX_RELAY_ENABLED`` is off (default).
"""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0044_scx_relay_submissions"
down_revision = "0043_password_change_required"
branch_labels = None
depends_on = None

TABLES = ("scx_relay_submissions",)


def upgrade() -> None:
    op.execute(sa.text("""
    CREATE TABLE IF NOT EXISTS scx_relay_submissions (
        request_id VARCHAR(128) PRIMARY KEY,
        request_sha256 VARCHAR(64) NOT NULL,
        client VARCHAR(32) NOT NULL,
        client_ref VARCHAR(4096),
        input_rel VARCHAR(1024) NOT NULL,
        job_dir_rel VARCHAR(1024) NOT NULL,
        state VARCHAR(16) NOT NULL CHECK (state IN ('SUBMITTING', 'SUBMITTED', 'UNKNOWN', 'FAILED', 'ATTACHED', 'ABANDONED')),
        job_id VARCHAR(128),
        solver VARCHAR(64),
        last_status VARCHAR(128),
        output_path VARCHAR(1024),
        output_rel VARCHAR(1024),
        error_code VARCHAR(80),
        error_message VARCHAR(200),
        created_at TIMESTAMP NOT NULL,
        updated_at TIMESTAMP NOT NULL,
        resolved_at TIMESTAMP,
        CONSTRAINT scx_relay_submissions_job_id UNIQUE (job_id)
    )
    """))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS scx_relay_submissions_state ON scx_relay_submissions (state)"))
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
