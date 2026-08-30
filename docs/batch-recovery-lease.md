# Batch recovery lease release gate

The recovery lease foundation only claims, renews, and releases ownership of a
previously-created queued DEMO_ONLY batch attempt. It does not schedule work,
invoke a runner, finalize an attempt, or change a public status.

DuckDB coverage is restricted to its serialized single-connection development
adapter. Before any multi-worker recovery capability is released, apply Alembic
head to a production-like PostgreSQL database and verify concurrent claims,
expiry takeover, stale renewal, and stale release from separate connections.

This foundation is not wired to a router, scheduler, worker, or state
transition. Future finalization must compare-and-swap the active owner, opaque
token, and generation in the same transaction as its state write; before that
work, this is not production recovery authority.

An active retry with the same owner and opaque token is idempotent, including
when it repeats the initial expected generation. It returns the original lease
without extending expiry or incrementing generation. Once that token expires it
cannot be used to claim again; takeover requires a new opaque token and the
current expected generation. The token must therefore remain internal and is
removed from every public and administrator projection.

Before a DuckDB-to-PostgreSQL transfer, active leases (`recovery_lease_token`
is non-null) must be zero. The transfer tool must also order or stage the
bidirectional `workflow_runs.batch_attempt_id` / `batch_execution_attempts.workflow_run_id`
identity and copy `batch_dispatches.attempt_id` only after its attempt exists;
its relationship audit must cover that dispatch link. Enforcing those transfer
preflights and cyclic-FK copy semantics is a separate operational release-gate
task; this ownership-only slice does not broaden the transfer workflow.
