"""SCX drive shared-account credentials (stage D0, integration 04 §2.1).

One encrypted row (``id='scx'``) holding the Fernet-encrypted TokenBundle JSON.
``generation`` fences worker token saves: every admin registration writes a
new, larger value and a worker save only updates the generation it loaded.
Additive only: no existing table or row is changed.  The table is empty after
the upgrade and stays unused while ``SIMDASH_DRIVE_GATEWAY=none``.
"""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0036_drive_credentials"
down_revision = "0035_folder_registration_delete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
    CREATE TABLE IF NOT EXISTS drive_credentials (
        id VARCHAR(32) PRIMARY KEY CHECK (id = 'scx'),
        ciphertext BYTEA NOT NULL,
        key_id VARCHAR(16) NOT NULL,
        account_hint VARCHAR(200),
        obtained_at TIMESTAMP NOT NULL,
        updated_by VARCHAR NOT NULL,
        updated_at TIMESTAMP NOT NULL,
        generation BIGINT NOT NULL
    )
    """))
    role = os.getenv("SIM_DASH_APP_ROLE", "simdashboard_app").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", role):
        raise ValueError("SIM_DASH_APP_ROLE is not a safe PostgreSQL identifier")
    op.execute(sa.text(
        f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN "
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON drive_credentials TO "{role}"; '
        "END IF; END $$;"
    ))


def downgrade() -> None:
    raise RuntimeError("drive_credentials holds the only copy of the rotated SCX refresh token; downgrade is not supported.")
