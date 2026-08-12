"""Add password users and immutable API audit events.

Revision ID: 0002_security_audit
Revises: 0001_initial
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_security_audit"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS users (
            id VARCHAR PRIMARY KEY,
            username VARCHAR NOT NULL UNIQUE,
            password_hash VARCHAR NOT NULL,
            display_name VARCHAR NOT NULL,
            role VARCHAR NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL,
            CONSTRAINT ck_users_role CHECK (role IN ('viewer', 'editor', 'admin'))
        )
    """))
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS audit_events (
            id VARCHAR PRIMARY KEY,
            occurred_at TIMESTAMP NOT NULL,
            user_id VARCHAR,
            username VARCHAR,
            role VARCHAR,
            action VARCHAR NOT NULL,
            method VARCHAR NOT NULL,
            path VARCHAR NOT NULL,
            status_code INTEGER NOT NULL,
            request_id VARCHAR NOT NULL,
            client_ip VARCHAR,
            user_agent VARCHAR,
            detail_json JSONB
        )
    """))
    # Fresh installations bootstrap from the current canonical schema, where
    # the legacy role column has already been renamed. Historical upgrades
    # still have `role` and need the original constraint.
    user_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "role" in user_columns:
        op.execute(sa.text("""
            DO $$
            BEGIN
                ALTER TABLE users ADD CONSTRAINT ck_users_role CHECK (role IN ('viewer', 'editor', 'admin'));
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$
        """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_audit_events_occurred_at ON audit_events (occurred_at DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_audit_events_user_id ON audit_events (user_id, occurred_at DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_audit_events_path ON audit_events (path, occurred_at DESC)"))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS audit_events"))
    op.execute(sa.text("DROP TABLE IF EXISTS users"))
