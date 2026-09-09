from __future__ import annotations

import duckdb


def ensure_managed_local_execution_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Install the embedded equivalent of the managed-device PostgreSQL schema."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS managed_device_bindings (
            id VARCHAR PRIMARY KEY,
            device_id VARCHAR NOT NULL,
            host_name VARCHAR NOT NULL,
            user_id VARCHAR NOT NULL,
            secret_hash VARCHAR NOT NULL,
            created_at TIMESTAMP NOT NULL,
            revoked_at TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS managed_device_pairing_tokens (
            token_hash VARCHAR PRIMARY KEY,
            user_id VARCHAR NOT NULL,
            device_id VARCHAR NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            consumed_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL
        );
        CREATE TABLE IF NOT EXISTS managed_device_sessions (
            token_hash VARCHAR PRIMARY KEY,
            binding_id VARCHAR NOT NULL,
            user_id VARCHAR NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP NOT NULL
        );
        CREATE TABLE IF NOT EXISTS managed_device_grants (
            id VARCHAR PRIMARY KEY,
            binding_id VARCHAR NOT NULL,
            user_id VARCHAR NOT NULL,
            action VARCHAR NOT NULL,
            request_id VARCHAR NOT NULL,
            work_item_id VARCHAR NOT NULL,
            task_name VARCHAR NOT NULL,
            actor VARCHAR NOT NULL,
            issued_at TIMESTAMP NOT NULL,
            CHECK (action IN ('execute', 'retry'))
        );
        CREATE TABLE IF NOT EXISTS managed_local_runs (
            id VARCHAR PRIMARY KEY,
            binding_id VARCHAR NOT NULL,
            grant_id VARCHAR NOT NULL,
            actor_user_id VARCHAR NOT NULL,
            request_id VARCHAR NOT NULL,
            work_item_id VARCHAR NOT NULL,
            task_name VARCHAR NOT NULL,
            run_json JSON NOT NULL,
            immutable_hash VARCHAR NOT NULL,
            last_sequence BIGINT NOT NULL,
            created_at TIMESTAMP NOT NULL,
            synced_at TIMESTAMP NOT NULL
        );
        CREATE TABLE IF NOT EXISTS managed_device_event_sequences (
            binding_id VARCHAR NOT NULL,
            run_id VARCHAR NOT NULL,
            sequence BIGINT NOT NULL,
            event_hash VARCHAR NOT NULL,
            accepted_at TIMESTAMP NOT NULL,
            PRIMARY KEY (binding_id, run_id, sequence)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_managed_bindings_user_active ON managed_device_bindings(user_id, revoked_at, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_managed_pairing_expiry ON managed_device_pairing_tokens(expires_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_managed_sessions_binding_expiry ON managed_device_sessions(binding_id, expires_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_managed_grants_binding_context ON managed_device_grants(binding_id, request_id, work_item_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_managed_runs_context ON managed_local_runs(request_id, work_item_id, synced_at)")
