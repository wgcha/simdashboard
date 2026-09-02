from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.database import connect, initialize_database  # noqa: E402
from app.services.drop_video_demo import DROP_VIDEO_DEMO_SCENES, DROP_VIDEO_SOURCE_DIR, probe_mp4  # noqa: E402
from app.services.media_storage_service import attach_stored_media, inspect_file, store_file  # noqa: E402


ASSET_ROOT = (ROOT / "backend" / "assets").resolve()
RECEIPT_FORMAT = "simdashboard-media-migration-receipt"
RECEIPT_FORMAT_VERSION = 1


def _reject_symlink_path(path: Path, root: Path) -> None:
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise RuntimeError(f"media migration source may not be a symlink: {current}")


def asset_source(raw_path: str) -> Path:
    parts = Path(raw_path).parts
    relative = Path(*parts[1:]) if parts and parts[0].lower() == "assets" else Path(raw_path)
    candidate = ASSET_ROOT / relative
    _reject_symlink_path(candidate, ASSET_ROOT)
    resolved = candidate.resolve()
    if ASSET_ROOT not in resolved.parents:
        raise RuntimeError(f"legacy asset path escaped the private root: {raw_path}")
    return resolved


def plan_targets(connection: Any, *, include_demo: bool) -> dict[str, Any]:
    media = []
    for row in connection.execute(
        "SELECT id, file_path, asset_type, mime_type, original_filename, blob_id "
        "FROM media_assets WHERE blob_id IS NULL ORDER BY id"
    ).fetchall():
        asset_id, file_path, asset_type, mime_type, original_filename, _ = row
        source = asset_source(str(file_path))
        if not source.is_file():
            raise RuntimeError(f"legacy media file is missing: {source}")
        filename = str(original_filename or source.name)
        inspection = inspect_file(source, filename=filename, mime_type=str(mime_type), asset_type=str(asset_type))
        media.append({
            "asset_id": str(asset_id),
            "legacy_logical_path": str(file_path),
            "source": str(source),
            "filename": filename,
            "asset_type": str(asset_type),
            "mime_type": str(mime_type),
            "file_size": inspection.file_size,
            "sha256": inspection.sha256,
        })

    demo = []
    if include_demo:
        if len(DROP_VIDEO_DEMO_SCENES) != 20:
            raise RuntimeError(f"demo allowlist must contain exactly 20 files, found {len(DROP_VIDEO_DEMO_SCENES)}")
        for scene in DROP_VIDEO_DEMO_SCENES:
            existing = connection.execute("SELECT blob_id FROM drop_video_assets WHERE video_id=?", [scene.video_id]).fetchone()
            # A fully connected demo is already canonical. Do not require its
            # legacy fixture to remain present merely to prove a no-op rerun.
            if existing is not None and existing[0] is not None:
                continue
            candidate = DROP_VIDEO_SOURCE_DIR / scene.filename
            _reject_symlink_path(candidate, DROP_VIDEO_SOURCE_DIR)
            source = candidate.resolve()
            if source.parent != DROP_VIDEO_SOURCE_DIR.resolve() or not source.is_file():
                raise RuntimeError(f"demo media file is missing: {source}")
            inspection = inspect_file(source, filename=scene.filename, mime_type="video/mp4", asset_type="VIDEO")
            media_probe = probe_mp4(source)
            demo.append({
                "video_id": scene.video_id,
                "load_case_id": "loadcase-drop-bottom-001",
                "legacy_logical_path": str(Path(DROP_VIDEO_SOURCE_DIR.name) / scene.filename),
                "scene_name": scene.scene_name,
                "sort_order": scene.sort_order,
                "source": str(source),
                "filename": scene.filename,
                "mime_type": "video/mp4",
                "asset_type": "VIDEO",
                "file_size": inspection.file_size,
                "sha256": inspection.sha256,
                "codec": media_probe.codec,
                "fast_start": media_probe.fast_start,
            })
    return {"media": media, "demo": demo, "total": len(media) + len(demo)}


@contextmanager
def _transaction(connection: Any):
    """Use one transaction for every migration item on both DB adapters."""
    connection.execute("BEGIN TRANSACTION")
    try:
        yield
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")


def _already_attached_media_blob_id(connection: Any, asset_id: str) -> str | None:
    row = connection.execute("SELECT blob_id FROM media_assets WHERE id=?", [asset_id]).fetchone()
    if row is None:
        raise ValueError("미디어 asset을 찾을 수 없습니다.")
    return str(row[0]) if row[0] is not None else None


def _demo_blob_state(connection: Any, video_id: str) -> tuple[bool, str | None]:
    row = connection.execute("SELECT blob_id FROM drop_video_assets WHERE video_id=?", [video_id]).fetchone()
    if row is None:
        return False, None
    return True, str(row[0]) if row[0] is not None else None


def _verify_existing_blob_matches_plan(connection: Any, blob_id: str, item: dict[str, Any], *, label: str) -> None:
    row = connection.execute("SELECT sha256, file_size FROM asset_blobs WHERE id=?", [blob_id]).fetchone()
    if row is None:
        raise RuntimeError(f"{label} 연결 blob을 찾을 수 없습니다: {blob_id}")
    if (str(row[0]), int(row[1])) != (str(item["sha256"]), int(item["file_size"])):
        raise RuntimeError(f"{label}는 preflight source와 다른 blob에 이미 연결되어 있습니다: {blob_id}")


def _receipt_item(item: dict[str, Any], *, blob_id: str, outcome: str) -> dict[str, Any]:
    return {
        "asset_id": item["asset_id"],
        "legacy_logical_path": item["legacy_logical_path"],
        "source_sha256": item["sha256"],
        "source_size": item["file_size"],
        "blob_id": blob_id,
        "outcome": outcome,
    }


def _demo_receipt_item(item: dict[str, Any], *, blob_id: str, outcome: str) -> dict[str, Any]:
    return {
        "video_id": item["video_id"],
        "load_case_id": item["load_case_id"],
        "legacy_logical_path": item["legacy_logical_path"],
        "source_sha256": item["sha256"],
        "source_size": item["file_size"],
        "blob_id": blob_id,
        "outcome": outcome,
    }


def execute(plan: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Migrate an entire preflighted plan atomically and return receipt rows."""
    results: dict[str, list[dict[str, Any]]] = {"media": [], "demo": []}
    with connect() as connection:
        with _transaction(connection):
            for item in plan["media"]:
                existing_blob_id = _already_attached_media_blob_id(connection, item["asset_id"])
                if existing_blob_id is not None:
                    _verify_existing_blob_matches_plan(connection, existing_blob_id, item, label="media asset")
                    results["media"].append(_receipt_item(item, blob_id=existing_blob_id, outcome="already_connected"))
                    continue
                inspected = inspect_file(Path(item["source"]), filename=item["filename"], mime_type=item["mime_type"], asset_type=item["asset_type"])
                if (inspected.file_size, inspected.sha256) != (item["file_size"], item["sha256"]):
                    raise RuntimeError(f"media changed after preflight: {item['source']}")
                stored = store_file(connection, Path(item["source"]), filename=item["filename"], mime_type=item["mime_type"], asset_type=item["asset_type"])
                if (stored.blob.file_size, stored.blob.sha256) != (item["file_size"], item["sha256"]):
                    raise RuntimeError(f"media changed while being migrated: {item['source']}")
                attach_stored_media(connection, item["asset_id"], stored)
                results["media"].append(_receipt_item(item, blob_id=stored.blob.id, outcome="migrated"))
            for item in plan["demo"]:
                exists, existing_blob_id = _demo_blob_state(connection, item["video_id"])
                if existing_blob_id is not None:
                    _verify_existing_blob_matches_plan(connection, existing_blob_id, item, label="demo video")
                    results["demo"].append(_demo_receipt_item(item, blob_id=existing_blob_id, outcome="already_connected"))
                    continue
                inspected = inspect_file(Path(item["source"]), filename=item["filename"], mime_type=item["mime_type"], asset_type=item["asset_type"])
                if (inspected.file_size, inspected.sha256) != (item["file_size"], item["sha256"]):
                    raise RuntimeError(f"demo media changed after preflight: {item['source']}")
                stored = store_file(connection, Path(item["source"]), filename=item["filename"], mime_type=item["mime_type"], asset_type=item["asset_type"])
                if (stored.blob.file_size, stored.blob.sha256) != (item["file_size"], item["sha256"]):
                    raise RuntimeError(f"demo media changed while being migrated: {item['source']}")
                connection.execute("UPDATE asset_blobs SET orphaned_at=NULL WHERE id=?", [stored.blob.id])
                metadata_json = json.dumps({"demo": True, "codec": item["codec"], "fast_start": item["fast_start"]})
                if exists:
                    connection.execute(
                        """
                        UPDATE drop_video_assets
                        SET blob_id=?, original_filename=?, mime_type=?, scene_name=?, sort_order=?, metadata_json=?
                        WHERE video_id=? AND blob_id IS NULL
                        """,
                        [stored.blob.id, item["filename"], item["mime_type"], item["scene_name"], item["sort_order"], metadata_json, item["video_id"]],
                    )
                else:
                    connection.execute(
                        """
                        INSERT INTO drop_video_assets
                            (video_id, load_case_id, blob_id, original_filename, mime_type, scene_name, sort_order, metadata_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT (video_id) DO NOTHING
                        """,
                        [item["video_id"], item["load_case_id"], stored.blob.id, item["filename"], item["mime_type"], item["scene_name"], item["sort_order"], metadata_json],
                    )
                _final_exists, final_blob_id = _demo_blob_state(connection, item["video_id"])
                if final_blob_id is None:
                    raise RuntimeError(f"demo media attachment failed: {item['video_id']}")
                _verify_existing_blob_matches_plan(connection, final_blob_id, item, label="demo video")
                outcome = "migrated" if final_blob_id == stored.blob.id else "already_connected"
                results["demo"].append(_demo_receipt_item(item, blob_id=final_blob_id, outcome=outcome))
    return results


def build_receipt(
    plan: dict[str, Any],
    results: dict[str, list[dict[str, Any]]],
    *,
    migration_id: str,
    executed_at: datetime,
) -> dict[str, Any]:
    return {
        "format": RECEIPT_FORMAT,
        "format_version": RECEIPT_FORMAT_VERSION,
        "state": "COMPLETED",
        "migration_id": migration_id,
        "executed_at": executed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "planned_total": int(plan["total"]),
        "media": results["media"],
        "demo": results["demo"],
    }


def _receipt_destination(path: Path) -> Path:
    candidate = path.expanduser()
    if not candidate.name or candidate.name in {".", ".."}:
        raise RuntimeError("receipt 파일명은 필요합니다.")
    parent = candidate.parent
    if not parent.is_dir():
        raise RuntimeError(f"receipt 디렉터리가 없습니다: {parent}")
    destination = parent / candidate.name
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"receipt는 덮어쓸 수 없습니다: {destination}")
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


@dataclass
class ReceiptReservation:
    """A no-overwrite receipt path with explicit pending/failure recovery state."""

    path: Path
    descriptor: int
    migration_id: str
    started_at: datetime

    def _write(self, payload: dict[str, Any]) -> None:
        encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        os.ftruncate(self.descriptor, 0)
        os.lseek(self.descriptor, 0, os.SEEK_SET)
        offset = 0
        while offset < len(encoded):
            offset += os.write(self.descriptor, encoded[offset:])
        os.fsync(self.descriptor)
        _fsync_directory(self.path.parent)

    def finalize(self, receipt: dict[str, Any]) -> None:
        self._write(receipt)

    def fail(self, error: BaseException) -> None:
        self._write({
            "format": RECEIPT_FORMAT,
            "format_version": RECEIPT_FORMAT_VERSION,
            "migration_id": self.migration_id,
            "started_at": self.started_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "state": "FAILED",
            "failed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "error": str(error),
        })

    def close(self) -> None:
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1


def reserve_receipt(path: Path, *, migration_id: str, started_at: datetime) -> ReceiptReservation:
    """Reserve the final path before DB work so a receipt race cannot lose evidence.

    The initial JSON is an explicit ``PENDING`` recovery record. It is replaced
    in-place after commit, rather than using a replace operation that could
    overwrite an operator's existing receipt.
    """
    destination = _receipt_destination(path)
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise FileExistsError(f"receipt는 덮어쓸 수 없습니다: {destination}") from error
    reservation = ReceiptReservation(destination, descriptor, migration_id, started_at)
    try:
        os.fchmod(descriptor, 0o600)
        reservation._write({
            "format": RECEIPT_FORMAT,
            "format_version": RECEIPT_FORMAT_VERSION,
            "migration_id": migration_id,
            "started_at": started_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "state": "PENDING",
        })
    except BaseException:
        reservation.close()
        raise
    return reservation


def require_initialized_media_schema(connection: Any) -> None:
    """Fail closed instead of creating schema or seed data from this operator CLI."""
    try:
        for table in ("media_assets", "asset_blobs", "asset_blob_chunks", "drop_video_assets"):
            connection.execute(f"SELECT 1 FROM {table} LIMIT 1")
    except Exception as error:
        raise RuntimeError(
            "media migration은 사전 migration/seed가 완료된 DB에서만 실행할 수 있습니다. "
            "initialize_database로 schema나 fixture를 생성하지 않습니다."
        ) from error


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate legacy media files into PostgreSQL/DuckDB chunk storage.")
    parser.add_argument("--execute", action="store_true", help="write blob metadata/chunks; default is dry-run")
    parser.add_argument("--receipt", type=Path, help="operator receipt path; required with --execute")
    parser.add_argument("--no-demo", action="store_true", help="do not include the explicit 20-file demo allowlist")
    args = parser.parse_args()
    if args.execute and args.receipt is None:
        parser.error("--execute에는 --receipt PATH가 필요합니다.")
    if not args.execute and args.receipt is not None:
        parser.error("--receipt는 --execute와 함께만 사용할 수 있습니다.")
    migration_id = str(uuid4()) if args.execute else None
    started_at = datetime.now(timezone.utc) if args.execute else None
    receipt_reservation = (
        reserve_receipt(args.receipt, migration_id=migration_id, started_at=started_at)
        if args.execute
        else None
    )
    try:
        with connect() as connection:
            require_initialized_media_schema(connection)
            plan = plan_targets(connection, include_demo=not args.no_demo)
        print(json.dumps({"mode": "execute" if args.execute else "dry-run", **plan}, ensure_ascii=False, indent=2))
        if args.execute:
            results = execute(plan)
            receipt = build_receipt(
                plan,
                results,
                migration_id=migration_id,
                executed_at=datetime.now(timezone.utc),
            )
            assert receipt_reservation is not None
            receipt_reservation.finalize(receipt)
            print(f"migrated={plan['total']} receipt={receipt_reservation.path}")
    except BaseException as error:
        if receipt_reservation is not None:
            try:
                receipt_reservation.fail(error)
            finally:
                receipt_reservation.close()
        raise
    else:
        if receipt_reservation is not None:
            receipt_reservation.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
