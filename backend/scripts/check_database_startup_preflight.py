"""Validate the selected database before deployment or service startup.

This intentionally performs no network or database write.  The caller runs the
PostgreSQL application-role connection check afterwards when PostgreSQL is
configured.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import database_settings


def _explicit_backend() -> str | None:
    # app.config's dotenv loading only places an explicit file value in the
    # process environment; it never writes the implicit PostgreSQL default.
    import os

    return os.getenv("ANALYSIS_DB_BACKEND")


def main() -> int:
    settings = database_settings()
    explicit_backend = _explicit_backend()
    if explicit_backend is None and settings.duckdb_path.is_file():
        print(
            "LEGACY_DUCKDB_BACKEND_SELECTION_REQUIRED: Existing DuckDB data was found but ANALYSIS_DB_BACKEND was not explicitly selected. "
            "Set ANALYSIS_DB_BACKEND=duckdb to keep using the existing data, or complete a verified PostgreSQL migration and explicitly set "
            "ANALYSIS_DB_BACKEND=postgresql before deploy/start. PostgreSQL가 기본값으로 선택되더라도 기존 DuckDB 데이터를 자동으로 "
            "무시하거나 이전하지 않습니다. 기존 데이터를 유지하려면 ANALYSIS_DB_BACKEND=duckdb를 명시하고, PostgreSQL을 사용하려면 "
            "검증된 이전을 완료한 뒤 ANALYSIS_DB_BACKEND=postgresql을 명시하십시오.",
            file=sys.stderr,
        )
        return 3
    if settings.backend == "postgresql" and not (settings.database_url and settings.database_url.strip()):
        print(
            "POSTGRESQL_DATABASE_URL_REQUIRED: PostgreSQL is selected but DATABASE_URL is not configured. "
            "Configure the PostgreSQL service, database, application DATABASE_URL, and .postgres-owner.env, then rerun deploy/start. "
            "For initial provisioning or a DuckDB migration, follow docs/windows-postgresql-quickstart.md. "
            "PostgreSQL가 선택되었지만 DATABASE_URL이 없습니다. PostgreSQL 서버·데이터베이스·소유자/앱 역할과 "
            "DATABASE_URL 및 .postgres-owner.env를 설정한 뒤 deploy/start를 다시 실행하십시오. 초기 구성 또는 DuckDB 이전은 "
            "docs/windows-postgresql-quickstart.md를 따르십시오. DuckDB로 자동 전환하지 않습니다.",
            file=sys.stderr,
        )
        return 2
    print(f"DATABASE_STARTUP_PREFLIGHT_OK backend={settings.backend}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
