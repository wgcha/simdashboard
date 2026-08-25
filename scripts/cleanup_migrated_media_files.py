from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

from app.database import connect  # noqa: E402
from app.services.media_integrity import media_inventory, require_media_integrity  # noqa: E402
from postgres_cli import executable  # noqa: E402


ASSET_ROOT = (ROOT / "backend" / "assets").resolve()
MIGRATION_RECEIPT_FORMAT = "simdashboard-media-migration-receipt"
CLEANUP_RECEIPT_FORMAT = "simdashboard-media-cleanup-receipt"
RECEIPT_FORMAT_VERSION = 1
MIN_BACKUP_AGE = timedelta(days=7)
APPROVAL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_utc_timestamp(value: object, *, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"{field}은 UTC timestamp여야 합니다.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise RuntimeError(f"{field} timestamp 형식이 올바르지 않습니다.") from error
    if parsed.tzinfo is None:
        raise RuntimeError(f"{field}은 timezone을 포함해야 합니다.")
    return parsed.astimezone(timezone.utc)


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    candidate = path.expanduser()
    if not candidate.is_file() or candidate.is_symlink():
        raise RuntimeError(f"{label} 파일을 읽을 수 없습니다: {candidate}")
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{label} JSON이 올바르지 않습니다: {candidate}") from error
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} JSON object가 필요합니다.")
    return payload


def _validate_approval_id(value: str) -> str:
    if not APPROVAL_ID_PATTERN.fullmatch(value):
        raise RuntimeError("approval ID는 3~128자의 영숫자·점·밑줄·콜론·하이픈만 허용합니다.")
    return value


def _validate_migration_receipt(payload: dict[str, Any]) -> tuple[str, datetime, list[dict[str, Any]]]:
    if payload.get("format") != MIGRATION_RECEIPT_FORMAT or payload.get("format_version") != RECEIPT_FORMAT_VERSION:
        raise RuntimeError("지원하지 않는 migration receipt 형식입니다.")
    if payload.get("state") != "COMPLETED":
        raise RuntimeError("COMPLETED migration receipt만 cleanup에 사용할 수 있습니다.")
    migration_id = payload.get("migration_id")
    if not isinstance(migration_id, str) or not migration_id:
        raise RuntimeError("migration receipt에 migration_id가 없습니다.")
    executed_at = _parse_utc_timestamp(payload.get("executed_at"), field="migration receipt executed_at")
    items = payload.get("media")
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise RuntimeError("migration receipt media 목록이 올바르지 않습니다.")
    return migration_id, executed_at, items


def _validate_backup_manifest(payload: dict[str, Any], *, migration_executed_at: datetime, now: datetime) -> dict[str, Any]:
    if payload.get("format") != "postgresql-custom":
        raise RuntimeError("지원하지 않는 backup manifest 형식입니다.")
    created_at = _parse_utc_timestamp(payload.get("created_at"), field="backup manifest created_at")
    checksum = payload.get("sha256")
    if not isinstance(checksum, str) or not SHA256_PATTERN.fullmatch(checksum):
        raise RuntimeError("검증된 backup manifest SHA-256이 필요합니다.")
    inventory = payload.get("media_inventory")
    if not isinstance(inventory, dict):
        raise RuntimeError("backup manifest에 media_inventory가 없습니다.")
    if created_at < migration_executed_at:
        raise RuntimeError("backup manifest는 migration executed_at 이후에 생성되어야 합니다.")
    if now < created_at + MIN_BACKUP_AGE:
        raise RuntimeError("검증된 backup 이후 최소 7일이 지나야 legacy source를 cleanup할 수 있습니다.")
    return inventory


def verify_backup_evidence(path: Path, backup_manifest: dict[str, Any]) -> Path:
    """Verify the archive named by a manifest before querying DB or deleting.

    The manifest is only an assertion until the actual custom-format dump has
    been streamed and matched by its safe basename, byte count, and digest.
    """
    candidate = path.expanduser()
    if candidate.is_symlink() or not candidate.is_file():
        raise RuntimeError(f"backup archive는 regular non-symlink file이어야 합니다: {candidate}")
    try:
        metadata = candidate.stat()
    except OSError as error:
        raise RuntimeError(f"backup archive를 읽을 수 없습니다: {candidate}") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError(f"backup archive는 regular file이어야 합니다: {candidate}")
    expected_name = backup_manifest.get("filename")
    expected_size = backup_manifest.get("bytes")
    expected_sha256 = backup_manifest.get("sha256")
    if (
        not isinstance(expected_name, str)
        or not expected_name
        or Path(expected_name).name != expected_name
        or isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or expected_size <= 0
        or not isinstance(expected_sha256, str)
        or not SHA256_PATTERN.fullmatch(expected_sha256)
    ):
        raise RuntimeError("backup manifest의 filename/bytes/SHA-256 증적이 올바르지 않습니다.")
    actual_size, actual_sha256 = _fingerprint(candidate)
    if (candidate.name, actual_size, actual_sha256) != (expected_name, expected_size, expected_sha256):
        raise RuntimeError("backup archive가 backup manifest의 filename/bytes/SHA-256과 일치하지 않습니다.")
    return candidate


def _verify_backup_archive(path: Path) -> None:
    """Make pg_restore parse the custom archive before DB access or deletion."""
    subprocess.run(
        [executable("pg_restore"), "--list", str(path)],
        env=os.environ.copy(),
        check=True,
        stdout=subprocess.DEVNULL,
    )


def _verify_staged_backup(path: Path, backup_manifest: dict[str, Any]) -> None:
    expected_size = backup_manifest["bytes"]
    expected_sha256 = backup_manifest["sha256"]
    actual_size, actual_sha256 = _fingerprint(path)
    if (actual_size, actual_sha256) != (expected_size, expected_sha256):
        raise RuntimeError("private cleanup backup staging copy가 manifest와 일치하지 않습니다.")


@contextmanager
def _private_staged_backup(backup: Path, backup_manifest: dict[str, Any]) -> Iterator[Path]:
    """Copy verified bytes once; archive parsing and DB planning use only it."""
    with tempfile.TemporaryDirectory(prefix="simdashboard-cleanup-backup-") as temporary_directory:
        staged = Path(temporary_directory) / "archive.dump"
        try:
            with backup.open("rb") as source, staged.open("xb") as destination:
                shutil.copyfileobj(source, destination, length=1024 * 1024)
                destination.flush()
                os.fsync(destination.fileno())
            os.chmod(staged, 0o600)
        except OSError as error:
            raise RuntimeError("private cleanup backup staging copy를 만들 수 없습니다.") from error
        _verify_staged_backup(staged, backup_manifest)
        yield staged


def _relative_legacy_path(raw: str) -> Path:
    candidate = Path(raw)
    if not raw or "\x00" in raw or candidate.is_absolute() or candidate.drive:
        raise RuntimeError(f"legacy path가 assets root 밖입니다: {raw!r}")
    parts = candidate.parts
    if parts and parts[0].lower() == "assets":
        parts = parts[1:]
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise RuntimeError(f"legacy path가 안전하지 않습니다: {raw!r}")
    if parts[0].casefold() == "video_example":
        raise RuntimeError("demo/video_example는 legacy cleanup 대상이 아닙니다.")
    return Path(*parts)


def safe_path(raw: str) -> Path:
    """Resolve a private legacy asset while rejecting every symlink component."""
    root = ASSET_ROOT
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError(f"legacy assets root가 안전하지 않습니다: {root}")
    relative = _relative_legacy_path(raw)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise RuntimeError(f"legacy path symlink는 cleanup할 수 없습니다: {current}")
    resolved_root = root.resolve(strict=True)
    resolved = current.resolve(strict=False)
    if resolved_root not in resolved.parents:
        raise RuntimeError(f"legacy path가 assets root 밖입니다: {raw!r}")
    return resolved


def _fingerprint(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            total += len(block)
            digest.update(block)
    return total, digest.hexdigest()


def _fingerprint_descriptor(descriptor: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    while True:
        block = os.read(descriptor, 1024 * 1024)
        if not block:
            break
        total += len(block)
        digest.update(block)
    return total, digest.hexdigest()


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _require_secure_dirfd_support() -> None:
    required = (os.open, os.stat, os.mkdir, os.rename, os.unlink, os.rmdir)
    if (
        not hasattr(os, "O_NOFOLLOW")
        or not hasattr(os, "O_DIRECTORY")
        or not all(operation in os.supports_dir_fd for operation in required)
    ):
        raise RuntimeError("secure cleanup rename은 이 플랫폼에서 지원되지 않습니다.")


def _open_regular_at(parent_descriptor: int, name: str) -> tuple[int, os.stat_result, tuple[int, str]]:
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_descriptor)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError("legacy source는 regular file이어야 합니다.")
        fingerprint = _fingerprint_descriptor(descriptor)
        return descriptor, metadata, fingerprint
    except BaseException:
        os.close(descriptor)
        raise


def _open_secure_parent(logical_path: str) -> tuple[int, str, list[int]]:
    """Walk from the assets-root dirfd without ever reopening an absolute parent."""
    relative = _relative_legacy_path(logical_path)
    descriptors: list[int] = []
    try:
        current = os.open(ASSET_ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(current)
        for component in relative.parts[:-1]:
            current = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current,
            )
            descriptors.append(current)
        return current, relative.name, descriptors
    except OSError as error:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise RuntimeError(f"legacy source의 secure parent traversal을 거부했습니다: {logical_path}") from error


def _receipt_item(item: dict[str, Any]) -> tuple[str, str, str, int, str]:
    asset_id = item.get("asset_id")
    logical_path = item.get("legacy_logical_path")
    blob_id = item.get("blob_id")
    source_sha256 = item.get("source_sha256")
    source_size = item.get("source_size")
    if not all(isinstance(value, str) and value for value in (asset_id, logical_path, blob_id, source_sha256)):
        raise RuntimeError("migration receipt media 항목의 ID/path/checksum이 올바르지 않습니다.")
    if not SHA256_PATTERN.fullmatch(source_sha256):
        raise RuntimeError("migration receipt media SHA-256이 올바르지 않습니다.")
    if isinstance(source_size, bool) or not isinstance(source_size, int) or source_size < 0:
        raise RuntimeError("migration receipt media source_size가 올바르지 않습니다.")
    return asset_id, logical_path, blob_id, source_size, source_sha256


def build_cleanup_plan(*, migration_receipt: dict[str, Any], backup_manifest: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Validate every gate and source before any deletion can start."""
    current_time = (now or utc_now()).astimezone(timezone.utc)
    migration_id, migration_executed_at, entries = _validate_migration_receipt(migration_receipt)
    expected_inventory = _validate_backup_manifest(backup_manifest, migration_executed_at=migration_executed_at, now=current_time)
    candidates: dict[Path, dict[str, Any]] = {}
    with connect() as connection:
        current_inventory = media_inventory(connection)
        require_media_integrity(current_inventory)
        if current_inventory != expected_inventory:
            raise RuntimeError("현재 media inventory가 검증된 backup manifest와 일치하지 않습니다.")
        for entry in entries:
            asset_id, logical_path, blob_id, source_size, source_sha256 = _receipt_item(entry)
            row = connection.execute(
                """
                SELECT m.file_path, m.blob_id, b.sha256, b.file_size
                FROM media_assets m JOIN asset_blobs b ON b.id=m.blob_id
                WHERE m.id=?
                """,
                [asset_id],
            ).fetchone()
            if row is None:
                raise RuntimeError(f"migration receipt asset이 현재 DB blob에 연결되어 있지 않습니다: {asset_id}")
            if (str(row[0]), str(row[1]), str(row[2]), int(row[3])) != (logical_path, blob_id, source_sha256, source_size):
                raise RuntimeError(f"migration receipt asset과 현재 DB blob이 일치하지 않습니다: {asset_id}")
            path = safe_path(logical_path)
            if not path.is_file():
                raise RuntimeError(f"legacy source 파일을 찾을 수 없습니다: {path}")
            actual_size, actual_sha256 = _fingerprint(path)
            if (actual_size, actual_sha256) != (source_size, source_sha256):
                raise RuntimeError(f"legacy source가 migration receipt 이후 변경되었습니다: {path}")
            existing = candidates.get(path)
            if existing is None:
                candidates[path] = {"path": path, "legacy_logical_path": logical_path, "source_size": source_size, "source_sha256": source_sha256, "asset_ids": [asset_id]}
            elif (existing["source_size"], existing["source_sha256"]) != (source_size, source_sha256):
                raise RuntimeError(f"동일 legacy path의 migration receipt 증적이 충돌합니다: {logical_path}")
            else:
                existing["asset_ids"].append(asset_id)
    return {
        "migration_id": migration_id,
        "migration_executed_at": migration_executed_at.isoformat().replace("+00:00", "Z"),
        "backup_created_at": _parse_utc_timestamp(backup_manifest["created_at"], field="backup manifest created_at").isoformat().replace("+00:00", "Z"),
        "candidates": [candidates[path] for path in sorted(candidates, key=lambda value: str(value))],
    }


def execute_cleanup(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Quarantine with dirfd/O_NOFOLLOW, then delete only the same verified inode."""
    _require_secure_dirfd_support()

    def validate_candidate(candidate: dict[str, Any]) -> Path:
        path = Path(candidate["path"])
        if safe_path(str(candidate["legacy_logical_path"])) != path:
            raise RuntimeError(f"legacy source 경로가 cleanup plan 이후 변경되었습니다: {path}")
        actual_size, actual_sha256 = _fingerprint(path)
        if (actual_size, actual_sha256) != (candidate["source_size"], candidate["source_sha256"]):
            raise RuntimeError(f"legacy source가 cleanup 사전검사 이후 변경되었습니다: {path}")
        return path

    # Validate every candidate before moving the first source.  Per-file work
    # below repeats this through a no-follow descriptor and its final inode.
    for candidate in plan["candidates"]:
        validate_candidate(candidate)

    deleted: list[dict[str, Any]] = []
    for candidate in plan["candidates"]:
        path = validate_candidate(candidate)
        parent_descriptor = -1
        parent_descriptors: list[int] = []
        quarantine_name = f".simdashboard-cleanup-quarantine-{uuid4().hex}"
        source_descriptor = -1
        quarantine_descriptor = -1
        quarantine_created = False
        moved = False
        try:
            parent_descriptor, source_name, parent_descriptors = _open_secure_parent(
                str(candidate["legacy_logical_path"])
            )
            source_descriptor, source_metadata, source_fingerprint = _open_regular_at(parent_descriptor, source_name)
            if source_fingerprint != (candidate["source_size"], candidate["source_sha256"]):
                raise RuntimeError(f"legacy source가 삭제 직전 변경되었습니다: {path}")
            current_metadata = os.stat(source_name, dir_fd=parent_descriptor, follow_symlinks=False)
            if not _same_inode(source_metadata, current_metadata):
                raise RuntimeError(f"legacy source inode가 삭제 직전 변경되었습니다: {path}")

            os.mkdir(quarantine_name, 0o700, dir_fd=parent_descriptor)
            quarantine_created = True
            quarantine_descriptor = os.open(
                quarantine_name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent_descriptor,
            )
            os.rename(source_name, source_name, src_dir_fd=parent_descriptor, dst_dir_fd=quarantine_descriptor)
            moved = True
            quarantined_metadata = os.stat(source_name, dir_fd=quarantine_descriptor, follow_symlinks=False)
            if not _same_inode(source_metadata, quarantined_metadata):
                raise RuntimeError(
                    f"legacy source inode가 quarantine 중 변경되어 보존했습니다: {path.parent / quarantine_name / source_name}"
                )
            quarantine_file, final_metadata, final_fingerprint = _open_regular_at(quarantine_descriptor, source_name)
            try:
                if not _same_inode(source_metadata, final_metadata) or final_fingerprint != source_fingerprint:
                    raise RuntimeError(
                        f"legacy source가 quarantine 뒤 변경되어 보존했습니다: {path.parent / quarantine_name / source_name}"
                    )
            finally:
                os.close(quarantine_file)
            os.unlink(source_name, dir_fd=quarantine_descriptor)
            os.rmdir(quarantine_name, dir_fd=parent_descriptor)
            deleted.append({"legacy_logical_path": candidate["legacy_logical_path"], "asset_ids": candidate["asset_ids"], "source_size": candidate["source_size"], "source_sha256": candidate["source_sha256"]})
        except BaseException as error:
            if moved:
                raise RuntimeError(
                    f"legacy source를 삭제하지 않고 quarantine에 보존했습니다: {path.parent / quarantine_name / source_name}"
                ) from error
            raise
        finally:
            if source_descriptor >= 0:
                os.close(source_descriptor)
            if quarantine_descriptor >= 0:
                os.close(quarantine_descriptor)
            if quarantine_created and not moved:
                try:
                    os.rmdir(quarantine_name, dir_fd=parent_descriptor)
                except OSError:
                    pass
            for descriptor in reversed(parent_descriptors):
                os.close(descriptor)
    return deleted


def _cleanup_receipt_destination(path: Path) -> Path:
    candidate = path.expanduser()
    if not candidate.name or candidate.name in {".", ".."} or not candidate.parent.is_dir():
        raise RuntimeError("cleanup receipt의 기존 디렉터리와 파일명이 필요합니다.")
    destination = candidate.parent / candidate.name
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"cleanup receipt는 덮어쓸 수 없습니다: {destination}")
    return destination


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class CleanupReceiptReservation:
    """O_EXCL receipt reservation; PENDING/FAILED remains recoverable evidence."""

    def __init__(self, path: Path, descriptor: int, cleanup_id: str, started_at: datetime) -> None:
        self.path = path
        self.descriptor = descriptor
        self.cleanup_id = cleanup_id
        self.started_at = started_at

    def _write(self, payload: dict[str, Any]) -> None:
        encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        os.ftruncate(self.descriptor, 0)
        os.lseek(self.descriptor, 0, os.SEEK_SET)
        offset = 0
        while offset < len(encoded):
            offset += os.write(self.descriptor, encoded[offset:])
        os.fsync(self.descriptor)
        _fsync_directory(self.path.parent)

    def finalize(self, payload: dict[str, Any]) -> None:
        self._write(payload)

    def fail(self, error: BaseException) -> None:
        self._write({"format": CLEANUP_RECEIPT_FORMAT, "format_version": RECEIPT_FORMAT_VERSION, "cleanup_id": self.cleanup_id, "started_at": self.started_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"), "state": "FAILED", "failed_at": utc_now().isoformat().replace("+00:00", "Z"), "error": str(error)})

    def close(self) -> None:
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1


def reserve_cleanup_receipt(path: Path, *, cleanup_id: str, started_at: datetime) -> CleanupReceiptReservation:
    destination = _cleanup_receipt_destination(path)
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise FileExistsError(f"cleanup receipt는 덮어쓸 수 없습니다: {destination}") from error
    reservation = CleanupReceiptReservation(destination, descriptor, cleanup_id, started_at)
    try:
        os.fchmod(descriptor, 0o600)
        reservation._write({"format": CLEANUP_RECEIPT_FORMAT, "format_version": RECEIPT_FORMAT_VERSION, "cleanup_id": cleanup_id, "started_at": started_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"), "state": "PENDING"})
    except BaseException:
        reservation.close()
        raise
    return reservation


def main() -> int:
    parser = argparse.ArgumentParser(description="Explicitly clean legacy files after a verified media migration.")
    parser.add_argument("--migration-receipt", type=Path, required=True)
    parser.add_argument("--backup-manifest", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True, help="actual PostgreSQL custom-format dump named by the manifest")
    parser.add_argument("--approval-id", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm", action="store_true", help="required with --execute")
    parser.add_argument("--migration-id", help="must exactly match the migration receipt when executing")
    parser.add_argument("--cleanup-receipt", type=Path, help="O_EXCL cleanup evidence path; required with --execute")
    args = parser.parse_args()
    approval_id = _validate_approval_id(args.approval_id)
    if args.execute and not args.confirm:
        parser.error("파일 삭제에는 --execute --confirm 두 옵션이 모두 필요합니다.")
    if args.execute and not args.migration_id:
        parser.error("--execute에는 --migration-id가 필요합니다.")
    if args.execute and args.cleanup_receipt is None:
        parser.error("--execute에는 --cleanup-receipt PATH가 필요합니다.")
    if not args.execute and (args.cleanup_receipt is not None or args.migration_id is not None):
        parser.error("--cleanup-receipt와 --migration-id는 --execute와 함께만 사용할 수 있습니다.")
    migration_receipt = _read_json(args.migration_receipt, label="migration receipt")
    backup_manifest = _read_json(args.backup_manifest, label="backup manifest")
    # Reject malformed/too-young evidence before touching the database.  The
    # full plan repeats these checks with its immutable planning input.
    _migration_id, migration_executed_at, _entries = _validate_migration_receipt(migration_receipt)
    _validate_backup_manifest(
        backup_manifest,
        migration_executed_at=migration_executed_at,
        now=utc_now(),
    )
    # This deliberately precedes build_cleanup_plan() (and therefore connect):
    # a manifest alone must never authorize a DB read or source deletion.
    verified_backup = verify_backup_evidence(args.backup, backup_manifest)
    with _private_staged_backup(verified_backup, backup_manifest) as staged_backup:
        _verify_backup_archive(staged_backup)
        plan = build_cleanup_plan(migration_receipt=migration_receipt, backup_manifest=backup_manifest)
    if args.execute and plan["migration_id"] != args.migration_id:
        raise RuntimeError("--migration-id가 migration receipt와 일치하지 않습니다.")
    if args.execute and not plan["candidates"]:
        raise RuntimeError("삭제할 legacy media receipt 항목이 없어 execute를 거부합니다.")
    print(json.dumps({"mode": "execute" if args.execute else "dry-run", "approval_id": approval_id, **plan}, ensure_ascii=False, indent=2, default=str))
    if not args.execute:
        return 0
    started_at = utc_now()
    reservation = reserve_cleanup_receipt(args.cleanup_receipt, cleanup_id=str(uuid4()), started_at=started_at)
    try:
        deleted = execute_cleanup(plan)
        reservation.finalize({"format": CLEANUP_RECEIPT_FORMAT, "format_version": RECEIPT_FORMAT_VERSION, "state": "COMPLETED", "cleanup_id": reservation.cleanup_id, "migration_id": plan["migration_id"], "approval_id": approval_id, "migration_executed_at": plan["migration_executed_at"], "backup_created_at": plan["backup_created_at"], "executed_at": utc_now().isoformat().replace("+00:00", "Z"), "deleted": deleted})
        print(f"removed={len(deleted)} cleanup_receipt={reservation.path}")
    except BaseException as error:
        try:
            reservation.fail(error)
        finally:
            reservation.close()
        raise
    else:
        reservation.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        raise SystemExit(1)
