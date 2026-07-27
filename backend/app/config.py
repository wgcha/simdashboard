from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / "backend" / ".env")


@dataclass(frozen=True)
class DatabaseSettings:
    backend: str
    duckdb_path: Path
    database_url: str | None


@dataclass(frozen=True)
class SecuritySettings:
    auth_mode: str
    secret_key: str | None
    token_ttl_minutes: int
    cors_allowed_origins: tuple[str, ...]
    cookie_secure: bool


def database_settings() -> DatabaseSettings:
    backend = os.getenv("ANALYSIS_DB_BACKEND", "duckdb").strip().lower()
    if backend not in {"duckdb", "postgresql"}:
        raise RuntimeError("ANALYSIS_DB_BACKEND은 duckdb 또는 postgresql이어야 합니다.")
    default_path = Path(__file__).resolve().parents[1] / "data" / "analysis_dashboard.duckdb"
    path = Path(os.getenv("ANALYSIS_DUCKDB_PATH", str(default_path))).expanduser().resolve()
    return DatabaseSettings(backend=backend, duckdb_path=path, database_url=os.getenv("DATABASE_URL"))


def security_settings() -> SecuritySettings:
    auth_mode = os.getenv("AUTH_MODE", "disabled").strip().lower()
    if auth_mode not in {"disabled", "password"}:
        raise RuntimeError("AUTH_MODE은 disabled 또는 password여야 합니다.")
    secret_key = os.getenv("AUTH_SECRET_KEY")
    if auth_mode == "password" and (not secret_key or len(secret_key) < 32):
        raise RuntimeError("AUTH_MODE=password일 때 32자 이상의 AUTH_SECRET_KEY가 필요합니다.")
    try:
        token_ttl_minutes = int(os.getenv("AUTH_TOKEN_TTL_MINUTES", "480"))
    except ValueError as exc:
        raise RuntimeError("AUTH_TOKEN_TTL_MINUTES는 정수여야 합니다.") from exc
    if not 5 <= token_ttl_minutes <= 10080:
        raise RuntimeError("AUTH_TOKEN_TTL_MINUTES는 5~10080분이어야 합니다.")
    origins = tuple(
        origin.strip()
        for origin in os.getenv("CORS_ALLOWED_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173").split(",")
        if origin.strip()
    )
    cookie_secure = os.getenv("AUTH_COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes", "on"}
    return SecuritySettings(auth_mode, secret_key, token_ttl_minutes, origins, cookie_secure)
