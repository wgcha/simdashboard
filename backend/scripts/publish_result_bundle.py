#!/usr/bin/env python3
"""Validate and atomically publish a canonical result bundle."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.result_bundle_publisher import (  # noqa: E402
    ResultBundlePublishError,
    publish_result_bundle,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-bundle", required=True, type=Path)
    parser.add_argument("--import-root", required=True, type=Path)
    parser.add_argument("--publication-id", required=True)
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate source and target contract without mutating --import-root (uses a private snapshot)",
    )
    arguments = parser.parse_args(argv)
    try:
        result = publish_result_bundle(
            arguments.source_bundle,
            arguments.import_root,
            arguments.publication_id,
            check_only=arguments.check,
        )
    except ResultBundlePublishError as error:
        print(json.dumps({"error": {"code": error.code, "message": str(error)}}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (OSError, RuntimeError, ValueError):
        # Do not expose an OS path or exception text in the CLI boundary.
        print(
            json.dumps(
                {"error": {"code": "RESULT_BUNDLE_PUBLISH_FAILED", "message": "결과 bundle publication을 완료할 수 없습니다."}},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
