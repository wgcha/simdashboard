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

The DuckDB-to-PostgreSQL transfer tool now fail-closes when any recovery lease
owner/token/acquired/expiry metadata remains (including expired or malformed
rows), or when only part of the five lease columns exists. It records that
preflight in the manifest, takes one source snapshot, NULL-stages the five
reverse immediate-FK references, restores them with compare-and-swap updates
inside the target transaction, and commits only after the full checksum match.
The same blockers make a read-only dry-run exit non-zero before any PostgreSQL
connection, after an explicitly requested manifest or JSON report is emitted.
Execute mode also verifies the target table/column contract and required lease
constraints/indexes before its first INSERT.
Actual PostgreSQL live migration and app-role verification remain release gates;
this ownership-only slice is still not production recovery authority.
