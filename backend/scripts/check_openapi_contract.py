#!/usr/bin/env python3
"""Fail when the checked-in OpenAPI snapshot differs from the running API."""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app


def canonical_openapi_bytes(schema: dict[object, object]) -> bytes:
    return (json.dumps(schema, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def check_contract(expected_path: Path, schema: dict[object, object]) -> list[str]:
    try:
        expected = expected_path.read_bytes()
    except OSError as error:
        return [f"cannot read OpenAPI snapshot {expected_path}: {error}"]
    actual = canonical_openapi_bytes(schema)
    if expected == actual:
        return []
    expected_text = expected.decode("utf-8", errors="replace").splitlines()
    actual_text = actual.decode("utf-8", errors="replace").splitlines()
    diff = list(
        difflib.unified_diff(
            expected_text,
            actual_text,
            fromfile=str(expected_path),
            tofile="FastAPI app.openapi()",
            lineterm="",
            n=2,
        )
    )
    return ["OpenAPI snapshot is stale; run `cd frontend && pnpm run generate:api`.", *diff[:120]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--expected",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "frontend" / "openapi.json",
        help="checked-in OpenAPI JSON snapshot",
    )
    arguments = parser.parse_args(argv)
    violations = check_contract(arguments.expected, app.openapi())
    if violations:
        print("OPENAPI_CONTRACT_CHECK_FAILED", file=sys.stderr)
        print("\n".join(violations), file=sys.stderr)
        return 1
    print("OPENAPI_CONTRACT_CHECK_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
