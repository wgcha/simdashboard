"""Bounded, secret-safe diagnostics for media backup child failures.

Only facts from the canonical media inventory error payload are returned.  In
particular, identifiers, paths, and arbitrary exception text never cross this
boundary.
"""

from __future__ import annotations

import json
import re
from typing import Any


_MAX = 2**63 - 1
_MAX_INPUT = 1024 * 1024
_COUNT_FIELDS = (
    "unbound_media_asset_count",
    "missing_reference_count",
    "orphan_blob_count",
    "orphan_chunk_count",
    "corrupt_blob_count",
    "chunk_count",
    "declared_chunk_count",
    "demo_count",
    "demo_expected_count",
)
_BOOL_FIELDS = ("demo_exact", "content_integrity_verified")
_MEDIA_CODES = frozenset({"MEDIA_INTEGRITY_FAILED", "MEDIA_INVENTORY_SCHEMA_INVALID"})
_DEPLOYMENT_REASONS = frozenset(
    {
        "MEDIA_INVENTORY_SCHEMA_INVALID",
        "MEDIA_BLOB_CORRUPT",
        "MEDIA_DATABASE_REFERENCES_INVALID",
        "UNBOUND_MEDIA_FILE_MISSING",
        "UNBOUND_MEDIA_PATH_INVALID",
        "UNBOUND_MEDIA_QUERY_FAILED",
        "UNBOUND_MEDIA_REFERENCE_MISMATCH",
        "ARCHIVE_EXISTS",
        "ARCHIVE_DIRECTORY_INVALID",
        "ARCHIVE_CREATE_FAILED",
        "ARCHIVE_VERIFY_FAILED",
        "ARCHIVE_PUBLISH_FAILED",
        "ASSETS_CHANGED",
        "ASSETS_ENTRY_INVALID",
        "ASSETS_REPARSE_POINT",
        "ASSETS_ROOT_INVALID",
        "ASSETS_UNREADABLE",
    }
)
_DEPLOYMENT_LINE = re.compile(
    r"^\s*(?:(?:scripts\.deployment_media_backup|__main__)\.)?"
    r"DeploymentMediaBackupError\s*:\s*([A-Z][A-Z0-9_]*)\s*$"
)


def _valid_count(value: object) -> bool:
    return type(value) is int and 0 <= value <= _MAX


def _from_inventory(payload: dict[str, Any]) -> dict[str, int | bool | str]:
    result: dict[str, int | bool | str] = {"code": payload["code"]}
    inventory = payload.get("media_inventory")
    if not isinstance(inventory, dict):
        return result
    for name in _COUNT_FIELDS:
        value = inventory.get(name)
        if _valid_count(value):
            result[name] = value
    for name in _BOOL_FIELDS:
        value = inventory.get(name)
        if type(value) is bool:
            result[name] = value
    for name, output_name in (("missing_demo_ids", "missing_demo_count"), ("unexpected_demo_ids", "unexpected_demo_count")):
        values = inventory.get(name)
        if isinstance(values, list) and len(values) <= _MAX:
            result[output_name] = len(values)
    return result


def safe_media_diagnostics(text: str) -> dict[str, int | bool | str]:
    """Extract allowlisted media diagnostics from canonical child output.

    Malformed JSON and unknown error codes produce an empty result.  Parsing is
    deliberately line-oriented so arbitrary traceback text cannot manufacture
    diagnostics through a broad substring match.
    """
    if not isinstance(text, str):
        return {}
    # Child output is bounded before any line/JSON processing.  This also
    # prevents attacker-controlled traceback volume from making scanning
    # quadratic or exhausting parser recursion.
    text = text[:_MAX_INPUT]
    decoder = json.JSONDecoder()
    diagnostics: dict[str, int | bool | str] = {}
    for line in text.splitlines():
        # Canonical MediaIntegrityError text is ``...: {JSON}``; decode only
        # complete JSON objects and then require a known code.
        offset = line.find("{")
        if offset >= 0:
            try:
                payload, end = decoder.raw_decode(line[offset:])
            except (json.JSONDecodeError, ValueError, RecursionError):
                payload = None
                end = 0
            if not line[offset + end :].strip() and isinstance(payload, dict):
                code = payload.get("code")
                if isinstance(code, str) and code in _MEDIA_CODES:
                    diagnostics.update(_from_inventory(payload))
        match = _DEPLOYMENT_LINE.match(line)
        if match and match.group(1) in _DEPLOYMENT_REASONS:
            diagnostics["reason"] = match.group(1)
    return diagnostics
