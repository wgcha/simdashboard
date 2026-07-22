from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DatabaseSettings:
    backend: str
    duckdb_path: Path
    database_url: str | None


def database_settings() -> DatabaseSettings:
    backend = os.getenv("ANALYSIS_DB_BACKEND", "duckdb").strip().lower()
    if backend not in {"duckdb", "postgresql"}:
        raise RuntimeError("ANALYSIS_DB_BACKEND은 duckdb 또는 postgresql이어야 합니다.")
    default_path = Path(__file__).resolve().parents[1] / "data" / "analysis_dashboard.duckdb"
    path = Path(os.getenv("ANALYSIS_DUCKDB_PATH", str(default_path))).expanduser().resolve()
    return DatabaseSettings(backend=backend, duckdb_path=path, database_url=os.getenv("DATABASE_URL"))
