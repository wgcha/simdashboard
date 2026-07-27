#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON="$PROJECT_ROOT/.venv/bin/python"
[[ -x "$PYTHON" ]] || { echo "Python virtual environment not found." >&2; exit 1; }
: "${DATABASE_URL:?DATABASE_URL environment variable is required}"
if [[ $# -gt 0 ]]; then
  exec "$PYTHON" "$PROJECT_ROOT/backend/scripts/backup_postgres.py" --output-dir "$1"
fi
exec "$PYTHON" "$PROJECT_ROOT/backend/scripts/backup_postgres.py"
