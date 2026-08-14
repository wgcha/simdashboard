# Rocky Linux 8 deployment template

This directory is a reviewable deployment contract, not an installer. It does
not create users, install packages, copy files, start services, or contact a
database. An operations owner must replace every `__REPLACE_*__` value, review
the rendered files, and perform the privileged actions under the organisation's
change-control process.

## Required runtime contract

- PostgreSQL 18 is the only production database. The service account uses the
  least-privilege app URL; the owner URL is restricted to Alembic/restore work.
- The release is immutable at `__REPLACE_RELEASE_ROOT__`; `current` is changed
  only after migration and preflight succeed.
- The API binds to loopback. nginx terminates TLS and proxies `/api/` and
  `/assets/`; it serves the built frontend directly.
- The EnvironmentFile is root-readable only and must contain `DATABASE_URL`,
  `ANALYSIS_DB_BACKEND=postgresql`, authentication settings, PostgreSQL pool
  settings, and no owner credential.
- Before activating a release, run Alembic with the owner credential, then
  `backend/scripts/check_postgres_connection.py` with the app credential.

## Review order

1. Render `simdashboard.service.template` and `nginx/simdashboard.conf.template`
   with organisation-specific paths, host name, certificate paths and service user.
2. Put the app EnvironmentFile outside the release tree with mode `0640`, owned
   by root and readable only by the service group. Do not put secrets in a unit
   file, Git, or shell arguments.
3. Build the frontend in CI and publish the exact immutable release artifact.
4. Apply migrations with the owner role. Execute `seed_database.py --mode reference`
   only for a deliberate initial/reference-data install, never as app startup.
5. Run the healthcheck through nginx and the PostgreSQL app-role preflight. Only
   then update the release symlink and restart the application under approved
   operational control.
6. Roll back application code by restoring the prior release target only when
   its Alembic revision is compatible. Database downgrade/restore is a separate,
   approved recovery operation using the owner role and verified backup.

`validate-templates.sh` is intentionally non-privileged: it only parses local
templates and checks that placeholders remain visible. It is safe for CI.
