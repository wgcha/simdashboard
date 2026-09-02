from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.database import connect, initialize_database  # noqa: E402
from app.repositories.media_repository import delete_orphaned, mark_orphaned  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Two-sweep orphan media blob garbage collection.")
    parser.add_argument("--execute", action="store_true", help="mark and delete eligible blobs")
    parser.add_argument("--grace-hours", type=int, default=24 * 7, choices=range(1, 24 * 366))
    args = parser.parse_args()
    initialize_database()
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=args.grace_hours)
    with connect() as connection:
        if not args.execute:
            candidates = connection.execute(
                """
                SELECT count(*) FROM asset_blobs
                WHERE orphaned_at IS NOT NULL AND orphaned_at < ?
                  AND NOT EXISTS (SELECT 1 FROM media_assets WHERE media_assets.blob_id=asset_blobs.id)
                  AND NOT EXISTS (SELECT 1 FROM drop_video_assets WHERE drop_video_assets.blob_id=asset_blobs.id)
                """,
                [cutoff],
            ).fetchone()[0]
            print(f"dry-run eligible_blobs={candidates} cutoff={cutoff.isoformat()}")
            return 0
        marked = mark_orphaned(connection)
        deleted = delete_orphaned(connection, older_than=cutoff)
    print(f"marked={marked} deleted={deleted} cutoff={cutoff.isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
