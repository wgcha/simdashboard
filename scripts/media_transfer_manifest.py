from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.database import connect, initialize_database  # noqa: E402
from app.repositories.media_repository import get_blob, validate_blob_chunks  # noqa: E402


def build_manifest() -> dict[str, object]:
    with connect() as connection:
        blobs = connection.execute("SELECT id, sha256, file_size, chunk_count FROM asset_blobs ORDER BY id").fetchall()
        chunks = int(connection.execute("SELECT count(*) FROM asset_blob_chunks").fetchone()[0])
        references = int(
            connection.execute(
                "SELECT count(*) FROM media_assets WHERE blob_id IS NOT NULL"
            ).fetchone()[0]
        ) + int(connection.execute("SELECT count(*) FROM drop_video_assets WHERE blob_id IS NOT NULL").fetchone()[0])
        digest = hashlib.sha256()
        total_bytes = 0
        total_declared_chunks = 0
        for blob_id, sha256, file_size, chunk_count in blobs:
            blob = get_blob(connection, str(blob_id))
            if blob is None:
                raise RuntimeError(f"blob metadata disappeared during manifest creation: {blob_id}")
            validate_blob_chunks(connection, blob)
            digest.update(f"{blob_id}:{sha256}:{file_size}:{chunk_count}\n".encode("utf-8"))
            total_bytes += int(file_size)
            total_declared_chunks += int(chunk_count)
    return {
        "format": "analysis-canvas-media-manifest",
        "format_version": 2,
        "blob_count": len(blobs),
        "chunk_count": chunks,
        "declared_chunk_count": total_declared_chunks,
        "total_blob_bytes": total_bytes,
        "reference_count": references,
        "catalog_sha256": digest.hexdigest(),
        "content_integrity_verified": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or verify a media blob backup manifest.")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if not args.output and not args.verify:
        parser.error("--output 또는 --verify가 필요합니다.")
    initialize_database()
    current = build_manifest()
    if args.verify:
        expected = json.loads(args.verify.read_text(encoding="utf-8"))
        if expected != current:
            raise RuntimeError(json.dumps({"expected": expected, "actual": current}, ensure_ascii=False))
        print(json.dumps({"status": "verified", **current}, ensure_ascii=False, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"manifest={args.output.resolve()}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        raise SystemExit(1)
