from __future__ import annotations

import hashlib
import re
import unicodedata
from uuid import uuid4


_NON_CODE = re.compile(r"[^a-z0-9]+")
_DUPLICATE_SEPARATORS = re.compile(r"[-_]{2,}")


def slugify(value: str | None, *, fallback: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    compact = _NON_CODE.sub("-", normalized).strip("-_")
    compact = _DUPLICATE_SEPARATORS.sub("-", compact)
    if not compact:
        # Preserve a deterministic hint for names written entirely outside
        # the ASCII code alphabet (for example Korean labels), while the
        # uniqueness check below still handles concurrent collisions.
        digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:8]
        compact = f"{fallback}-{digest}"
    if not compact[0].isalpha():
        compact = f"{fallback}-{compact}"
    return compact[:64].strip("-_") or fallback


def unique_identifier(conn, table: str, column: str, base: str, *, reserved: set[str] | None = None) -> str:
    reserved = reserved or set()
    candidate = base
    if candidate not in reserved and not conn.execute(f"SELECT 1 FROM {table} WHERE {column}=? LIMIT 1", [candidate]).fetchone():
        return candidate
    for _ in range(8):
        candidate = f"{base}-{uuid4().hex[:6]}"
        if candidate not in reserved and not conn.execute(f"SELECT 1 FROM {table} WHERE {column}=? LIMIT 1", [candidate]).fetchone():
            return candidate
    return f"{base}-{uuid4().hex}"
