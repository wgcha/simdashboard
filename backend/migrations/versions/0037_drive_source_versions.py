"""SCX drive source versions (stage D2, integration 04 §2.2, 05 §4).

One row per registered version of a drive source file below the SPDM root:
the version the dashboard applied (``version_token``: sha1 when the drive
listing has it, else size + modification time), a pending newer drive version
waiting for user confirmation, and the source state (PRESENT / CHANGED /
MISSING).  Additive only: no existing table or row is changed.  The table stays
empty while ``SIMDASH_DRIVE_GATEWAY=none``.
"""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0037_drive_source_versions"
down_revision = "0036_drive_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
    CREATE TABLE IF NOT EXISTS drive_source_versions (
        id VARCHAR(80) PRIMARY KEY,
        root_key VARCHAR(64) NOT NULL,
        rel_path VARCHAR NOT NULL,
        version_no INTEGER NOT NULL CHECK (version_no >= 1),
        project_id VARCHAR,
        request_id VARCHAR,
        item_id VARCHAR,
        size_bytes BIGINT,
        modified_at TIMESTAMP,
        sha1 VARCHAR(40),
        sha256 VARCHAR(64),
        version_token VARCHAR NOT NULL,
        content_stored BOOLEAN NOT NULL DEFAULT FALSE,
        stored_path VARCHAR,
        pending_version_token VARCHAR,
        pending_size_bytes BIGINT,
        pending_modified_at TIMESTAMP,
        pending_sha1 VARCHAR(40),
        pending_item_id VARCHAR,
        pending_detected_at TIMESTAMP,
        review_state VARCHAR(16) NOT NULL DEFAULT 'NONE' CHECK (review_state IN ('NONE', 'PENDING', 'IGNORED')),
        reviewed_by VARCHAR,
        reviewed_at TIMESTAMP,
        registered_at TIMESTAMP NOT NULL,
        registered_by VARCHAR,
        superseded_at TIMESTAMP,
        source_state VARCHAR(16) NOT NULL CHECK (source_state IN ('PRESENT', 'CHANGED', 'MISSING')),
        last_checked_at TIMESTAMP,
        CONSTRAINT drive_source_versions_version_unique UNIQUE (root_key, rel_path, version_no)
    )
    """))
    op.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS drive_source_versions_current "
        "ON drive_source_versions (root_key, rel_path) WHERE superseded_at IS NULL"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS drive_source_versions_scope "
        "ON drive_source_versions (root_key, project_id, request_id) WHERE superseded_at IS NULL"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS drive_source_versions_attention "
        "ON drive_source_versions (root_key) WHERE superseded_at IS NULL "
        "AND (review_state <> 'NONE' OR source_state = 'MISSING')"
    ))
    role = os.getenv("SIM_DASH_APP_ROLE", "simdashboard_app").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", role):
        raise ValueError("SIM_DASH_APP_ROLE is not a safe PostgreSQL identifier")
    op.execute(sa.text(
        f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN "
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON drive_source_versions TO "{role}"; '
        "END IF; END $$;"
    ))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS drive_source_versions"))
