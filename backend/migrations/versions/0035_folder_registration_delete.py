"""Allow administrators to delete folder environment registrations (tombstones).

Contract: docs/contracts/depth-schema.md §13.4.

* ``folder_environment_registrations.status`` additionally accepts ``DELETED``.
* New nullable columns ``deleted_at``, ``deleted_by`` and ``created_targets``
  (JSON text: ``{project_ids, request_ids, case_ids}`` actually inserted by the
  registration). Existing rows keep ``NULL`` and use ownership inference.
* The application role receives ``DELETE`` on the registration-owned tables
  that were previously insert-only. No existing row is rewritten or removed.
"""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0035_folder_registration_delete"
down_revision = "0034_folder_depth_schema"
branch_labels = None
depends_on = None

STATUSES = ("REGISTERED", "CAPTURING", "COMPLETED", "FAILED", "DELETED")
DELETE_TABLES = (
    "folder_environment_registry", "folder_environment_capture_jobs",
    "dashboard_cases", "dashboard_captures", "dashboard_assets",
)


def upgrade() -> None:
    allowed = ",".join(f"'{status}'" for status in STATUSES)
    op.execute(sa.text(
        "ALTER TABLE folder_environment_registrations "
        "DROP CONSTRAINT IF EXISTS folder_environment_registrations_status_check"
    ))
    op.execute(sa.text(
        "ALTER TABLE folder_environment_registrations ADD CONSTRAINT folder_environment_registrations_status_check "
        f"CHECK (status IN ({allowed}))"
    ))
    op.execute(sa.text(
        "ALTER TABLE folder_environment_registrations "
        "ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP NULL, "
        "ADD COLUMN IF NOT EXISTS deleted_by VARCHAR NULL, "
        "ADD COLUMN IF NOT EXISTS created_targets TEXT NULL"
    ))
    role = os.getenv("SIM_DASH_APP_ROLE", "simdashboard_app").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", role):
        raise ValueError("SIM_DASH_APP_ROLE is not a safe PostgreSQL identifier")
    tables = ",".join(DELETE_TABLES)
    op.execute(sa.text(
        f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN "
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON {tables} TO "{role}"; '
        f'GRANT UPDATE ON folder_environment_scans, folder_environment_registrations TO "{role}"; '
        "END IF; END $$;"
    ))


def downgrade() -> None:
    raise RuntimeError("Deleted registrations are durable audit tombstones; downgrade is not supported.")
