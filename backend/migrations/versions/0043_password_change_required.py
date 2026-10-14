"""Forced password change after an administrator reset (user decision 2026-10-14).

Additive only: ``users.must_change_password`` (default false) and ``users.sessions_revoked_at``
(nullable; sign-in tokens issued before it are refused, so a reset locks out stolen sessions). Existing accounts are unchanged; an
administrator's [비밀번호 초기화] sets it with a one-time temporary password and the user must choose
a new password before using the dashboard.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0043_password_change_required"
down_revision = "0042_drive_connected_account"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT FALSE"))
    op.execute(sa.text("ALTER TABLE users ADD COLUMN IF NOT EXISTS sessions_revoked_at TIMESTAMP"))


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE users DROP COLUMN IF EXISTS sessions_revoked_at"))
    op.execute(sa.text("ALTER TABLE users DROP COLUMN IF EXISTS must_change_password"))
