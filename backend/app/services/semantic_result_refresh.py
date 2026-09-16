"""Small application boundary for load-case scoped semantic refreshes.

The HTTP router supplies the actual file reader/import command; this service
only discovers exact, persisted binding identities so callers never turn a
load-case request into a broad folder scan.
"""
from __future__ import annotations

from typing import Any

from ..repositories import semantic_mapping


def load_case_binding_snapshots(conn: Any, load_case_id: str) -> list[dict[str, Any]]:
    """Capture direct result binding identities in one read transaction."""
    entries = []
    for binding in semantic_mapping.semantic_bindings(conn):
        if str(binding.get("load_case_id") or "") == load_case_id and binding.get("role") == "RESULTS":
            entries.append(binding)
    return sorted(entries, key=lambda binding: (str(binding["relative_path"]).casefold(), str(binding["id"])))


def load_case_binding_ids(conn: Any, load_case_id: str) -> list[str]:
    """Legacy convenience wrapper for callers that need identifiers only."""
    return [str(binding["id"]) for binding in load_case_binding_snapshots(conn, load_case_id)]
