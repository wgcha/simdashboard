"""Administrator database backup (user request 2026-10-23, docs/deployment-security-backup-guide.md §5.1).

One service for the 「DB 백업」 button and for callers that want a safety copy before a destructive action
(``create_backup(reason, actor)``, used e.g. before 선택 삭제).

* PostgreSQL: ``pg_dump --format=custom`` (owner URL when ``POSTGRES_OWNER_URL`` / ``.postgres-owner.env`` points at
  the same database, else ``DATABASE_URL``), verified with ``pg_restore --list``.  The password travels in
  ``PGPASSWORD`` only; it is never on a command line, in a log line or in an error message.  Tool discovery
  (``POSTGRES_BIN``, PATH, PostgreSQL install folders) is ``scripts.postgres_cli.executable``, the same code the
  deployment backup uses; failures carry the allow-listed ``backup_failure_details`` fields.
* DuckDB (development): the database file is checkpointed and copied.
* Files: ``<UTC timestamp>_<reason>.dump`` (``.duckdb``) plus ``<same stem>.manifest.json`` (version, commit,
  database, size, sha256, reason, memo, who).  A file is published by rename only after it was verified.
* Folder: ``SIMDASH_DB_BACKUP_DIR``; default ``<install>/state/backups`` for the offline-installer layout
  (``<install>/releases/<id>`` next to ``<install>/state``), else ``backend/data/db-backups``.  A folder inside
  the web root (frontend / public assets) is refused.
* Retention: ``SIMDASH_DB_BACKUP_KEEP`` (default 20) newest *automatic* backups are kept; ``manual`` ones are
  never deleted automatically.
* One backup at a time (process-wide lock).  The endpoint runs it in a background thread.

Restore is deliberately not offered by the web application (``pg_restore``; see the guide §6).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..config import ROOT, database_settings
from ..version import __version__

logger = logging.getLogger("app.services.db_backup")

BACKEND = Path(__file__).resolve().parents[2]
MANUAL_REASON = "manual"
DEFAULT_KEEP = 20
MAX_MEMO = 200
MANIFEST_SUFFIX = ".manifest.json"
DUMP_SUFFIXES = (".dump", ".duckdb")
ID_PATTERN = re.compile(r"^\d{8}T\d{6}Z_[a-z0-9][a-z0-9-]{0,31}(?:-\d{1,3})?$")
SUBPROCESS_TIMEOUT_SECONDS = 4 * 3600
LOCK_WAIT_SECONDS = 1800


class BackupError(Exception):
    """A backup problem with a stable code and a Korean message (no secrets)."""

    def __init__(self, code: str, message: str, *, status: int = 500, stage: str | None = None,
                 details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.stage = stage
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.stage:
            out["stage"] = self.stage
        if self.details:
            out["details"] = self.details
        return out


# --------------------------------------------------------------------------------------------- settings

def keep_automatic() -> int:
    raw = os.getenv("SIMDASH_DB_BACKUP_KEEP", str(DEFAULT_KEEP)).strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_KEEP
    return value if 1 <= value <= 1000 else DEFAULT_KEEP


def _installer_state_dir() -> Path | None:
    """``<install>/state`` when this release lives in ``<install>/releases/<id>`` (offline installer layout)."""
    releases = ROOT.parent
    if releases.name.lower() != "releases":
        return None
    state = releases.parent / "state"
    return state if state.is_dir() else None


def _web_roots() -> list[Path]:
    return [ROOT / "frontend", BACKEND / "public_assets", BACKEND / "assets"]


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def backup_dir() -> Path:
    configured = os.getenv("SIMDASH_DB_BACKUP_DIR", "").strip()
    if configured:
        candidate = Path(configured).expanduser()
    else:
        state = _installer_state_dir()
        candidate = state / "backups" if state is not None else BACKEND / "data" / "db-backups"
    candidate = candidate.absolute()
    for web_root in _web_roots():
        if _inside(candidate, web_root):
            raise BackupError("DB_BACKUP_DIR_IN_WEB_ROOT",
                              "백업 폴더가 웹 공개 폴더 안에 있습니다. SIMDASH_DB_BACKUP_DIR을 웹 루트 밖으로 지정하세요.",
                              status=500, stage="config")
    return candidate


def _prepare_dir() -> Path:
    directory = backup_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(directory, 0o700)
    except OSError as error:
        raise _storage_error(error, "config") from error
    return directory


# ------------------------------------------------------------------------------------------- build info

def _commit() -> str | None:
    try:
        from .releases import read_build_info
        return read_build_info().get("commit")
    except Exception:  # noqa: BLE001 - the commit is informational
        return None


# ---------------------------------------------------------------------------------------------- errors

def _details(error: BaseException) -> dict[str, Any]:
    try:
        from scripts.backup_failure_details import exception_details
    except ImportError:  # pragma: no cover - backend is not on sys.path
        sys.path.insert(0, str(BACKEND))
        from scripts.backup_failure_details import exception_details
    return exception_details(error)


def _storage_error(error: OSError, stage: str) -> BackupError:
    return BackupError("DB_BACKUP_STORAGE_FAILED",
                       "백업 파일을 쓰지 못했습니다. 백업 폴더 권한과 남은 디스크 공간을 확인하세요.",
                       stage=stage, details=_details(error))


_PASSWORD_PARAM = re.compile(r"(?i)(password\s*[=:]\s*)\S+")


def _redact(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return _PASSWORD_PARAM.sub(r"\1***", text)


def _stderr_tail(stderr: str, secrets: list[str]) -> str:
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    return _redact(" / ".join(lines[-3:]), secrets)[:400]


def _classify_tool_failure(tool: str, stage: str, error: subprocess.CalledProcessError, stderr: str,
                           secrets: list[str]) -> BackupError:
    lowered = stderr.lower()
    details = {**_details(error), "tool": tool}
    tail = _stderr_tail(stderr, secrets)
    if tail:
        details["stderr_tail"] = tail
    if "server version" in lowered or "version mismatch" in lowered:
        return BackupError("DB_BACKUP_VERSION_MISMATCH",
                           f"{tool} 버전이 DB 서버 버전과 맞지 않습니다. 서버와 같은 메이저 버전의 PostgreSQL bin 폴더를 POSTGRES_BIN에 지정하세요.",
                           stage=stage, details=details)
    if "password authentication failed" in lowered or "no pg_hba.conf entry" in lowered:
        return BackupError("DB_BACKUP_AUTH_FAILED", "DB 계정 인증에 실패했습니다. DATABASE_URL·POSTGRES_OWNER_URL의 계정을 확인하세요.",
                           stage=stage, details=details)
    if "could not connect" in lowered or "connection refused" in lowered or "timed out" in lowered:
        return BackupError("DB_BACKUP_CONNECTION_FAILED", "PostgreSQL에 연결하지 못했습니다. 서비스 상태와 포트를 확인하세요.",
                           stage=stage, details=details)
    if "permission denied" in lowered:
        return BackupError("DB_BACKUP_DB_PRIVILEGE", "DB 읽기 권한이 부족합니다. POSTGRES_OWNER_URL(소유자 계정)을 설정하세요.",
                           stage=stage, details=details)
    if "no space left" in lowered or "disk full" in lowered:
        return BackupError("DB_BACKUP_STORAGE_FAILED", "백업 폴더의 디스크 공간이 부족합니다.", stage=stage, details=details)
    if stage == "pg_restore_list":
        return BackupError("DB_BACKUP_VERIFY_FAILED", "백업 파일 검증(pg_restore --list)에 실패했습니다. 파일을 보관하지 않았습니다.",
                           stage=stage, details=details)
    return BackupError("DB_BACKUP_DUMP_FAILED", "pg_dump가 실패했습니다. 상세 코드를 관리자에게 전달하세요.", stage=stage, details=details)


# ----------------------------------------------------------------------------------------- PostgreSQL

def _postgres_cli():
    try:
        from scripts import postgres_cli
    except ImportError:  # pragma: no cover
        sys.path.insert(0, str(BACKEND))
        from scripts import postgres_cli
    return postgres_cli


def _owner_url() -> str | None:
    value = os.getenv("POSTGRES_OWNER_URL", "").strip()
    if value:
        return value
    try:
        from dotenv import dotenv_values
        return (dotenv_values(ROOT / ".postgres-owner.env").get("POSTGRES_OWNER_URL") or "").strip() or None
    except Exception:  # noqa: BLE001
        return None


def _dump_target(cli) -> tuple[Any, str]:
    """The target to dump: the owner account when it addresses the same database, else the app account."""
    app_url = database_settings().database_url
    if not app_url:
        raise BackupError("DB_BACKUP_CONFIG", "DATABASE_URL이 설정되어 있지 않습니다.", stage="config")
    try:
        app_target = cli.parse_target(app_url)
    except RuntimeError as error:
        raise BackupError("DB_BACKUP_CONFIG", "DATABASE_URL 형식이 올바르지 않습니다.", stage="config") from error
    owner = _owner_url()
    if owner:
        try:
            owner_target = cli.parse_target(owner)
        except RuntimeError:
            owner_target = None
        if owner_target and (owner_target.host, owner_target.port, owner_target.database) == \
                (app_target.host, app_target.port, app_target.database):
            return owner_target, "owner"
    return app_target, "app"


def _tool_major(path: str, secrets: list[str]) -> int | None:
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=30, check=True,
                             env={**os.environ, "LC_ALL": "C"}).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    found = re.search(r"(\d+)(?:\.\d+)*", out.split(")")[-1])
    return int(found.group(1)) if found else None


def _server_major() -> int | None:
    try:
        from ..database_connection import connect
        with connect() as conn:
            row = conn.execute("SHOW server_version_num").fetchone()
        return int(row[0]) // 10000 if row else None
    except Exception:  # noqa: BLE001 - pg_dump reports a mismatch itself
        return None


def _run(command: list[str], env: dict[str, str], stage: str, tool: str, secrets: list[str]) -> None:
    try:
        subprocess.run(command, env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                       text=True, encoding="utf-8", errors="replace", timeout=SUBPROCESS_TIMEOUT_SECONDS)
    except subprocess.CalledProcessError as error:
        raise _classify_tool_failure(tool, stage, error, error.stderr or "", secrets) from None
    except subprocess.TimeoutExpired as error:
        raise BackupError("DB_BACKUP_TIMEOUT", f"{tool}가 제한 시간 안에 끝나지 않았습니다.", stage=stage,
                          details=_details(error)) from None
    except OSError as error:
        raise BackupError("DB_BACKUP_TOOL_LAUNCH_FAILED", f"{tool}를 실행하지 못했습니다. POSTGRES_BIN 경로와 실행 권한을 확인하세요.",
                          stage=stage, details=_details(error)) from None


def _dump_postgres(partial: Path) -> dict[str, Any]:
    cli = _postgres_cli()
    target, account = _dump_target(cli)
    secrets = [target.password or ""]
    try:
        pg_dump = cli.executable("pg_dump")
        pg_restore = cli.executable("pg_restore")
    except cli.PostgresToolNotFound as error:
        raise BackupError("DB_BACKUP_TOOL_MISSING",
                          "pg_dump / pg_restore를 찾지 못했습니다. PostgreSQL bin 폴더를 POSTGRES_BIN에 지정하세요.",
                          stage="pg_dump_resolve", details=_details(error)) from None
    server = _server_major()
    client = _tool_major(pg_dump, secrets)
    if server is not None and client is not None and client != server:
        raise BackupError("DB_BACKUP_VERSION_MISMATCH",
                          f"pg_dump 버전({client})이 DB 서버 버전({server})과 맞지 않습니다. "
                          "서버와 같은 메이저 버전의 PostgreSQL bin 폴더를 POSTGRES_BIN에 지정하세요.",
                          stage="pg_dump_version", details={"client_major": client, "server_major": server})
    env = cli.command_env(target)      # PGPASSWORD only; never in argv
    env.update({"LC_ALL": "C", "LC_MESSAGES": "C", "LANGUAGE": "C"})
    command = [pg_dump, *cli.connection_args(target), "--format=custom", "--compress=6",
               "--no-owner", "--no-privileges", "--file", str(partial)]
    _run(command, env, "pg_dump", "pg_dump", secrets)
    _run([pg_restore, "--list", str(partial)], env, "pg_restore_list", "pg_restore", secrets)
    return {"format": "postgresql-custom", "database": target.database, "account": account,
            "server_major": server, "tool_major": client}


def _dump_duckdb(partial: Path) -> dict[str, Any]:
    settings = database_settings()
    source = settings.duckdb_path
    if not source.is_file():
        raise BackupError("DB_BACKUP_CONFIG", "DuckDB 파일을 찾지 못했습니다.", stage="config")
    from ..database_connection import connect
    try:
        with connect() as conn:       # holds the serialized DuckDB connection while the file is copied
            conn.execute("CHECKPOINT")
            shutil.copyfile(source, partial)
    except OSError as error:
        raise _storage_error(error, "pg_dump") from error
    if partial.stat().st_size != source.stat().st_size:
        raise BackupError("DB_BACKUP_VERIFY_FAILED", "복사한 DuckDB 파일 크기가 원본과 다릅니다.", stage="pg_restore_list")
    return {"format": "duckdb-file", "database": source.name, "account": None}


# -------------------------------------------------------------------------------------------- creation

def _slug(reason: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (reason or "").strip().lower()).strip("-")[:32].strip("-")
    return slug or "backup"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iso(value: float | datetime) -> str:
    moment = value if isinstance(value, datetime) else datetime.fromtimestamp(value, timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _reserve(directory: Path, reason: str, suffix: str) -> tuple[str, Path, Path]:
    """Pick a free ``<timestamp>_<reason>`` id and reserve its exclusive partial file."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = f"{stamp}_{_slug(reason)}"
    for n in range(1, 100):
        backup_id = base if n == 1 else f"{base}-{n}"
        final = directory / f"{backup_id}{suffix}"
        if final.exists() or (directory / f"{backup_id}{MANIFEST_SUFFIX}").exists():
            continue
        partial = directory / f".{backup_id}.{os.getpid()}.partial"
        try:
            fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        except OSError as error:
            raise _storage_error(error, "config") from error
        os.close(fd)
        return backup_id, partial, final
    raise BackupError("DB_BACKUP_STORAGE_FAILED", "백업 파일 이름을 정하지 못했습니다.", stage="config")


_lock = threading.Lock()
_job_lock = threading.Lock()
_job: dict[str, Any] | None = None


def _create_locked(reason: str, actor: str | None, memo: str | None) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    directory = _prepare_dir()
    settings = database_settings()
    suffix = ".dump" if settings.backend == "postgresql" else ".duckdb"
    backup_id, partial, final = _reserve(directory, reason, suffix)
    manifest_path = directory / f"{backup_id}{MANIFEST_SUFFIX}"
    published = False
    try:
        _set_stage("pg_dump")
        info = _dump_postgres(partial) if settings.backend == "postgresql" else _dump_duckdb(partial)
        try:
            size = partial.stat().st_size
            if size <= 0:
                raise BackupError("DB_BACKUP_VERIFY_FAILED", "백업 파일이 비어 있습니다.", stage="pg_restore_list")
            digest = _sha256(partial)
            _set_stage("publish_dump")
            os.replace(partial, final)
            published = True
            manifest = {
                "format_version": 1,
                "id": backup_id,
                "file": final.name,
                "reason": _slug(reason),
                "kind": "manual" if reason == MANUAL_REASON else "automatic",
                "memo": (memo or "").strip()[:MAX_MEMO] or None,
                "created_by": actor,
                "created_at": _iso(started),
                "completed_at": _iso(datetime.now(timezone.utc)),
                "app_version": __version__,
                "commit": _commit(),
                "backend": settings.backend,
                "size": size,
                "sha256": digest,
                **info,
            }
            _set_stage("write_manifest")
            _write_json_exclusive(manifest_path, manifest)
        except OSError as error:
            raise _storage_error(error, "publish_dump") from error
    except BaseException:
        for leftover in (partial, final if published else None, manifest_path):
            if leftover is not None:
                try:
                    leftover.unlink(missing_ok=True)
                except OSError:
                    pass
        raise
    _prune(directory)
    return {"id": backup_id, "path": str(final), "size": size, "created_at": manifest["created_at"]}


def _write_json_exclusive(path: Path, payload: dict[str, Any]) -> None:
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
    finally:
        os.close(fd)


def create_backup(reason: str, actor: str | None, *, memo: str | None = None) -> dict[str, Any]:
    """Create and verify one backup and return ``{id, path, size, created_at}``.

    Callable from any thread (e.g. a delete flow that wants a safety copy first).  Backups never overlap: a
    second caller waits for the running one (up to 30 minutes) and then makes its own.  Raises ``BackupError``.
    """
    if not _lock.acquire(timeout=LOCK_WAIT_SECONDS):
        raise BackupError("DB_BACKUP_BUSY", "다른 백업이 오래 실행 중입니다.", status=409)
    try:
        return _create_locked(reason, actor, memo)
    finally:
        _lock.release()


# -------------------------------------------------------------------------------------------- job state

def _set_stage(stage: str) -> None:
    with _job_lock:
        if _job is not None and _job.get("status") == "running":
            _job["stage"] = stage


def current_job() -> dict[str, Any] | None:
    with _job_lock:
        return dict(_job) if _job is not None else None


def start_job(reason: str, actor: str | None, *, memo: str | None = None,
              on_finish: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Start a background backup; ``BackupError(DB_BACKUP_RUNNING)`` when one is already running."""
    global _job
    if not _lock.acquire(blocking=False):
        raise BackupError("DB_BACKUP_RUNNING", "이미 백업이 진행 중입니다. 끝난 뒤 다시 시도하세요.", status=409)
    with _job_lock:
        _job = {"status": "running", "reason": reason, "actor": actor, "memo": (memo or "").strip()[:MAX_MEMO] or None,
                "started_at": _iso(datetime.now(timezone.utc)), "finished_at": None, "stage": "config",
                "backup": None, "error": None}
        snapshot = dict(_job)

    def work() -> None:
        outcome: dict[str, Any] = {}
        try:
            outcome["backup"] = _create_locked(reason, actor, memo)
        except BackupError as error:
            outcome["error"] = error.as_dict()
        except Exception as error:  # noqa: BLE001 - reported without text (secrets)
            logger.error("DB backup failed unexpectedly: %s", type(error).__name__)
            outcome["error"] = {"code": "DB_BACKUP_FAILED", "message": "백업 중 예기치 않은 오류가 발생했습니다.",
                                "details": _details(error)}
        finally:
            _lock.release()
        with _job_lock:
            if _job is not None:
                _job.update(status="failed" if "error" in outcome else "succeeded",
                            finished_at=_iso(datetime.now(timezone.utc)), **outcome)
                final = dict(_job)
        if on_finish is not None:
            try:
                on_finish(final)
            except Exception:  # noqa: BLE001 - audit trouble must not hide the result
                logger.error("DB backup completion hook failed")

    threading.Thread(target=work, name="db-backup", daemon=True).start()
    return snapshot


# ----------------------------------------------------------------------------------------- list / delete

def _entry(directory: Path, dump: Path) -> dict[str, Any]:
    stem = dump.name[: -len(dump.suffix)]
    manifest: dict[str, Any] = {}
    try:
        loaded = json.loads((directory / f"{stem}{MANIFEST_SUFFIX}").read_text(encoding="utf-8"))
        manifest = loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError):
        pass
    stat = dump.stat()
    status = "ok"
    if not manifest:
        status = "no_manifest"
    elif manifest.get("size") != stat.st_size:
        status = "size_mismatch"
    return {
        "id": stem, "file": dump.name, "size": stat.st_size,
        "created_at": manifest.get("created_at") or _iso(stat.st_mtime),
        "reason": manifest.get("reason"), "kind": manifest.get("kind"), "memo": manifest.get("memo"),
        "created_by": manifest.get("created_by"), "status": status,
        "app_version": manifest.get("app_version"), "commit": manifest.get("commit"),
        "database": manifest.get("database"), "sha256": manifest.get("sha256"), "format": manifest.get("format"),
    }


def list_backups() -> list[dict[str, Any]]:
    directory = backup_dir()
    if not directory.is_dir():
        return []
    entries = []
    for dump in directory.iterdir():
        if dump.suffix in DUMP_SUFFIXES and not dump.name.startswith(".") and dump.is_file() and not dump.is_symlink() \
                and ID_PATTERN.fullmatch(dump.name[: -len(dump.suffix)]):
            try:
                entries.append(_entry(directory, dump))
            except OSError:
                continue
    entries.sort(key=lambda item: (item["created_at"], item["id"]), reverse=True)
    return entries


def resolve(backup_id: str) -> Path:
    """The dump file of ``backup_id`` (never a path taken from the caller)."""
    if not ID_PATTERN.fullmatch(backup_id or ""):
        raise BackupError("DB_BACKUP_NOT_FOUND", "백업을 찾을 수 없습니다.", status=404)
    directory = backup_dir()
    for suffix in DUMP_SUFFIXES:
        candidate = directory / f"{backup_id}{suffix}"
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    raise BackupError("DB_BACKUP_NOT_FOUND", "백업을 찾을 수 없습니다.", status=404)


def delete_backup(backup_id: str) -> dict[str, Any]:
    dump = resolve(backup_id)
    entry = _entry(dump.parent, dump)
    try:
        dump.unlink()
        (dump.parent / f"{backup_id}{MANIFEST_SUFFIX}").unlink(missing_ok=True)
    except OSError as error:
        raise _storage_error(error, "publish_dump") from error
    return entry


def _prune(directory: Path) -> list[str]:
    """Delete automatic backups beyond ``keep_automatic()``; manual and unrecognised files stay."""
    removed: list[str] = []
    automatic = [item for item in list_backups() if item["kind"] == "automatic"]
    for item in automatic[keep_automatic():]:
        try:
            delete_backup(item["id"])
            removed.append(item["id"])
        except BackupError:
            logger.warning("Old automatic DB backup %s could not be removed", item["id"])
    return removed


def overview() -> dict[str, Any]:
    directory = backup_dir()
    return {"directory": str(directory), "keep_automatic": keep_automatic(), "backend": database_settings().backend,
            "backups": list_backups(), "job": current_job(), "restore_command": restore_hint()}


def restore_hint() -> str:
    return ("pg_restore --host <호스트> --port <포트> --username <소유자 계정> --dbname <빈 DB> "
            "--no-owner --single-transaction --exit-on-error <파일>.dump  (웹 화면에서는 복구하지 않습니다)")


__all__ = ["BackupError", "create_backup", "start_job", "current_job", "list_backups", "resolve", "delete_backup",
           "overview", "backup_dir", "keep_automatic"]
