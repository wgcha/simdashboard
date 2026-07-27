from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit


@dataclass(frozen=True)
class PostgresTarget:
    host: str
    port: int
    username: str
    password: str | None
    database: str


def parse_target(database_url: str) -> PostgresTarget:
    normalized = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    parsed = urlsplit(normalized)
    if parsed.scheme != "postgresql" or not parsed.hostname or not parsed.username or not parsed.path.strip("/"):
        raise RuntimeError("DATABASE_URL은 사용자·호스트·DB명이 포함된 PostgreSQL URL이어야 합니다.")
    return PostgresTarget(
        parsed.hostname,
        parsed.port or 5432,
        unquote(parsed.username),
        unquote(parsed.password) if parsed.password else None,
        unquote(parsed.path.strip("/")),
    )


def executable(name: str) -> str:
    postgres_bin = os.getenv("POSTGRES_BIN")
    suffix = ".exe" if os.name == "nt" else ""
    if postgres_bin:
        candidate = Path(postgres_bin).expanduser().resolve() / f"{name}{suffix}"
        if candidate.is_file():
            return str(candidate)
    found = shutil.which(name)
    if found:
        return found
    raise RuntimeError(f"{name} 실행 파일을 찾지 못했습니다. POSTGRES_BIN에 PostgreSQL bin 폴더를 지정하세요.")


def command_env(target: PostgresTarget) -> dict[str, str]:
    environment = os.environ.copy()
    if target.password:
        environment["PGPASSWORD"] = target.password
    return environment


def connection_args(target: PostgresTarget) -> list[str]:
    return ["--host", target.host, "--port", str(target.port), "--username", target.username, "--dbname", target.database]
