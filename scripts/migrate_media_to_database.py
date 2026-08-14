from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.database import connect, initialize_database  # noqa: E402
from app.services.drop_video_demo import DROP_VIDEO_DEMO_SCENES, DROP_VIDEO_SOURCE_DIR, probe_mp4  # noqa: E402
from app.services.media_storage_service import attach_stored_media, inspect_file, store_file  # noqa: E402


ASSET_ROOT = (ROOT / "backend" / "assets").resolve()


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
        "SELECT id, file_path, asset_type, mime_type, original_filename, blob_id FROM media_assets WHERE blob_id IS NULL"
    ).fetchall():
        asset_id, file_path, asset_type, mime_type, original_filename, _ = row
        source = asset_source(str(file_path))
        if not source.is_file():
            raise RuntimeError(f"legacy media file is missing: {source}")
        filename = str(original_filename or source.name)
        inspection = inspect_file(source, filename=filename, mime_type=str(mime_type), asset_type=str(asset_type))
        media.append({"asset_id": str(asset_id), "source": str(source), "filename": filename, "asset_type": str(asset_type), "mime_type": str(mime_type), "file_size": inspection.file_size, "sha256": inspection.sha256})

    demo = []
    if include_demo:
        if len(DROP_VIDEO_DEMO_SCENES) != 20:
            raise RuntimeError(f"demo allowlist must contain exactly 20 files, found {len(DROP_VIDEO_DEMO_SCENES)}")
        for scene in DROP_VIDEO_DEMO_SCENES:
            candidate = DROP_VIDEO_SOURCE_DIR / scene.filename
            _reject_symlink_path(candidate, DROP_VIDEO_SOURCE_DIR)
            source = candidate.resolve()
            if source.parent != DROP_VIDEO_SOURCE_DIR.resolve() or not source.is_file():
                raise RuntimeError(f"demo media file is missing: {source}")
            inspection = inspect_file(source, filename=scene.filename, mime_type="video/mp4", asset_type="VIDEO")
            media_probe = probe_mp4(source)
            exists = connection.execute("SELECT 1 FROM drop_video_assets WHERE video_id=?", [scene.video_id]).fetchone()
            if not exists:
                demo.append({"video_id": scene.video_id, "load_case_id": "loadcase-drop-bottom-001", "scene_name": scene.scene_name, "sort_order": scene.sort_order, "source": str(source), "filename": scene.filename, "mime_type": "video/mp4", "asset_type": "VIDEO", "file_size": inspection.file_size, "sha256": inspection.sha256, "codec": media_probe.codec, "fast_start": media_probe.fast_start})
    return {"media": media, "demo": demo, "total": len(media) + len(demo)}


def execute(plan: dict[str, Any]) -> None:
    with connect() as connection:
        for item in plan["media"]:
            inspected = inspect_file(Path(item["source"]), filename=item["filename"], mime_type=item["mime_type"], asset_type=item["asset_type"])
            if (inspected.file_size, inspected.sha256) != (item["file_size"], item["sha256"]):
                raise RuntimeError(f"media changed after preflight: {item['source']}")
            stored = store_file(connection, Path(item["source"]), filename=item["filename"], mime_type=item["mime_type"], asset_type=item["asset_type"])
            if (stored.blob.file_size, stored.blob.sha256) != (item["file_size"], item["sha256"]):
                raise RuntimeError(f"media changed while being migrated: {item['source']}")
            attach_stored_media(connection, item["asset_id"], stored)
        for item in plan["demo"]:
            inspected = inspect_file(Path(item["source"]), filename=item["filename"], mime_type=item["mime_type"], asset_type=item["asset_type"])
            if (inspected.file_size, inspected.sha256) != (item["file_size"], item["sha256"]):
                raise RuntimeError(f"demo media changed after preflight: {item['source']}")
            stored = store_file(connection, Path(item["source"]), filename=item["filename"], mime_type=item["mime_type"], asset_type=item["asset_type"])
            if (stored.blob.file_size, stored.blob.sha256) != (item["file_size"], item["sha256"]):
                raise RuntimeError(f"demo media changed while being migrated: {item['source']}")
            connection.execute("UPDATE asset_blobs SET orphaned_at=NULL WHERE id=?", [stored.blob.id])
            connection.execute(
                """
                INSERT INTO drop_video_assets
                    (video_id, load_case_id, blob_id, original_filename, mime_type, scene_name, sort_order, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (video_id) DO NOTHING
                """,
                [item["video_id"], item["load_case_id"], stored.blob.id, item["filename"], item["mime_type"], item["scene_name"], item["sort_order"], json.dumps({"demo": True, "codec": item["codec"], "fast_start": item["fast_start"]})],
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate legacy media files into PostgreSQL/DuckDB chunk storage.")
    parser.add_argument("--execute", action="store_true", help="write blob metadata/chunks; default is dry-run")
    parser.add_argument("--no-demo", action="store_true", help="do not include the explicit 20-file demo allowlist")
    args = parser.parse_args()
    initialize_database()
    with connect() as connection:
        plan = plan_targets(connection, include_demo=not args.no_demo)
    print(json.dumps({"mode": "execute" if args.execute else "dry-run", **plan}, ensure_ascii=False, indent=2))
    if args.execute and plan["total"]:
        execute(plan)
        print(f"migrated={plan['total']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
