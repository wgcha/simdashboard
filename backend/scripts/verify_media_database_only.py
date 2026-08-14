from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import database_settings  # noqa: E402
from app.database import connect, initialize_database  # noqa: E402
from app.database_connection import postgres_connection_budget  # noqa: E402
from app.repositories.media_repository import get_blob, validate_blob_chunks  # noqa: E402
from app.services.drop_video_demo import DROP_VIDEO_DEMO_SCENES  # noqa: E402


EXPECTED_REVISION = "0009_menu_workflow_order"


def _private_path(raw: str) -> Path:
    root = (Path(__file__).resolve().parents[1] / "assets").resolve()
    parts = Path(raw).parts
    relative = Path(*parts[1:]) if parts and parts[0].lower() == "assets" else Path(raw)
    path = (root / relative).resolve()
    if root not in path.parents:
        raise RuntimeError(f"unsafe legacy media path: {raw}")
    return path


def verify() -> dict[str, object]:
    settings = database_settings()
    raw_public_root = Path(__file__).resolve().parents[1] / "public_assets"
    raw_legacy_root = Path(__file__).resolve().parents[1] / "assets"
    if raw_public_root.is_symlink() or raw_legacy_root.is_symlink():
        raise RuntimeError("public/static and legacy roots may not be symlinks")
    public_root = raw_public_root.resolve()
    legacy_root = raw_legacy_root.resolve()
    if public_root == legacy_root or public_root in legacy_root.parents or legacy_root in public_root.parents:
        raise RuntimeError("public/static and legacy roots overlap")

    with connect() as connection:
        unbound = int(connection.execute("SELECT count(*) FROM media_assets WHERE blob_id IS NULL").fetchone()[0])
        missing = int(
            connection.execute(
                """
                SELECT count(*) FROM media_assets m
                LEFT JOIN asset_blobs b ON b.id=m.blob_id
                WHERE m.blob_id IS NOT NULL AND b.id IS NULL
                """
            ).fetchone()[0]
        )
        missing += int(
            connection.execute(
                """
                SELECT count(*) FROM drop_video_assets d
                LEFT JOIN asset_blobs b ON b.id=d.blob_id
                WHERE b.id IS NULL
                """
            ).fetchone()[0]
        )
        corrupt_blob_ids: list[str] = []
        for (blob_id,) in connection.execute("SELECT id FROM asset_blobs ORDER BY id").fetchall():
            blob = get_blob(connection, str(blob_id))
            try:
                if blob is None:
                    raise ValueError("blob metadata missing")
                validate_blob_chunks(connection, blob)
            except (RuntimeError, ValueError):
                corrupt_blob_ids.append(str(blob_id))
        inconsistent = len(corrupt_blob_ids)
        demo_ids = {
            str(row[0])
            for row in connection.execute(
                "SELECT video_id FROM drop_video_assets WHERE load_case_id='loadcase-drop-bottom-001'"
            ).fetchall()
        }
        expected_demo_ids = {scene.video_id for scene in DROP_VIDEO_DEMO_SCENES}
        demo_count = len(demo_ids)
        legacy_paths = [row[0] for row in connection.execute("SELECT file_path FROM media_assets WHERE blob_id IS NULL AND file_path IS NOT NULL").fetchall()]
        revision = None
        permission_failures: list[str] = []
        if settings.backend == "postgresql":
            revision_row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
            revision = str(revision_row[0]) if revision_row else None
            required_privileges = {
                "asset_blobs": "SELECT,INSERT,UPDATE,DELETE",
                "asset_blob_chunks": "SELECT,INSERT,UPDATE,DELETE",
                "media_assets": "SELECT,INSERT,UPDATE",
                "drop_video_assets": "SELECT,INSERT,UPDATE",
            }
            for table, privileges in required_privileges.items():
                allowed = connection.execute(
                    "SELECT has_table_privilege(current_user, ?, ?)", [table, privileges]
                ).fetchone()[0]
                if not allowed:
                    permission_failures.append(f"{table}:{privileges}")
    for raw in legacy_paths:
        if public_root in _private_path(str(raw)).parents:
            raise RuntimeError(f"legacy media is reachable below public root: {raw}")

    workers = int(os.getenv("UVICORN_WORKERS", "1"))
    connection_budget = postgres_connection_budget(workers, settings.postgres_pool)
    configured_max = os.getenv("POSTGRES_MAX_CONNECTIONS")
    if configured_max and connection_budget >= int(configured_max):
        raise RuntimeError(f"connection budget {connection_budget} exceeds configured max_connections {configured_max}")
    if unbound or missing or inconsistent or demo_ids != expected_demo_ids or permission_failures or (revision and revision != EXPECTED_REVISION):
        raise RuntimeError(json.dumps({
            "unbound": unbound,
            "missing": missing,
            "inconsistent": inconsistent,
            "corrupt_blob_ids": corrupt_blob_ids[:20],
            "demo_count": demo_count,
            "missing_demo_ids": sorted(expected_demo_ids - demo_ids),
            "unexpected_demo_ids": sorted(demo_ids - expected_demo_ids),
            "revision": revision,
            "permission_failures": permission_failures,
        }))
    return {"status": "database_only", "backend": settings.backend, "unbound": unbound, "missing": missing, "inconsistent": inconsistent, "demo_count": demo_count, "connection_budget": connection_budget, "revision": revision}


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the database-only media storage gate.")
    parser.add_argument("--initialize", action="store_true", help="run the normal local initialization before checking")
    args = parser.parse_args()
    if args.initialize:
        initialize_database()
    print(json.dumps(verify(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        raise SystemExit(1)
