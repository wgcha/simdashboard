"""Read-only service-start gate; schema writes belong to deployment after backup."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import database_settings
from scripts.upgrade_postgres_schema import StartupMigrationError, inspect_app_revision


def main() -> int:
    settings = database_settings()
    if settings.backend != "postgresql":
        return 0
    if not settings.database_url:
        print("POSTGRES_SCHEMA_CHECK_FAILED APP_DATABASE_URL_MISSING", file=sys.stderr)
        return 2
    try:
        # Verify history before catalog so older legitimate schemas get the
        # actionable deployment instruction instead of a missing-table error.
        state = inspect_app_revision(settings.database_url, verify_catalog=False)
        if state.pending:
            print(
                "POSTGRES_SCHEMA_UPDATE_REQUIRED: Run deploy.bat/update.bat or the "
                "offline installer to back up and upgrade before starting.",
                file=sys.stderr,
            )
            return 3
        inspect_app_revision(settings.database_url, verify_catalog=True)
    except StartupMigrationError as error:
        print(f"POSTGRES_SCHEMA_CHECK_FAILED {error}", file=sys.stderr)
        return 1
    print("POSTGRES_SCHEMA_CURRENT")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
