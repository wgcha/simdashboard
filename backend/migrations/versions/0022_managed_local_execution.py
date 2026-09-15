"""Add account-bound managed local execution bindings and central run history."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0022_managed_local_execution"
down_revision = "0021_modeling_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS managed_device_bindings (
            id VARCHAR PRIMARY KEY,
            device_id VARCHAR NOT NULL,
            host_name VARCHAR NOT NULL,
            user_id VARCHAR NOT NULL REFERENCES users(id),
            secret_hash CHAR(64) NOT NULL CHECK (secret_hash ~ '^[0-9a-f]{64}$'),
            created_at TIMESTAMPTZ NOT NULL,
            revoked_at TIMESTAMPTZ
        )
    """))
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS managed_device_pairing_tokens (
            token_hash CHAR(64) PRIMARY KEY CHECK (token_hash ~ '^[0-9a-f]{64}$'),
            user_id VARCHAR NOT NULL REFERENCES users(id),
            device_id VARCHAR NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            consumed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL
        )
    """))
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS managed_device_sessions (
            token_hash CHAR(64) PRIMARY KEY CHECK (token_hash ~ '^[0-9a-f]{64}$'),
            binding_id VARCHAR NOT NULL REFERENCES managed_device_bindings(id),
            user_id VARCHAR NOT NULL REFERENCES users(id),
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
    """))
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS managed_device_grants (
            id VARCHAR PRIMARY KEY,
            binding_id VARCHAR NOT NULL REFERENCES managed_device_bindings(id),
            user_id VARCHAR NOT NULL REFERENCES users(id),
            action VARCHAR NOT NULL CHECK (action IN ('execute', 'retry')),
            request_id VARCHAR NOT NULL REFERENCES analysis_requests(id),
            work_item_id VARCHAR NOT NULL REFERENCES request_work_items(id),
            task_name VARCHAR NOT NULL,
            actor VARCHAR NOT NULL,
            issued_at TIMESTAMPTZ NOT NULL
        )
    """))
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS managed_local_runs (
            id VARCHAR PRIMARY KEY,
            binding_id VARCHAR NOT NULL REFERENCES managed_device_bindings(id),
            grant_id VARCHAR NOT NULL REFERENCES managed_device_grants(id),
            actor_user_id VARCHAR NOT NULL REFERENCES users(id),
            request_id VARCHAR NOT NULL REFERENCES analysis_requests(id),
            work_item_id VARCHAR NOT NULL REFERENCES request_work_items(id),
            task_name VARCHAR NOT NULL,
            run_json JSONB NOT NULL,
            immutable_hash CHAR(64) NOT NULL CHECK (immutable_hash ~ '^[0-9a-f]{64}$'),
            last_sequence BIGINT NOT NULL CHECK (last_sequence >= 0),
            created_at TIMESTAMPTZ NOT NULL,
            synced_at TIMESTAMPTZ NOT NULL
        )
    """))
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS managed_device_event_sequences (
            binding_id VARCHAR NOT NULL REFERENCES managed_device_bindings(id),
            run_id VARCHAR NOT NULL REFERENCES managed_local_runs(id),
            sequence BIGINT NOT NULL CHECK (sequence >= 0),
            event_hash CHAR(64) NOT NULL CHECK (event_hash ~ '^[0-9a-f]{64}$'),
            accepted_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (binding_id, run_id, sequence)
        )
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_managed_bindings_user_active ON managed_device_bindings(user_id, revoked_at, created_at)"))
    op.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS ux_managed_active_binding_per_device ON managed_device_bindings(user_id, device_id) WHERE revoked_at IS NULL"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_managed_pairing_expiry ON managed_device_pairing_tokens(expires_at)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_managed_sessions_binding_expiry ON managed_device_sessions(binding_id, expires_at)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_managed_grants_binding_context ON managed_device_grants(binding_id, request_id, work_item_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_managed_runs_context ON managed_local_runs(request_id, work_item_id, synced_at)"))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE managed_device_event_sequences"))
    op.execute(sa.text("DROP TABLE managed_local_runs"))
    op.execute(sa.text("DROP TABLE managed_device_grants"))
    op.execute(sa.text("DROP TABLE managed_device_sessions"))
    op.execute(sa.text("DROP TABLE managed_device_pairing_tokens"))
    op.execute(sa.text("DROP TABLE managed_device_bindings"))
