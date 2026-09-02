from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.database import connect  # noqa: E402
from app.services.media_integrity import media_inventory, require_media_integrity  # noqa: E402


def build_manifest() -> dict[str, object]:
    """Return the shared, deterministic inventory after strict validation."""
    with connect() as connection:
        report = media_inventory(connection)
    require_media_integrity(report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or verify a media blob backup manifest.")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if not args.output and not args.verify:
        parser.error("--output 또는 --verify가 필요합니다.")
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
