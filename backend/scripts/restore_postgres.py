from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import psycopg

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.services.media_integrity import media_inventory, require_media_integrity  # noqa: E402
from scripts.account_backup_inventory import account_inventory, require_account_inventory  # noqa: E402
try:  # pragma: no cover - branch depends on ``python script.py`` invocation
    from .postgres_cli import command_env, connection_args, executable, parse_target
except ImportError:  # pragma: no cover
    from postgres_cli import command_env, connection_args, executable, parse_target


_DATABASE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _psycopg_url(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _same_database_target(left, right) -> bool:
    """Do not let an app-role verifier inspect a same-named remote database."""
    return (left.host, left.port, left.database) == (right.host, right.port, right.database)


def _verify_restored_media(*, expected: object, verify_database_url: str) -> dict[str, object]:
    """Compare the app-role view of restored media before starting the app."""
    if not isinstance(expected, dict):
        raise RuntimeError("백업 manifest에 media_inventory가 없습니다.")
    with psycopg.connect(_psycopg_url(verify_database_url)) as connection:
        actual = media_inventory(connection)
    require_media_integrity(actual)
    if actual != expected:
        raise RuntimeError(
            "복구된 media inventory가 백업 manifest와 일치하지 않습니다. 애플리케이션을 시작하지 마세요."
        )
    return actual


def _verify_restored_accounts(*, expected: object, verify_database_url: str) -> dict[str, object]:
    require_account_inventory(expected)
    with psycopg.connect(_psycopg_url(verify_database_url)) as connection:
        actual = account_inventory(connection)
    if actual != expected:
        raise RuntimeError("복구된 계정/전역 관리자/프로젝트 멤버십 inventory가 backup manifest와 일치하지 않습니다.")
    return actual


def _run_database_only_verifier(verify_database_url: str) -> None:
    environment = os.environ.copy()
    environment.update({"ANALYSIS_DB_BACKEND": "postgresql", "DATABASE_URL": verify_database_url})
    subprocess.run(
        [sys.executable, str(BACKEND / "scripts" / "verify_media_database_only.py")],
        cwd=BACKEND,
        env=environment,
        check=True,
    )


def _harden_audit_event_privileges(owner_database_url: str) -> None:
    """Restore append-only audit permissions before the app-role verification.

    ``pg_restore --no-privileges`` deliberately avoids importing source ACLs.
    A target's owner default privileges can otherwise leave UPDATE/DELETE on
    ``audit_events`` available to the application role, so restore must apply
    the canonical hardening with the owner connection explicitly selected.
    """
    environment = os.environ.copy()
    environment.update({
        "DATABASE_URL": owner_database_url,
        "SIM_DASH_APP_ROLE": os.getenv("SIM_DASH_APP_ROLE", "simdashboard_app"),
    })
    subprocess.run(
        [sys.executable, str(BACKEND / "scripts" / "harden_postgres_privileges.py")],
        cwd=BACKEND,
        env=environment,
        check=True,
    )


def _read_verified_manifest(backup: Path) -> dict[str, object]:
    """Validate all external evidence before opening or changing the target."""
    manifest_path = backup.with_suffix(".manifest.json")
    if not manifest_path.is_file():
        raise RuntimeError(f"백업 manifest를 찾지 못했습니다: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("백업 manifest를 읽을 수 없습니다.") from error
    if not isinstance(manifest, dict):
        raise RuntimeError("백업 manifest는 JSON object여야 합니다.")
    if manifest.get("format") == "analysis-canvas-deployment-postgresql":
        raise RuntimeError("이 manifest 형식은 DB와 파일을 함께 복구하는 배포 백업입니다. docs/account-backup-and-recovery.md의 dual-read 복구 절차를 사용하세요.")
    if manifest.get("format") != "postgresql-custom":
        raise RuntimeError("지원하지 않는 백업 manifest 형식입니다.")
    if manifest.get("filename") != backup.name:
        raise RuntimeError("백업 manifest filename이 대상 파일과 일치하지 않습니다.")
    if type(manifest.get("bytes")) is not int or manifest["bytes"] != backup.stat().st_size:
        raise RuntimeError("백업 manifest bytes가 대상 파일과 일치하지 않습니다.")
    if manifest.get("sha256") != sha256(backup):
        raise RuntimeError("백업 SHA-256이 manifest와 일치하지 않습니다.")
    database = manifest.get("database")
    if not isinstance(database, str) or not _DATABASE_IDENTIFIER.fullmatch(database):
        raise RuntimeError("백업 manifest database가 안전한 PostgreSQL 식별자가 아닙니다.")
    try:
        require_media_integrity(manifest.get("media_inventory"))
    except Exception as error:
        raise RuntimeError("백업 manifest media_inventory가 strict 계약을 충족하지 않습니다.") from error
    if "account_inventory" in manifest:
        try:
            require_account_inventory(manifest["account_inventory"])
        except Exception as error:
            raise RuntimeError("백업 manifest account_inventory가 strict 계약을 충족하지 않습니다.") from error
    return manifest


def _verify_backup_archive(backup: Path, target) -> None:
    """Make pg_restore parse the archive before any target connection is used."""
    subprocess.run(
        [executable("pg_restore"), "--list", str(backup)],
        env=command_env(target),
        check=True,
        stdout=subprocess.DEVNULL,
    )


def _verify_staged_backup(staged: Path, manifest: dict[str, object]) -> None:
    """Bind staged bytes to the already-verified immutable manifest values."""
    if staged.stat().st_size != manifest["bytes"] or sha256(staged) != manifest["sha256"]:
        raise RuntimeError("private restore staging copy가 backup manifest와 일치하지 않습니다.")


@contextmanager
def _private_staged_backup(backup: Path, manifest: dict[str, object]) -> Iterator[Path]:
    """Copy once into a 0700 temporary directory and restore only those bytes."""
    with tempfile.TemporaryDirectory(prefix="simdashboard-restore-") as temporary_directory:
        staged = Path(temporary_directory) / "archive.dump"
        try:
            with backup.open("rb") as source, staged.open("xb") as destination:
                shutil.copyfileobj(source, destination, length=1024 * 1024)
                destination.flush()
                os.fsync(destination.fileno())
            os.chmod(staged, 0o600)
        except OSError as error:
            raise RuntimeError("private restore staging copy를 만들 수 없습니다.") from error
        _verify_staged_backup(staged, manifest)
        yield staged


def _restore_command(target, backup: Path, *, clean: bool) -> list[str]:
    """Build the non-partial restore invocation for an already-selected DB.

    ``pg_restore --single-transaction`` is deliberately paired with
    ``--exit-on-error``.  This CLI never offers ``--create``: PostgreSQL does
    not permit that option with a single transaction, and a target database
    must already have passed the explicit confirmation gate above.
    """
    command = [
        executable("pg_restore"),
        *connection_args(target),
        "--single-transaction",
        "--exit-on-error",
        "--no-owner",
        "--no-privileges",
    ]
    if clean:
        command.extend(["--clean", "--if-exists"])
    command.append(str(backup))
    if "--create" in command:
        raise AssertionError("restore command must never combine --create with --single-transaction")
    return command


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify and restore an Analysis Canvas PostgreSQL custom-format backup.")
    parser.add_argument("backup", type=Path)
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument(
        "--verify-database-url",
        default=os.getenv("SIMDASH_APP_DATABASE_URL"),
        help="application-role URL used for post-restore inventory and runtime-gate verification",
    )
    parser.add_argument("--confirm-database", required=True, help="Must exactly match the target DB name.")
    parser.add_argument("--clean", action="store_true", help="Drop objects recorded in the archive before restoring.")
    args = parser.parse_args()
    if not args.database_url:
        raise RuntimeError("DATABASE_URL이 필요합니다.")
    if not args.verify_database_url:
        raise RuntimeError("--verify-database-url 또는 SIMDASH_APP_DATABASE_URL이 필요합니다.")
    requested_backup = args.backup.expanduser()
    if requested_backup.is_symlink() or not requested_backup.is_file():
        raise RuntimeError(f"backup은 regular non-symlink file이어야 합니다: {requested_backup}")
    backup = requested_backup.resolve(strict=True)
    target = parse_target(args.database_url)
    verify_target = parse_target(args.verify_database_url)
    if args.confirm_database != target.database:
        raise RuntimeError("--confirm-database가 실제 대상 DB명과 일치하지 않아 복구를 중단했습니다.")
    if not _same_database_target(target, verify_target):
        raise RuntimeError("--verify-database-url은 복구 대상과 같은 host, port, 데이터베이스여야 합니다.")

    manifest = _read_verified_manifest(backup)
    with _private_staged_backup(backup, manifest) as staged_backup:
        _verify_backup_archive(staged_backup, target)

        connection_url = args.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        with psycopg.connect(connection_url) as connection:
            user_tables = connection.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'"
            ).fetchone()[0]
        if user_tables and not args.clean:
            raise RuntimeError(f"대상 DB에 테이블 {user_tables}개가 있습니다. 빈 DB를 사용하거나 명시적으로 --clean을 지정하세요.")

        command = _restore_command(target, staged_backup, clean=args.clean)
        subprocess.run(command, env=command_env(target), check=True)
    _harden_audit_event_privileges(args.database_url)
    inventory = _verify_restored_media(
        expected=manifest["media_inventory"], verify_database_url=args.verify_database_url
    )
    if "account_inventory" in manifest:
        _verify_restored_accounts(expected=manifest["account_inventory"], verify_database_url=args.verify_database_url)
    _run_database_only_verifier(args.verify_database_url)
    print(
        "PostgreSQL restore completed, media inventory matched, and database-only verification passed: "
        f"database={target.database}, backup={backup}, media_catalog_sha256={inventory['catalog_sha256']}"
    )


if __name__ == "__main__":
    main()
