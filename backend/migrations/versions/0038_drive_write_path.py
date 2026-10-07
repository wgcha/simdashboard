"""SCX drive write path (stage D3, integration 04 §2.3–2.5, 05 §5–6).

* ``drive_upload_queue``: one row per drive write step (MKDIR, COPY, FILE,
  COMPLETE_MARKER) grouped in batches; executed in ``seq`` order by the
  single background worker with ``mkdirs``/``copy_within``/``upload_new`` only
  (never delete, move or overwrite on the drive).
* ``drive_locks``: DB locks that replace lock files on the drive (Final per request).
* ``finalization_operations``: Final plan/report/completion metadata that the
  local mode keeps in ``Final/.finalizations/<op>/`` (scx mode cannot replace files).

Additive only: no existing table or row is changed.  The tables stay empty while
``SIMDASH_DRIVE_GATEWAY=none``.
"""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0038_drive_write_path"
down_revision = "0037_drive_source_versions"
branch_labels = None
depends_on = None

TABLES = ("drive_upload_queue", "drive_locks", "finalization_operations")


def upgrade() -> None:
    op.execute(sa.text("""
    CREATE TABLE IF NOT EXISTS drive_upload_queue (
        id VARCHAR(80) PRIMARY KEY,
        batch_id VARCHAR(80) NOT NULL,
        seq INTEGER NOT NULL CHECK (seq >= 0),
        kind VARCHAR(20) NOT NULL CHECK (kind IN ('FILE', 'MKDIR', 'COPY', 'COMPLETE_MARKER')),
        root_key VARCHAR(64) NOT NULL,
        staging_path VARCHAR,
        src_rel VARCHAR,
        src_version_token VARCHAR,
        dst_rel_dir VARCHAR NOT NULL,
        dst_name VARCHAR,
        size_bytes BIGINT,
        sha256 VARCHAR(64),
        state VARCHAR(16) NOT NULL CHECK (state IN ('PENDING', 'RUNNING', 'DONE', 'CONFLICT', 'FAILED', 'BLOCKED', 'CANCELLED')),
        halt_on_error BOOLEAN NOT NULL DEFAULT TRUE,
        attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt_at TIMESTAMP,
        last_error_code VARCHAR(80),
        last_error_msg VARCHAR(400),
        result_item_id VARCHAR,
        result_size BIGINT,
        result_sha1 VARCHAR(40),
        transfer_method VARCHAR(20),
        requested_by VARCHAR NOT NULL,
        origin VARCHAR(40) NOT NULL,
        origin_ref VARCHAR(80),
        project_id VARCHAR,
        request_id VARCHAR,
        environment VARCHAR(16),
        created_at TIMESTAMP NOT NULL,
        updated_at TIMESTAMP NOT NULL,
        finished_at TIMESTAMP,
        CONSTRAINT drive_upload_queue_batch_seq UNIQUE (batch_id, seq)
    )
    """))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS drive_upload_queue_ready ON drive_upload_queue (state, next_attempt_at)"))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS drive_upload_queue_origin ON drive_upload_queue (origin, origin_ref)"))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS drive_upload_queue_request ON drive_upload_queue (project_id, request_id)"))
    op.execute(sa.text("""
    CREATE TABLE IF NOT EXISTS drive_locks (
        scope VARCHAR(400) PRIMARY KEY,
        owner VARCHAR(80) NOT NULL,
        acquired_at TIMESTAMP NOT NULL,
        expires_at TIMESTAMP NOT NULL
    )
    """))
    op.execute(sa.text("""
    CREATE TABLE IF NOT EXISTS finalization_operations (
        operation_id VARCHAR(32) PRIMARY KEY,
        root_key VARCHAR(64) NOT NULL,
        project_id VARCHAR NOT NULL,
        request_id VARCHAR NOT NULL,
        environment VARCHAR(16) NOT NULL,
        case_id VARCHAR NOT NULL,
        capture_id VARCHAR NOT NULL,
        status VARCHAR(16) NOT NULL CHECK (status IN ('PLANNED', 'STAGED', 'PUBLISHING', 'COMPLETE', 'FAILED')),
        plan_json VARCHAR NOT NULL,
        reports_json VARCHAR,
        report_formats VARCHAR,
        upload_batch_id VARCHAR(80),
        complete_json VARCHAR,
        complete_sha256 VARCHAR(64),
        confirmed_at VARCHAR(40),
        designation_seq INTEGER,
        designation_batch_id VARCHAR(80),
        error_code VARCHAR(80),
        error_message VARCHAR(400),
        created_by VARCHAR NOT NULL,
        confirmed_by VARCHAR,
        created_at TIMESTAMP NOT NULL,
        queued_at TIMESTAMP,
        updated_at TIMESTAMP NOT NULL,
        CONSTRAINT finalization_operations_designation UNIQUE (project_id, request_id, designation_seq)
    )
    """))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS finalization_operations_request "
        "ON finalization_operations (project_id, request_id, environment)"))
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
