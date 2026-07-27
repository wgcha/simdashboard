from __future__ import annotations

import argparse
import os

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} 환경변수가 필요합니다.")
    return value


def role_exists(connection: psycopg.Connection, role: str) -> bool:
    return connection.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", [role]).fetchone() is not None


def main() -> None:
    parser = argparse.ArgumentParser(description="Create portable PostgreSQL roles and database for Analysis Canvas.")
    parser.add_argument("--database", default=os.getenv("SIM_DASH_DATABASE", "simulation_dashboard"))
    parser.add_argument("--owner-role", default=os.getenv("SIM_DASH_OWNER_ROLE", "simdashboard_owner"))
    parser.add_argument("--app-role", default=os.getenv("SIM_DASH_APP_ROLE", "simdashboard_app"))
    args = parser.parse_args()

    admin_url = required_env("POSTGRES_ADMIN_URL")
    owner_password = required_env("SIM_DASH_OWNER_PASSWORD")
    app_password = required_env("SIM_DASH_APP_PASSWORD")
    admin_parts = conninfo_to_dict(admin_url.replace("postgresql+psycopg://", "postgresql://"))

    with psycopg.connect(admin_url.replace("postgresql+psycopg://", "postgresql://"), autocommit=True) as admin:
        if role_exists(admin, args.owner_role):
            admin.execute(sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(sql.Identifier(args.owner_role), sql.Literal(owner_password)))
        else:
            admin.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(args.owner_role), sql.Literal(owner_password)))
        if role_exists(admin, args.app_role):
            admin.execute(sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(sql.Identifier(args.app_role), sql.Literal(app_password)))
        else:
            admin.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(args.app_role), sql.Literal(app_password)))
        database_exists = admin.execute("SELECT 1 FROM pg_database WHERE datname=%s", [args.database]).fetchone()
        if not database_exists:
            admin.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(args.database), sql.Identifier(args.owner_role)))
        else:
            admin.execute(sql.SQL("ALTER DATABASE {} OWNER TO {}").format(sql.Identifier(args.database), sql.Identifier(args.owner_role)))

    target_url = make_conninfo(**{**admin_parts, "dbname": args.database})
    with psycopg.connect(target_url, autocommit=True) as target:
        target.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(args.database), sql.Identifier(args.app_role)))
        target.execute(sql.SQL("ALTER SCHEMA public OWNER TO {}").format(sql.Identifier(args.owner_role)))
        target.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(args.app_role)))
        target.execute(sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}").format(sql.Identifier(args.owner_role), sql.Identifier(args.app_role)))
        target.execute(sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {}").format(sql.Identifier(args.owner_role), sql.Identifier(args.app_role)))
        target.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}").format(sql.Identifier(args.app_role)))
        target.execute(sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(sql.Identifier(args.app_role)))

    print(f"PostgreSQL 준비 완료: database={args.database}, owner={args.owner_role}, app={args.app_role}")
    print("비밀번호는 출력하지 않았습니다. owner DATABASE_URL로 Alembic을 실행한 뒤 app DATABASE_URL로 서비스를 실행하세요.")


if __name__ == "__main__":
    main()
