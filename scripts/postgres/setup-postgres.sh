#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${project_root}/.venv-runtime/bin/python"
if [[ ! -x "${python_bin}" ]]; then python_bin="${project_root}/.venv/bin/python"; fi
if [[ ! -x "${python_bin}" ]]; then echo "Python virtual environment not found." >&2; exit 1; fi

for name in POSTGRES_ADMIN_URL SIM_DASH_OWNER_PASSWORD SIM_DASH_APP_PASSWORD DATABASE_URL; do
  if [[ -z "${!name:-}" ]]; then echo "${name} environment variable is required." >&2; exit 1; fi
done

"${python_bin}" "${project_root}/backend/scripts/bootstrap_postgres.py"
export ANALYSIS_DB_BACKEND=postgresql
(
  cd "${project_root}/backend"
  "${python_bin}" -m alembic -c alembic.ini upgrade head
  "${python_bin}" scripts/harden_postgres_privileges.py
  if [[ -n "${MIGRATE_DUCKDB_SOURCE:-}" ]]; then
    "${python_bin}" scripts/migrate_duckdb_to_postgres.py --source "${MIGRATE_DUCKDB_SOURCE}" --target-url "${DATABASE_URL}" --execute
  else
    "${python_bin}" scripts/seed_database.py
  fi
)

echo "PostgreSQL schema and initial data setup completed."
