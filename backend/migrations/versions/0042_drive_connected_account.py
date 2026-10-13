"""SCX drive "연결 계정" (connected account) for re-login (issue #50, spec §6 decision 4).

One clear-text row (``id='scx'``) naming the shared drive account of the first
successful token registration or account login.  It is not a secret (the same
value is already kept in clear in ``drive_credentials.account_hint``) and it
survives deleting the credentials, so a token or login of a *different* account
is saved only after the admin confirms the change.  Additive only: no existing
table or row is changed; the table starts empty (the application falls back to
``drive_credentials.account_hint`` until the first registration after the
upgrade) and stays unused while ``SIMDASH_DRIVE_GATEWAY=none``.
"""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0042_drive_connected_account"
down_revision = "0041_folder_link_reservations"
branch_labels = None
depends_on = None

TABLES = ("drive_connected_account",)


def upgrade() -> None:
    op.execute(sa.text("""
    CREATE TABLE IF NOT EXISTS drive_connected_account (
        id VARCHAR(32) PRIMARY KEY CHECK (id = 'scx'),
        account_hint VARCHAR(200) NOT NULL,
        updated_by VARCHAR NOT NULL,
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
