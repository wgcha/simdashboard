from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

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
    oidc_issuer_url: str | None
    oidc_client_id: str | None
    oidc_client_secret: str | None
    oidc_redirect_uri: str | None
    oidc_scopes: str
    oidc_employee_id_claim: str
    oidc_username_claim: str
    oidc_display_name_claim: str
    oidc_department_claim: str
    oidc_job_title_claim: str
    oidc_login_success_url: str
    oidc_login_failure_url: str
    oidc_timeout_seconds: float
    oidc_allow_insecure_localhost: bool


@dataclass(frozen=True)
class DirectorySettings:
    mode: str
    base_url: str | None
    search_path: str
    token: str | None
    timeout_seconds: float
    result_limit: int
    allow_insecure_localhost: bool


def database_settings() -> DatabaseSettings:
    backend = os.getenv("ANALYSIS_DB_BACKEND", "duckdb").strip().lower()
    if backend not in {"duckdb", "postgresql"}:
        raise RuntimeError("ANALYSIS_DB_BACKEND은 duckdb 또는 postgresql이어야 합니다.")
    default_path = Path(__file__).resolve().parents[1] / "data" / "analysis_dashboard.duckdb"
    path = Path(os.getenv("ANALYSIS_DUCKDB_PATH", str(default_path))).expanduser().resolve()
    return DatabaseSettings(backend=backend, duckdb_path=path, database_url=os.getenv("DATABASE_URL"))


def security_settings() -> SecuritySettings:
    auth_mode = os.getenv("AUTH_MODE", "disabled").strip().lower()
    if auth_mode not in {"disabled", "password", "oidc"}:
        raise RuntimeError("AUTH_MODE은 disabled, password 또는 oidc여야 합니다.")
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
    oidc_allow_insecure_localhost = os.getenv("OIDC_ALLOW_INSECURE_LOCALHOST", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if auth_mode == "oidc":
        required = {
            "OIDC_ISSUER_URL": os.getenv("OIDC_ISSUER_URL"),
            "OIDC_CLIENT_ID": os.getenv("OIDC_CLIENT_ID"),
            "OIDC_CLIENT_SECRET": os.getenv("OIDC_CLIENT_SECRET"),
            "OIDC_REDIRECT_URI": os.getenv("OIDC_REDIRECT_URI"),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(f"AUTH_MODE=oidc 필수 환경변수가 없습니다: {', '.join(missing)}")
        if not secret_key or len(secret_key) < 32:
            raise RuntimeError("AUTH_MODE=oidc일 때 32자 이상의 AUTH_SECRET_KEY가 필요합니다.")
        issuer_url = str(required["OIDC_ISSUER_URL"]).rstrip("/")
        redirect_uri = str(required["OIDC_REDIRECT_URI"])

        def is_insecure_localhost(url: str) -> bool:
            parsed = urlparse(url)
            return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}

        for name, url in (("OIDC_ISSUER_URL", issuer_url), ("OIDC_REDIRECT_URI", redirect_uri)):
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise RuntimeError(f"{name}은 절대 HTTP(S) URL이어야 합니다.")
            if parsed.scheme != "https" and not (oidc_allow_insecure_localhost and is_insecure_localhost(url)):
                raise RuntimeError(f"{name}은 HTTPS여야 합니다. 로컬 테스트만 OIDC_ALLOW_INSECURE_LOCALHOST=true를 사용하세요.")
        if not cookie_secure and not (
            oidc_allow_insecure_localhost
            and is_insecure_localhost(issuer_url)
            and is_insecure_localhost(redirect_uri)
        ):
            raise RuntimeError("AUTH_MODE=oidc 운영 환경에서는 AUTH_COOKIE_SECURE=true가 필요합니다.")
    try:
        oidc_timeout_seconds = float(os.getenv("OIDC_TIMEOUT_SECONDS", "5"))
    except ValueError as exc:
        raise RuntimeError("OIDC_TIMEOUT_SECONDS는 숫자여야 합니다.") from exc
    if not 1 <= oidc_timeout_seconds <= 30:
        raise RuntimeError("OIDC_TIMEOUT_SECONDS는 1~30초여야 합니다.")
    return SecuritySettings(
        auth_mode=auth_mode,
        secret_key=secret_key,
        token_ttl_minutes=token_ttl_minutes,
        cors_allowed_origins=origins,
        cookie_secure=cookie_secure,
        oidc_issuer_url=os.getenv("OIDC_ISSUER_URL"),
        oidc_client_id=os.getenv("OIDC_CLIENT_ID"),
        oidc_client_secret=os.getenv("OIDC_CLIENT_SECRET"),
        oidc_redirect_uri=os.getenv("OIDC_REDIRECT_URI"),
        oidc_scopes=os.getenv("OIDC_SCOPES", "openid profile email"),
        oidc_employee_id_claim=os.getenv("OIDC_EMPLOYEE_ID_CLAIM", "employee_id"),
        oidc_username_claim=os.getenv("OIDC_USERNAME_CLAIM", "preferred_username"),
        oidc_display_name_claim=os.getenv("OIDC_DISPLAY_NAME_CLAIM", "name"),
        oidc_department_claim=os.getenv("OIDC_DEPARTMENT_CLAIM", "department"),
        oidc_job_title_claim=os.getenv("OIDC_JOB_TITLE_CLAIM", "job_title"),
        oidc_login_success_url=os.getenv("OIDC_LOGIN_SUCCESS_URL", "/"),
        oidc_login_failure_url=os.getenv("OIDC_LOGIN_FAILURE_URL", "/login"),
        oidc_timeout_seconds=oidc_timeout_seconds,
        oidc_allow_insecure_localhost=oidc_allow_insecure_localhost,
    )


def directory_settings() -> DirectorySettings:
    mode = os.getenv("DIRECTORY_MODE", "local").strip().lower()
    if mode not in {"local", "http"}:
        raise RuntimeError("DIRECTORY_MODE은 local 또는 http여야 합니다.")
    try:
        timeout_seconds = float(os.getenv("DIRECTORY_API_TIMEOUT_SECONDS", "3"))
        result_limit = int(os.getenv("DIRECTORY_API_RESULT_LIMIT", "20"))
    except ValueError as exc:
        raise RuntimeError("디렉터리 timeout과 result limit 설정을 확인하세요.") from exc
    if not 1 <= timeout_seconds <= 30:
        raise RuntimeError("DIRECTORY_API_TIMEOUT_SECONDS는 1~30초여야 합니다.")
    if not 1 <= result_limit <= 50:
        raise RuntimeError("DIRECTORY_API_RESULT_LIMIT는 1~50이어야 합니다.")
    base_url = os.getenv("DIRECTORY_API_BASE_URL")
    token = os.getenv("DIRECTORY_API_TOKEN")
    allow_insecure_localhost = os.getenv("DIRECTORY_ALLOW_INSECURE_LOCALHOST", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if mode == "http":
        if not base_url or not token:
            raise RuntimeError("DIRECTORY_MODE=http일 때 DIRECTORY_API_BASE_URL과 DIRECTORY_API_TOKEN이 필요합니다.")
        parsed = urlparse(base_url)
        insecure_localhost = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
        if parsed.scheme != "https" and not (allow_insecure_localhost and insecure_localhost):
            raise RuntimeError("DIRECTORY_API_BASE_URL은 HTTPS여야 합니다.")
    return DirectorySettings(
        mode=mode,
        base_url=base_url,
        search_path=os.getenv("DIRECTORY_API_SEARCH_PATH", "/employees/search"),
        token=token,
        timeout_seconds=timeout_seconds,
        result_limit=result_limit,
        allow_insecure_localhost=allow_insecure_localhost,
    )
