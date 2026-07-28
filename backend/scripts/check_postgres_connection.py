from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.exc import OperationalError

from app.database import initialize_database


def _sqlstate(error: BaseException) -> str | None:
    original = getattr(error, "orig", error)
    return getattr(original, "sqlstate", None)


def main() -> int:
    try:
        initialize_database()
    except OperationalError as exc:
        state = _sqlstate(exc)
        message = str(exc).lower()
        if state == "28P01" or "password" in message:
            print("[ERROR] PostgreSQL rejected the simdashboard_app password.", file=sys.stderr)
            print("Make the password in .env DATABASE_URL match the PostgreSQL role password.", file=sys.stderr)
            print("If this is the first setup, run scripts\\postgres\\setup-postgres.ps1.", file=sys.stderr)
        elif state == "3D000":
            print("[ERROR] The PostgreSQL database does not exist.", file=sys.stderr)
            print("Run scripts\\postgres\\setup-postgres.ps1 first.", file=sys.stderr)
        elif state in {"28000", "42704"}:
            print("[ERROR] The PostgreSQL login role is not available.", file=sys.stderr)
            print("Run scripts\\postgres\\setup-postgres.ps1 first.", file=sys.stderr)
        elif state and state.startswith("08"):
            print("[ERROR] Could not reach the PostgreSQL server.", file=sys.stderr)
            print("Check the PostgreSQL service, host, and port in DATABASE_URL.", file=sys.stderr)
        else:
            print(f"[ERROR] PostgreSQL connection failed (SQLSTATE: {state or 'unknown'}).", file=sys.stderr)
            print("Check DATABASE_URL and the PostgreSQL server log.", file=sys.stderr)
        return 1
    except RuntimeError:
        print("[ERROR] The PostgreSQL schema is not ready.", file=sys.stderr)
        print("Run Alembic migrations with the database owner account first.", file=sys.stderr)
        return 1

    print("PostgreSQL connection and schema check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
