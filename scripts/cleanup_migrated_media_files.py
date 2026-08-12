from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.database import connect, initialize_database  # noqa: E402


ASSET_ROOT = (ROOT / "backend" / "assets").resolve()


def safe_path(raw: str) -> Path:
    parts = Path(raw).parts
    relative = Path(*parts[1:]) if parts and parts[0].lower() == "assets" else Path(raw)
    path = (ASSET_ROOT / relative).resolve()
    if ASSET_ROOT not in path.parents:
        raise RuntimeError(f"legacy path escaped assets root: {raw}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Explicitly clean legacy files after media migration.")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm", action="store_true", help="required with --execute")
    parser.add_argument("--min-age-days", type=int, default=7, choices=range(7, 3661))
    args = parser.parse_args()
    if args.execute and not args.confirm:
        raise RuntimeError("파일 삭제에는 --execute --confirm 두 옵션이 모두 필요합니다.")
    initialize_database()
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=args.min_age_days)
    candidates: list[tuple[str, Path]] = []
    with connect() as connection:
        for asset_id, file_path, created_at in connection.execute(
            """
            SELECT id, file_path, b.created_at
            FROM media_assets m JOIN asset_blobs b ON b.id=m.blob_id
            WHERE m.file_path IS NOT NULL AND b.created_at < ?
            """,
            [cutoff],
        ).fetchall():
            path = safe_path(str(file_path))
            if path.is_file():
                candidates.append((str(asset_id), path))
    print(f"candidates={len(candidates)} cutoff={cutoff.isoformat()}")
    if not args.execute:
        for asset_id, path in candidates:
            print(f"would-remove asset={asset_id} path={path}")
        return 0
    for asset_id, path in candidates:
        path.unlink()
        print(f"removed asset={asset_id} path={path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        raise SystemExit(1)
