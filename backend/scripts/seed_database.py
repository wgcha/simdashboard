from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import database_settings
from app.database import initialize_database, seed_current_database
from app.database_connection import connect


def main() -> None:
    settings = database_settings()
    if settings.backend != "postgresql":
        raise RuntimeError("PostgreSQL seed는 ANALYSIS_DB_BACKEND=postgresql에서만 실행합니다.")
    initialize_database()
    seed_current_database()
    with connect() as conn:
        counts = {
            "projects": conn.execute("SELECT count(*) FROM projects").fetchone()[0],
            "requests": conn.execute("SELECT count(*) FROM analysis_requests").fetchone()[0],
            "load_cases": conn.execute("SELECT count(*) FROM load_cases").fetchone()[0],
            "runs": conn.execute("SELECT count(*) FROM analysis_runs").fetchone()[0],
        }
    print("PostgreSQL seed 완료:", ", ".join(f"{key}={value}" for key, value in counts.items()))


if __name__ == "__main__":
    os.environ.setdefault("ANALYSIS_DB_BACKEND", "postgresql")
    main()
