#!/usr/bin/env bash
set -euo pipefail
[[ $# -ge 2 ]] || { echo "usage: restore-postgres.sh BACKUP CONFIRM_DATABASE [--clean]" >&2; exit 2; }
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON="$PROJECT_ROOT/.venv/bin/python"
[[ -x "$PYTHON" ]] || { echo "Python virtual environment not found." >&2; exit 1; }
: "${DATABASE_URL:?DATABASE_URL environment variable is required}"
BACKUP="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
exec "$PYTHON" "$PROJECT_ROOT/backend/scripts/restore_postgres.py" "$BACKUP" --confirm-database "$2" "${@:3}"
