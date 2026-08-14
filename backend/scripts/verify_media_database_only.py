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
    public_root = (Path(__file__).resolve().parents[1] / "public_assets").resolve()
    legacy_root = (Path(__file__).resolve().parents[1] / "assets").resolve()
    if public_root == legacy_root or public_root in legacy_root.parents or legacy_root in public_root.parents:
        raise RuntimeError("public/static and legacy roots overlap")
    if public_root.is_symlink() or legacy_root.is_symlink():
        raise RuntimeError("public/static and legacy roots may not be symlinks")

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
        inconsistent = int(
            connection.execute(
                """
                SELECT count(*) FROM (
                    SELECT b.id
                    FROM asset_blobs b
                    LEFT JOIN asset_blob_chunks c ON c.blob_id=b.id
                    GROUP BY b.id, b.file_size, b.chunk_count
                    HAVING count(c.chunk_index) <> b.chunk_count
                       OR COALESCE(sum(c.content_length), 0) <> b.file_size
                ) invalid
                """
            ).fetchone()[0]
        )
        demo_count = int(connection.execute("SELECT count(*) FROM drop_video_assets WHERE load_case_id='loadcase-drop-bottom-001'").fetchone()[0])
        legacy_paths = [row[0] for row in connection.execute("SELECT file_path FROM media_assets WHERE blob_id IS NULL AND file_path IS NOT NULL").fetchall()]
    for raw in legacy_paths:
        if public_root in _private_path(str(raw)).parents:
            raise RuntimeError(f"legacy media is reachable below public root: {raw}")

    workers = int(os.getenv("UVICORN_WORKERS", "1"))
    connection_budget = postgres_connection_budget(workers, settings.postgres_pool)
    configured_max = os.getenv("POSTGRES_MAX_CONNECTIONS")
    if configured_max and connection_budget >= int(configured_max):
        raise RuntimeError(f"connection budget {connection_budget} exceeds configured max_connections {configured_max}")
    if unbound or missing or inconsistent or demo_count != 20:
        raise RuntimeError(json.dumps({"unbound": unbound, "missing": missing, "inconsistent": inconsistent, "demo_count": demo_count}))
    return {"status": "database_only", "backend": settings.backend, "unbound": unbound, "missing": missing, "inconsistent": inconsistent, "demo_count": demo_count, "connection_budget": connection_budget}


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
