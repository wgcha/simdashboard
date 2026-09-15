"""Read the effective source-deployment database selection without secrets."""
from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    # Match the deployment application's precedence: backend/.env supplies
    # compatibility defaults, root .env overrides it, then process values win.
    values = {k: v for k, v in dotenv_values(ROOT / "backend" / ".env").items() if v is not None}
    values.update({k: v for k, v in dotenv_values(ROOT / ".env").items() if v is not None})
    values.update({k: v for k, v in os.environ.items() if v is not None})
    backend = str(values.get("ANALYSIS_DB_BACKEND", "postgresql")).strip().lower()
    print(json.dumps({"backend": backend, "has_database_url": bool(str(values.get("DATABASE_URL", "")).strip())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
