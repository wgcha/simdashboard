"""Non-secret account preservation evidence for database backup manifests."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any


def _digest_rows(rows: Iterable[tuple[Any, ...]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        # JSON framing prevents ambiguous concatenation. Password hashes,
        # display names, email/employee identifiers, and session values are
        # intentionally never selected or written to the manifest.
        digest.update(json.dumps(list(row), separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def account_inventory(connection: Any) -> dict[str, object]:
    """Return counts plus opaque ID/authorization digests from one DB snapshot."""
    users = connection.execute(
        "SELECT id::text, account_status, is_global_admin FROM users ORDER BY id"
    ).fetchall()
    memberships = connection.execute(
        "SELECT user_id::text, project_id::text, role FROM project_memberships "
        "ORDER BY user_id, project_id"
    ).fetchall()
    statuses = {"PENDING": 0, "ACTIVE": 0, "SUSPENDED": 0}
    global_admin_count = 0
    for _user_id, status, is_global_admin in users:
        if status not in statuses:
            raise RuntimeError("users.account_status contains an unsupported value.")
        statuses[status] += 1
        global_admin_count += int(bool(is_global_admin))
    return {
        "format": "analysis-canvas-account-inventory",
        "format_version": 1,
        "users": {
            "count": len(users),
            "pending_count": statuses["PENDING"],
            "active_count": statuses["ACTIVE"],
            "suspended_count": statuses["SUSPENDED"],
            "global_admin_count": global_admin_count,
            "identity_authorization_sha256": _digest_rows(users),
        },
        "project_memberships": {
            "count": len(memberships),
            "authorization_sha256": _digest_rows(memberships),
        },
    }


def require_account_inventory(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or value.get("format") != "analysis-canvas-account-inventory" or value.get("format_version") != 1:
        raise RuntimeError("account_inventory format is invalid.")
    for group, fields in (("users", ("count", "pending_count", "active_count", "suspended_count", "global_admin_count", "identity_authorization_sha256")), ("project_memberships", ("count", "authorization_sha256"))):
        item = value.get(group)
        if not isinstance(item, dict) or any(type(item.get(field)) is not int and field.endswith("count") for field in fields):
            raise RuntimeError("account_inventory counts are invalid.")
        for field in fields:
            current = item.get(field)
            if field.endswith("count") and (type(current) is not int or current < 0):
                raise RuntimeError("account_inventory counts are invalid.")
            if field.endswith("sha256") and (not isinstance(current, str) or len(current) != 64 or any(c not in "0123456789abcdef" for c in current)):
                raise RuntimeError("account_inventory digest is invalid.")
    users = value["users"]
    if users["count"] != users["pending_count"] + users["active_count"] + users["suspended_count"] or users["global_admin_count"] > users["active_count"] + users["pending_count"] + users["suspended_count"]:
        raise RuntimeError("account_inventory counts are inconsistent.")
    return value
