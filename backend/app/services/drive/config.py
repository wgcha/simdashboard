"""SCX drive mode settings (docs/features/scx-drive.md §설정값, integration 01 §2).

``SIMDASH_DRIVE_GATEWAY`` selects the mode.  ``none`` (default) keeps the
existing local SPDM root and never touches anything in this package beyond
reading that one variable.  ``scx`` requires the variables listed in
``REQUIRED_SCX`` and refuses startup naming the first missing/invalid one.
"""
from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from ...config import ROOT

DriveMode = Literal["none", "scx"]
PasswordLoginMode = Literal["off", "https_only", "on"]

MODE_ENV = "SIMDASH_DRIVE_GATEWAY"
REQUIRED_SCX = (
    "SIMDASH_SCX_WORKER_PYTHON",
    "SIMDASH_SCX_SERVER_URL",
    "SIMDASH_SCX_CLIENT_NAME",
    "SIMDASH_SECRET_ENC_KEY",
)
DEFAULT_WORK_DIR = ROOT / "backend" / "data" / "scx-worker"


class DriveConfigError(RuntimeError):
    """Invalid or incomplete SCX drive configuration; the message names the variable."""

    def __init__(self, variable: str, reason: str) -> None:
        self.variable = variable
        super().__init__(
            f"SCX 드라이브 설정 오류: {variable} — {reason} "
            f"(SCX drive configuration error: {variable})"
        )


@dataclass(frozen=True)
class DriveSettings:
    mode: DriveMode
    worker_python: Path | None = None
    server_url: str | None = None
    client_name: str | None = None
    spdm_root: str | None = None          # SIMDASH_SCX_DRIVE_ROOT, relative to the shared account home
    ca_bundle: Path | None = None
    work_dir: Path = DEFAULT_WORK_DIR
    staging_dir: Path = DEFAULT_WORK_DIR / "staging"
    max_concurrency: int = 1
    cache_ttl_seconds: float = 30.0
    secret_key: str | None = field(default=None, repr=False)
    # scx-drive plan §9 "드라이브 쓰기 허용": off by default. D3: the upload queue, result drop
    # uploads and Final designation write to the drive only when this is on.
    writes_enabled: bool = False
    # 05 §5.2: attempts of one queue item for retryable drive errors before it is FAILED.
    upload_max_attempts: int = 8
    # D3 review #1: a same-name drive file without sha1 is downloaded (server staging) and compared by
    # sha256 up to this size; a larger one ends the queue item CONFLICT for an admin decision.
    verify_max_bytes: int = 2 * 1024 ** 3
    # 05 §3 server blob store for downloaded files above BLOB_THRESHOLD_BYTES (content-addressed).
    blob_dir: Path = DEFAULT_WORK_DIR / "blobs"
    blob_max_bytes: int = 100 * 1024 ** 3
    # Issue #50 (docs/plans/scx-drive-relogin-spec.md §3.1): account login from the reconnect dialog.
    password_login: PasswordLoginMode = "https_only"
    # Activation-code login inside the dashboard (relogin spec §7).  Off by default since 2026-10-16:
    # the shared account is connected with the adapter CLI `login` and token registration.
    device_login: bool = False
    login_max_attempts: int = 3

    @property
    def enabled(self) -> bool:
        return self.mode == "scx"

    @property
    def server_host(self) -> str | None:
        return url_host(self.server_url) if self.server_url else None

    @property
    def lock_path(self) -> Path:
        return self.work_dir / "dashboard.lock"


def url_host(url: str) -> str | None:
    try:
        host = urlparse(url.strip()).hostname
    except ValueError:
        return None
    return host.lower() if host else None


def drive_mode() -> DriveMode:
    raw = os.getenv(MODE_ENV, "none").strip().lower() or "none"
    if raw not in {"none", "scx"}:
        raise DriveConfigError(MODE_ENV, "none 또는 scx여야 합니다.")
    return "scx" if raw == "scx" else "none"


def _text(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _required(name: str) -> str:
    value = _text(name)
    if value is None:
        raise DriveConfigError(name, "scx 모드에서 필수입니다. 값이 없습니다.")
    return value


def _absolute_path(name: str, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise DriveConfigError(name, "절대 경로여야 합니다.")
    return path.absolute()


def _number(name: str, default: float, minimum: float, maximum: float, *, integer: bool) -> float:
    raw = _text(name)
    if raw is None:
        return default
    try:
        value = int(raw) if integer else float(raw)
    except ValueError as exc:
        raise DriveConfigError(name, "숫자여야 합니다.") from exc
    if not minimum <= value <= maximum:
        raise DriveConfigError(name, f"{minimum:g}~{maximum:g} 범위여야 합니다.")
    return value


def validate_drive_rel_path(value: str, *, allow_empty: bool) -> str:
    """Contract §1 relative path rules (NFC, '/', no '.', '..', empty segment, leading '~')."""
    text = unicodedata.normalize("NFC", value or "")
    if not text:
        if allow_empty:
            return ""
        raise ValueError("경로가 비어 있습니다.")
    if text.startswith("/") or "\\" in text or text.startswith("~"):
        raise ValueError("드라이브 상대 경로는 '/'·'~'로 시작하거나 '\\'를 포함할 수 없습니다.")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise ValueError("제어 문자를 포함할 수 없습니다.")
    parts = text.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("빈 구간, '.', '..' 구간은 쓸 수 없습니다.")
    return text


def _flag(name: str) -> bool:
    raw = _text(name)
    if raw is None:
        return False
    value = raw.casefold()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise DriveConfigError(name, "true 또는 false여야 합니다.")


def _password_login(name: str) -> PasswordLoginMode:
    raw = "_".join((_text(name) or "https_only").casefold().split("-"))
    if raw not in {"off", "https_only", "on"}:
        raise DriveConfigError(name, "off, https_only, on 중 하나여야 합니다.")
    return raw  # type: ignore[return-value]


def _fernet_key(name: str) -> str:
    value = _required(name)
    try:
        from cryptography.fernet import Fernet

        Fernet(value.encode("ascii"))
    except Exception as exc:  # ValueError/binascii errors; never echo the value
        raise DriveConfigError(name, "Fernet 키(32바이트 urlsafe base64)가 아닙니다.") from exc
    if value == (os.getenv("AUTH_SECRET_KEY") or "").strip():
        raise DriveConfigError(name, "AUTH_SECRET_KEY와 다른 값이어야 합니다.")
    return value


# --- SCX relay for PhysicsAI (docs/features/scx-relay.md §2, default off) ---------------------

RELAY_ENABLED_ENV = "SIMDASH_SCX_RELAY_ENABLED"
RELAY_SECRET_ENV = "SIMDASH_SCX_RELAY_SECRET_FILE"
RELAY_HOME_DEFAULT = "/Users/vd.dashboard"
RELAY_MAX_UPLOAD_DEFAULT = 4 * 1024 ** 3
RELAY_MAX_DOWNLOAD_DEFAULT = 20 * 1024 ** 3
RELAY_PATH_MAX = 1024
RELAY_SEGMENT_MAX = 255
_RELAY_SECRET = re.compile(r"[A-Za-z0-9_\-]{43,128}")
JobPathForm = Literal["rel", "tilde", "absolute"]


@dataclass(frozen=True)
class RelaySettings:
    """``SIMDASH_SCX_RELAY_*``; the secret bytes never appear in ``repr`` or comparisons."""

    enabled: bool = False
    secret: bytes = field(default=b"", repr=False, compare=False)
    secret_file: Path | None = None
    drive_base: str = ""
    job_path_form: JobPathForm = "rel"
    home_abs: str = RELAY_HOME_DEFAULT
    max_upload_bytes: int = RELAY_MAX_UPLOAD_DEFAULT
    max_download_bytes: int = RELAY_MAX_DOWNLOAD_DEFAULT


def relay_enabled() -> bool:
    return _flag(RELAY_ENABLED_ENV)


def check_relay_rel_path(value: str, *, allow_empty: bool) -> str:
    """Relay path form (contract §4): ``validate_drive_rel_path`` + 1–255 per segment, ≤ 1024 in total."""
    text = validate_drive_rel_path(value, allow_empty=allow_empty)
    if len(text) > RELAY_PATH_MAX:
        raise ValueError(f"경로가 {RELAY_PATH_MAX}자를 넘습니다.")
    if text and any(len(part) > RELAY_SEGMENT_MAX for part in text.split("/")):
        raise ValueError(f"경로 구간이 {RELAY_SEGMENT_MAX}자를 넘습니다.")
    return text


def _relay_secret(path: Path) -> bytes:
    """One line of base64url (43–128 chars); BOM and surrounding whitespace ignored.  Never echoed."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise DriveConfigError(RELAY_SECRET_ENV, f"비밀 파일을 읽을 수 없습니다: {path}") from exc
    try:
        text = raw.decode("utf-8-sig").strip()
    except UnicodeDecodeError as exc:
        raise DriveConfigError(RELAY_SECRET_ENV, "비밀 파일 형식이 올바르지 않습니다(한 줄 base64url 43~128자).") from exc
    if not _RELAY_SECRET.fullmatch(text):
        raise DriveConfigError(RELAY_SECRET_ENV, "비밀 파일 형식이 올바르지 않습니다(한 줄 base64url 43~128자).")
    return text.encode("ascii")


def _relay_home(name: str) -> str:
    raw = _text(name)
    if raw is None:
        return RELAY_HOME_DEFAULT
    text = unicodedata.normalize("NFC", raw).rstrip("/")
    parts = text.split("/")
    if (not text.startswith("/") or "\\" in text or any(ord(ch) < 32 or ord(ch) == 127 for ch in text)
            or any(part in {"", ".", ".."} for part in parts[1:])):
        raise DriveConfigError(name, "'/'로 시작하는 절대 표기여야 합니다(예: /Users/<계정>).")
    return text


def relay_settings() -> RelaySettings:
    """Read the relay settings; when enabled every value is validated (startup refuses otherwise)."""
    if not relay_enabled():
        return RelaySettings(enabled=False)
    secret_raw = _text(RELAY_SECRET_ENV)
    if secret_raw is None:
        raise DriveConfigError(RELAY_SECRET_ENV, "relay를 켜면 필수입니다. 값이 없습니다.")
    secret_file = _absolute_path(RELAY_SECRET_ENV, secret_raw)
    secret = _relay_secret(secret_file)
    base_raw = os.getenv("SIMDASH_SCX_RELAY_DRIVE_BASE", "").strip()
    try:
        drive_base = check_relay_rel_path(base_raw, allow_empty=True)
    except ValueError as exc:
        raise DriveConfigError("SIMDASH_SCX_RELAY_DRIVE_BASE", f"공용 계정 홈 기준 상대 경로여야 합니다. {exc}") from exc
    if drive_base.startswith("."):
        raise DriveConfigError("SIMDASH_SCX_RELAY_DRIVE_BASE", "'.'으로 시작하는 폴더는 쓸 수 없습니다.")
    form = (_text("SIMDASH_SCX_RELAY_JOB_PATH_FORM") or "rel").casefold()
    if form not in {"rel", "tilde", "absolute"}:
        raise DriveConfigError("SIMDASH_SCX_RELAY_JOB_PATH_FORM", "rel, tilde, absolute 중 하나여야 합니다.")
    return RelaySettings(
        enabled=True,
        secret=secret,
        secret_file=secret_file,
        drive_base=drive_base,
        job_path_form=form,  # type: ignore[arg-type]
        home_abs=_relay_home("SIMDASH_SCX_RELAY_HOME_ABS"),
        max_upload_bytes=int(_number("SIMDASH_SCX_RELAY_MAX_UPLOAD_BYTES", RELAY_MAX_UPLOAD_DEFAULT,
                                     1024 ** 2, 2 ** 40, integer=True)),
        max_download_bytes=int(_number("SIMDASH_SCX_RELAY_MAX_DOWNLOAD_BYTES", RELAY_MAX_DOWNLOAD_DEFAULT,
                                       1024 ** 2, 2 ** 40, integer=True)),
    )


def drive_settings() -> DriveSettings:
    """Read the drive settings; in scx mode every required value is validated."""
    mode = drive_mode()
    if mode == "none":
        return DriveSettings(mode="none")
    for name in REQUIRED_SCX:
        _required(name)
    worker_python = _absolute_path("SIMDASH_SCX_WORKER_PYTHON", _required("SIMDASH_SCX_WORKER_PYTHON"))
    if not worker_python.is_file():
        raise DriveConfigError("SIMDASH_SCX_WORKER_PYTHON", f"파일이 없습니다: {worker_python}")
    server_url = _required("SIMDASH_SCX_SERVER_URL")
    parsed = urlparse(server_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise DriveConfigError("SIMDASH_SCX_SERVER_URL", "https://<호스트>/ 형식이어야 합니다.")
    client_name = _required("SIMDASH_SCX_CLIENT_NAME")
    spdm_root = _text("SIMDASH_SCX_DRIVE_ROOT")
    if spdm_root is not None:
        try:
            spdm_root = validate_drive_rel_path(spdm_root.strip("/"), allow_empty=False)
        except ValueError as exc:
            raise DriveConfigError("SIMDASH_SCX_DRIVE_ROOT", f"공용 계정 홈 기준 상대 경로여야 합니다. {exc}") from exc
    ca_raw = _text("SIMDASH_SCX_CA_BUNDLE")
    ca_bundle = _absolute_path("SIMDASH_SCX_CA_BUNDLE", ca_raw) if ca_raw else None
    if ca_bundle is not None and not ca_bundle.is_file():
        raise DriveConfigError("SIMDASH_SCX_CA_BUNDLE", f"파일이 없습니다: {ca_bundle}")
    work_raw = _text("SIMDASH_SCX_WORK_DIR")
    work_dir = _absolute_path("SIMDASH_SCX_WORK_DIR", work_raw) if work_raw else DEFAULT_WORK_DIR
    staging_raw = _text("SIMDASH_SCX_STAGING_DIR")
    staging_dir = _absolute_path("SIMDASH_SCX_STAGING_DIR", staging_raw) if staging_raw else work_dir / "staging"
    blob_raw = _text("SIMDASH_DRIVE_BLOB_DIR")
    blob_dir = _absolute_path("SIMDASH_DRIVE_BLOB_DIR", blob_raw) if blob_raw else work_dir / "blobs"
    from ..storage.server_local import blob_dir_conflict

    conflict = blob_dir_conflict(blob_dir, reserved=(work_dir, staging_dir), outside=(staging_dir,))
    if conflict is not None:
        # L2: blob eviction deletes files, so its folder never overlaps the work or staging folders.
        raise DriveConfigError("SIMDASH_DRIVE_BLOB_DIR", f"작업·임시 폴더와 겹칠 수 없습니다({conflict}).")
    return DriveSettings(
        mode="scx",
        worker_python=worker_python,
        server_url=server_url,
        client_name=client_name,
        spdm_root=spdm_root,
        ca_bundle=ca_bundle,
        work_dir=work_dir,
        staging_dir=staging_dir,
        max_concurrency=int(_number("SIMDASH_SCX_MAX_CONCURRENCY", 1, 1, 8, integer=True)),
        cache_ttl_seconds=float(_number("SIMDASH_SCX_CACHE_TTL_SECONDS", 30.0, 0.0, 3600.0, integer=False)),
        secret_key=_fernet_key("SIMDASH_SECRET_ENC_KEY"),
        writes_enabled=_flag("SIMDASH_DRIVE_WRITES_ENABLED"),
        upload_max_attempts=int(_number("SIMDASH_DRIVE_UPLOAD_MAX_ATTEMPTS", 8, 1, 100, integer=True)),
        verify_max_bytes=int(_number("SIMDASH_DRIVE_VERIFY_MAX_BYTES", 2 * 1024 ** 3, 0, 2 ** 50, integer=True)),
        blob_dir=blob_dir,
        blob_max_bytes=int(_number("SIMDASH_DRIVE_BLOB_MAX_BYTES", 100 * 1024 ** 3, 64 * 1024 ** 2, 2 ** 50, integer=True)),
        password_login=_password_login("SIMDASH_DRIVE_PASSWORD_LOGIN"),
        device_login=_flag("SIMDASH_DRIVE_DEVICE_LOGIN"),
        login_max_attempts=int(_number("SIMDASH_DRIVE_LOGIN_MAX_ATTEMPTS", 3, 1, 20, integer=True)),
    )
