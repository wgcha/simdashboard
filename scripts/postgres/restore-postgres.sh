#!/usr/bin/env bash
set -euo pipefail
[[ $# -ge 2 ]] || { echo "usage: restore-postgres.sh BACKUP CONFIRM_DATABASE [APP_ROLE_VERIFY_URL] [--clean]" >&2; exit 2; }
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON="$PROJECT_ROOT/.venv/bin/python"
[[ -x "$PYTHON" ]] || { echo "Python virtual environment not found." >&2; exit 1; }
: "${DATABASE_URL:?DATABASE_URL environment variable is required}"
BACKUP="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
confirm_database="$2"
shift 2
verify_database_url="${SIMDASH_APP_DATABASE_URL:-}"
if [[ $# -gt 0 && "$1" == --verify-database-url ]]; then
  [[ $# -gt 1 ]] || { echo "--verify-database-url requires a value." >&2; exit 2; }
  verify_database_url="$2"
  shift 2
elif [[ $# -gt 0 && "$1" != --* ]]; then
  verify_database_url="$1"
  shift
fi
[[ -n "$verify_database_url" ]] || {
  echo "APP_ROLE_VERIFY_URL or SIMDASH_APP_DATABASE_URL is required." >&2
  exit 2
}
exec "$PYTHON" "$PROJECT_ROOT/backend/scripts/restore_postgres.py" "$BACKUP" \
  --confirm-database "$confirm_database" --verify-database-url "$verify_database_url" "$@"
