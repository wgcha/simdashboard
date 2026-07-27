from __future__ import annotations

import os

import psycopg
from psycopg import sql


def main() -> None:
    database_url = os.getenv("DATABASE_URL")
    app_role = os.getenv("SIM_DASH_APP_ROLE", "simdashboard_app")
    if not database_url:
        raise RuntimeError("owner DATABASE_URL이 필요합니다.")
    with psycopg.connect(database_url.replace("postgresql+psycopg://", "postgresql://"), autocommit=True) as connection:
        connection.execute(sql.SQL("GRANT SELECT, INSERT ON TABLE audit_events TO {}").format(sql.Identifier(app_role)))
        connection.execute(sql.SQL("REVOKE UPDATE, DELETE, TRUNCATE ON TABLE audit_events FROM {}").format(sql.Identifier(app_role)))
    print(f"감사 로그 append-only 권한 적용 완료: role={app_role}")


if __name__ == "__main__":
    main()
