# ADR 0002: PostgreSQL runtime bootstrap and API contract boundaries

- Status: accepted
- Date: 2026-08-14

## Decision

PostgreSQL is the production source of truth. Alembic exclusively owns its
schema changes; the application role must not create tables, alter tables, or
write default catalog data during startup. `initialize_database()` performs a
read-only required-table preflight in PostgreSQL mode. An operator applies
migrations using the owner role and then uses the app role for normal runtime.

The current idempotent fixture set is invoked explicitly with:

```bash
cd backend
ANALYSIS_DB_BACKEND=postgresql DATABASE_URL="$APP_DATABASE_URL" \
  ../.venv-wsl/bin/python scripts/seed_database.py --mode reference
```

`--mode demo` remains a compatibility alias for the same fixture set. It is
not a separate production reference catalogue yet, so it must not be treated
as production business data. A future production reference seed requires a
separate reviewed data source and acceptance criteria.

DuckDB retains its historic DDL/compatibility bootstrap only behind the local
development adapter. It is supported for single-process development and
migration input, not concurrent production runtime.

OpenAPI is the unique HTTP path/DTO snapshot. CI independently compares the
FastAPI schema with `frontend/openapi.json`; the frontend then generates the
typed source from that snapshot and rejects a resulting diff. Application and
domain code do not use OpenAPI DTOs as their domain model.

## Consequences

- Deployment cannot rely on startup side effects to repair missing schema or
  seed content. It fails closed and directs operators to Alembic/preflight.
- The current `reference` name describes fixture intent rather than a
  production-data guarantee. Demo/reference separation remains an explicit
  follow-up, not an implied completion.
- PostgreSQL connection pool settings are environment-configurable and checked
  against a declared server connection budget before deployment.
- Existing DuckDB fixtures stay behavior-compatible while their large legacy
  implementation is not mechanically moved during this refactor.
