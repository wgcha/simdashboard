from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from uuid import uuid4
from datetime import datetime, timezone
from pathlib import Path

import psycopg

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.services.media_integrity import media_inventory, require_media_integrity  # noqa: E402
from scripts.account_backup_inventory import account_inventory  # noqa: E402
try:  # pragma: no cover - branch depends on ``python script.py`` invocation
    from .postgres_cli import command_env, connection_args, executable, parse_target
except ImportError:  # pragma: no cover
    from postgres_cli import command_env, connection_args, executable, parse_target


LABEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _validate_label(value: str) -> str:
    """Accept an archive identifier, never a path component or shell fragment."""
    if not LABEL_PATTERN.fullmatch(value):
        raise RuntimeError("backup label은 1~64자의 영숫자·점·밑줄·하이픈 ID만 허용합니다.")
    return value


def _reject_symlink_components(path: Path) -> Path:
    """Return an absolute path only when neither it nor its parents are links."""
    candidate = path.expanduser().absolute()
    current = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        current /= part
        if current.is_symlink():
            raise RuntimeError(f"backup output symlink 경로는 허용하지 않습니다: {current}")
    return candidate


def _output_directory(path: Path) -> Path:
    candidate = _reject_symlink_components(path)
    if candidate.exists() and not candidate.is_dir():
        raise RuntimeError(f"backup output directory가 아닙니다: {candidate}")
    candidate.mkdir(parents=True, exist_ok=True)
    # Check once more after mkdir: a concurrent replacement must not redirect
    # an archive write through a newly introduced symlink.
    return _reject_symlink_components(candidate)


def _path_exists_or_symlink(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _require_unused(path: Path, *, label: str) -> None:
    if _path_exists_or_symlink(path):
        raise FileExistsError(f"{label}는 덮어쓸 수 없습니다: {path}")


def _reserve_unique_partial_path(final_path: Path) -> Path:
    """Create an O_EXCL diagnostic partial without touching another run's residue."""
    for _ in range(16):
        candidate = final_path.with_name(f".{final_path.name}.{uuid4().hex}.partial")
        try:
            descriptor = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        else:
            os.close(descriptor)
            return candidate
    raise RuntimeError("충돌 없는 backup partial 경로를 만들 수 없습니다.")


def _publish_without_overwrite(temporary_path: Path, final_path: Path) -> None:
    """Create the final name atomically, failing if another producer won it."""
    try:
        os.link(temporary_path, final_path)
    except FileExistsError as error:
        raise FileExistsError(f"backup dump는 덮어쓸 수 없습니다: {final_path}") from error
    temporary_path.unlink()


def _write_manifest_without_overwrite(path: Path, manifest: dict[str, object]) -> None:
    encoded = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise FileExistsError(f"backup manifest는 덮어쓸 수 없습니다: {path}") from error
    try:
        offset = 0
        while offset < len(encoded):
            offset += os.write(descriptor, encoded[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _psycopg_url(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _snapshot_inventory(database_url: str) -> tuple[psycopg.Connection, str, dict[str, object], dict[str, object]]:
    """Hold a repeatable-read snapshot open for inventory and ``pg_dump``.

    PostgreSQL keeps an exported snapshot valid only while its exporting
    transaction stays open.  The caller therefore owns and closes the returned
    connection after ``pg_dump --snapshot`` has completed.
    """
    connection = psycopg.connect(_psycopg_url(database_url))
    try:
        connection.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        snapshot = str(connection.execute("SELECT pg_export_snapshot()").fetchone()[0])
        inventory = media_inventory(connection)
        require_media_integrity(inventory)
        accounts = account_inventory(connection)
        return connection, snapshot, inventory, accounts
    except BaseException:
        connection.close()
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Create and verify an Analysis Canvas PostgreSQL custom-format backup.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "backups")
    parser.add_argument("--label", default="analysis-canvas")
    args = parser.parse_args()
    if not args.database_url:
        raise RuntimeError("DATABASE_URL이 필요합니다.")

    label = _validate_label(args.label)
    target = parse_target(args.database_url)
    output_dir = _output_directory(args.output_dir)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final_path = output_dir / f"{label}-{timestamp}.dump"
    manifest_path = final_path.with_suffix(".manifest.json")
    _require_unused(final_path, label="backup dump")
    _require_unused(manifest_path, label="backup manifest")
    environment = command_env(target)
    snapshot_connection, snapshot, inventory, accounts = _snapshot_inventory(args.database_url)
    try:
        temporary_path = _reserve_unique_partial_path(final_path)
        dump_command = [
            executable("pg_dump"), *connection_args(target), "--format=custom", "--compress=6",
            "--no-owner", "--no-privileges", f"--snapshot={snapshot}", "--file", str(temporary_path),
        ]
        subprocess.run(dump_command, env=environment, check=True)
    finally:
        snapshot_connection.close()
    subprocess.run([executable("pg_restore"), "--list", str(temporary_path)], env=environment, check=True, stdout=subprocess.DEVNULL)
    # If pg_dump/pg_restore fails, retain the uniquely named partial for
    # operator inspection.  A later invocation never reuses or overwrites it.
    _publish_without_overwrite(temporary_path, final_path)
    manifest = {
        "format": "postgresql-custom",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database": target.database,
        "filename": final_path.name,
        "bytes": final_path.stat().st_size,
        "sha256": sha256(final_path),
        "media_inventory": inventory,
        "account_inventory": accounts,
    }
    _write_manifest_without_overwrite(manifest_path, manifest)
    print(f"PostgreSQL backup verified: file={final_path}, bytes={manifest['bytes']}, sha256={manifest['sha256']}")
    print(f"manifest={manifest_path}")


if __name__ == "__main__":
    main()
