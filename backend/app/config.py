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
    postgres_pool: "PostgresPoolSettings"


@dataclass(frozen=True)
class PostgresPoolSettings:
    """Bounded, deployment-configurable PostgreSQL pool budgets."""

    request_pool_size: int
    request_max_overflow: int
    request_timeout_seconds: int
    media_pool_size: int
    media_max_overflow: int
    media_timeout_seconds: int
    recycle_seconds: int


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


@dataclass(frozen=True)
class ImportBundleLimits:
    """Hard ceilings for one captured canonical result bundle.

    These limits apply before a master-folder bundle is parsed or written to
    the database.  They bound private snapshot disk usage as well as parser
    work, so each knob is intentionally finite even when configured through
    the environment. ``max_curve_points`` is intentionally both the per-curve
    and cumulative bundle point ceiling.
    """

    max_manifest_bytes: int
    max_mapping_count: int
    max_file_bytes: int
    max_total_bytes: int
    max_structured_bytes: int = 8 * 1024 * 1024
    max_scalar_records: int = 100_000
    max_curves: int = 128
    max_curve_points: int = 100_000


@dataclass(frozen=True)
class ImportSnapshotSettings:
    """Private snapshot workspace and disk-reservation policy."""

    root: Path
    reserve_bytes: int
    min_free_bytes: int
    stale_seconds: int


def import_snapshot_settings(
    limits: ImportBundleLimits | None = None,
) -> ImportSnapshotSettings:
    """Read bounded private snapshot workspace settings.

    On POSIX the default is deliberately a native ``/tmp`` child, avoiding
    environment-selected temporary directories which may be mounted on a
    non-POSIX filesystem under WSL.
    """

    limits = import_bundle_limits() if limits is None else limits
    default_root = Path("/tmp/simdashboard-import-snapshots") if os.name == "posix" else Path(os.getenv("TEMP", str(ROOT / "tmp"))) / "simdashboard-import-snapshots"
    raw_root = os.getenv("SIMDASH_IMPORT_SNAPSHOT_ROOT")
    root = Path(raw_root).expanduser() if raw_root else default_root
    if not root.is_absolute():
        raise RuntimeError("SIMDASH_IMPORT_SNAPSHOT_ROOT는 절대 경로여야 합니다.")
    reserve_default = limits.max_total_bytes
    reserve = _bounded_int(
        "SIMDASH_IMPORT_SNAPSHOT_RESERVE_BYTES",
        reserve_default,
        reserve_default,
        16 * 1024 * 1024 * 1024,
    )
    min_free = _bounded_int(
        "SIMDASH_IMPORT_SNAPSHOT_MIN_FREE_BYTES",
        64 * 1024 * 1024,
        0,
        16 * 1024 * 1024 * 1024,
    )
    stale = _bounded_int(
        "SIMDASH_IMPORT_SNAPSHOT_STALE_SECONDS",
        24 * 60 * 60,
        60,
        90 * 24 * 60 * 60,
    )
    return ImportSnapshotSettings(
        root=root,
        reserve_bytes=reserve,
        min_free_bytes=min_free,
        stale_seconds=stale,
    )


def import_readiness_policy() -> str:
    """Return the import policy for bundles which do not have a READY marker."""

    policy = os.getenv("SIMDASH_IMPORT_READINESS_POLICY", "legacy").strip().lower()
    if policy not in {"legacy", "required"}:
        raise RuntimeError("SIMDASH_IMPORT_READINESS_POLICY는 legacy 또는 required여야 합니다.")
    return policy


def import_refresh_max_concurrent() -> int:
    """Read the refresh concurrency contract (currently serialized at one)."""
    return _bounded_int("SIMDASH_IMPORT_REFRESH_MAX_CONCURRENT", 1, 1, 1)


def database_settings() -> DatabaseSettings:
    backend = os.getenv("ANALYSIS_DB_BACKEND", "duckdb").strip().lower()
    if backend not in {"duckdb", "postgresql"}:
        raise RuntimeError("ANALYSIS_DB_BACKEND은 duckdb 또는 postgresql이어야 합니다.")
    default_path = Path(__file__).resolve().parents[1] / "data" / "analysis_dashboard.duckdb"
    path = Path(os.getenv("ANALYSIS_DUCKDB_PATH", str(default_path))).expanduser().resolve()
    return DatabaseSettings(
        backend=backend,
        duckdb_path=path,
        database_url=os.getenv("DATABASE_URL"),
        postgres_pool=_postgres_pool_settings(),
    )


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name}은 정수여야 합니다.") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name}은 {minimum}~{maximum} 범위여야 합니다.")
    return value


def import_bundle_limits() -> ImportBundleLimits:
    """Read bounded canonical result-bundle capture limits.

    Defaults are deliberately generous enough for the checked-in fixtures and
    bound snapshot/parser work. Manifest and typed-scalar JSON use bounded
    ``ijson`` streaming; each scalar item is still materialized within its
    fixed 64-event complexity budget, and the validated scalar list remains
    materialized up to ``max_scalar_records`` for the current UoW contract.
    Operators must therefore size overrides for the worker memory budget
    rather than treating these limits as an absolute exhaustion guard.
    """

    limits = ImportBundleLimits(
        max_manifest_bytes=_bounded_int(
            "SIMDASH_IMPORT_MAX_MANIFEST_BYTES",
            1 * 1024 * 1024,
            1024,
            16 * 1024 * 1024,
        ),
        max_mapping_count=_bounded_int("SIMDASH_IMPORT_MAX_MAPPING_COUNT", 128, 1, 10_000),
        max_file_bytes=_bounded_int(
            "SIMDASH_IMPORT_MAX_FILE_BYTES",
            512 * 1024 * 1024,
            1024,
            4 * 1024 * 1024 * 1024,
        ),
        max_total_bytes=_bounded_int(
            "SIMDASH_IMPORT_MAX_TOTAL_BYTES",
            1 * 1024 * 1024 * 1024,
            1024,
            16 * 1024 * 1024 * 1024,
        ),
        max_structured_bytes=_bounded_int(
            "SIMDASH_IMPORT_MAX_STRUCTURED_BYTES",
            8 * 1024 * 1024,
            1024,
            64 * 1024 * 1024,
        ),
        max_scalar_records=_bounded_int("SIMDASH_IMPORT_MAX_SCALAR_RECORDS", 100_000, 1, 1_000_000),
        max_curves=_bounded_int("SIMDASH_IMPORT_MAX_CURVES", 128, 1, 10_000),
        max_curve_points=_bounded_int("SIMDASH_IMPORT_MAX_CURVE_POINTS", 100_000, 1, 10_000_000),
    )
    if limits.max_structured_bytes > limits.max_file_bytes:
        raise RuntimeError("SIMDASH_IMPORT_MAX_STRUCTURED_BYTES는 SIMDASH_IMPORT_MAX_FILE_BYTES 이하여야 합니다.")
    return limits


def _postgres_pool_settings() -> PostgresPoolSettings:
    """Read explicit pool knobs once per database settings lookup.

    Defaults retain the existing 5+10 request and 10+5 media connection
    budgets. Deployment profiles can reduce those numbers without code edits.
    """
    return PostgresPoolSettings(
        request_pool_size=_bounded_int("POSTGRES_REQUEST_POOL_SIZE", 5, 1, 100),
        request_max_overflow=_bounded_int("POSTGRES_REQUEST_MAX_OVERFLOW", 10, 0, 100),
        request_timeout_seconds=_bounded_int("POSTGRES_REQUEST_POOL_TIMEOUT_SECONDS", 10, 1, 120),
        media_pool_size=_bounded_int("POSTGRES_MEDIA_POOL_SIZE", 10, 1, 100),
        media_max_overflow=_bounded_int("POSTGRES_MEDIA_MAX_OVERFLOW", 5, 0, 100),
        media_timeout_seconds=_bounded_int("POSTGRES_MEDIA_POOL_TIMEOUT_SECONDS", 10, 1, 120),
        recycle_seconds=_bounded_int("POSTGRES_POOL_RECYCLE_SECONDS", 1800, 60, 86400),
    )


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
