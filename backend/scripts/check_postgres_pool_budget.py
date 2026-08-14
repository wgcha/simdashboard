#!/usr/bin/env python3
"""Validate the configured PostgreSQL application pool fits the server budget."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import database_settings
from app.database_connection import postgres_connection_budget


def check_budget(workers: int, max_connections: int, reserved_connections: int) -> int:
    if max_connections < 1:
        raise ValueError("max_connections must be at least one")
    if reserved_connections < 0 or reserved_connections >= max_connections:
        raise ValueError("reserved connections must be >= 0 and smaller than max_connections")
    settings = database_settings()
    if getattr(settings, "backend", "postgresql") != "postgresql":
        raise RuntimeError("ANALYSIS_DB_BACKEND=postgresql이 필요합니다.")
    budget = postgres_connection_budget(workers, settings.postgres_pool)
    usable = max_connections - reserved_connections
    if budget > usable:
        raise RuntimeError(
            f"PostgreSQL pool budget {budget} exceeds usable connections {usable} "
            f"(max_connections={max_connections}, reserved={reserved_connections})."
        )
    return budget


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=int(os.getenv("UVICORN_WORKERS", "1")))
    parser.add_argument("--max-connections", type=int, default=int(os.getenv("POSTGRES_MAX_CONNECTIONS", "0")))
    parser.add_argument("--reserved-connections", type=int, default=int(os.getenv("POSTGRES_RESERVED_CONNECTIONS", "10")))
    arguments = parser.parse_args(argv)
    if arguments.max_connections == 0:
        raise RuntimeError("POSTGRES_MAX_CONNECTIONS 또는 --max-connections가 필요합니다.")
    budget = check_budget(arguments.workers, arguments.max_connections, arguments.reserved_connections)
    print(
        "POSTGRES_POOL_BUDGET_OK "
        f"workers={arguments.workers} budget={budget} max_connections={arguments.max_connections} "
        f"reserved={arguments.reserved_connections}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        raise SystemExit(1)
