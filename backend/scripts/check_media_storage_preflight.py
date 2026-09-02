"""Run the database-only media gate only when the cutover requires it."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import media_storage_mode  # noqa: E402


def _run_database_only_verifier() -> dict[str, object]:
    # Keep the verifier import lazy: dual-read must be an explicit no-database
    # skip, including when this script is used as systemd ExecStartPre.
    from scripts.verify_media_database_only import verify

    return verify()


def main() -> int:
    mode = media_storage_mode()
    if mode == "dual-read":
        print(json.dumps({
            "status": "skipped",
            "mode": mode,
            "database_access": False,
            "reason": "database-only media verifier is required only for database-only mode",
        }, ensure_ascii=False))
        return 0
    if mode != "database-only":
        raise RuntimeError("SIMDASH_MEDIA_STORAGE_MODE_INVALID")
    report = _run_database_only_verifier()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
