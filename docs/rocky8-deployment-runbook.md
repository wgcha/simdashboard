# Rocky Linux 8 deployment runbook

This runbook describes the approved ordering. It does not grant authority to
install packages, modify systemd, change nginx, or access production secrets.
Use the reviewed templates under `deploy/rocky8` and the organisation's change
process for those privileged actions.

1. Build a release artifact with the pinned Python/Node runtimes and commit the
   OpenAPI snapshot/client. Run `deploy/rocky8/validate-templates.sh` in CI.
2. Render the systemd/nginx templates with an approved service identity,
   immutable release path, TLS configuration, and root-readable EnvironmentFile.
   The service EnvironmentFile contains only the app role `DATABASE_URL`; owner
   credentials stay in the migration/restore secret path.
3. With the owner role, execute `alembic upgrade head`, then run
   `harden_postgres_privileges.py` as prescribed by the PostgreSQL setup guide.
4. On a deliberate initial/reference install only, run
   `seed_database.py --mode reference` with the app role. This is the current
   deterministic fixture set; `--mode demo` is identical for compatibility and
   is not production business seed data.
5. With the app role, run `check_postgres_connection.py` and
   `check_postgres_pool_budget.py` with `UVICORN_WORKERS`,
   `POSTGRES_MAX_CONNECTIONS`, and reserved connections explicitly configured.
6. Activate the reviewed release and use the nginx-routed `/api/health` check.
   It must return `status=ok` and `database_backend=postgresql`.
7. For application rollback, repoint only to a release compatible with the
   current Alembic revision. Database downgrade/restore is a separate approved
   recovery operation using an owner credential and verified backup.

The production app must not use DuckDB. A missing PostgreSQL table or stale
Alembic revision is a deployment failure, not a startup repair opportunity.
