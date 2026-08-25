"""Deterministic fingerprints for already-captured result bundle entries.

Filesystem traversal and byte capture belong to :mod:`bundle_snapshot`. This
module deliberately accepts immutable metadata only, so calculating a
fingerprint can never reopen a live producer file after it was validated.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class FingerprintEntry:
    """One captured bundle file represented without a live filesystem path."""

    relative_path: str
    size: int
    sha256: str

    def __post_init__(self) -> None:
        if not self.relative_path or self.relative_path.startswith("/"):
            raise ValueError("bundle fingerprint path는 비어 있지 않은 상대 경로여야 합니다.")
        if self.size < 0:
            raise ValueError("bundle fingerprint size는 음수일 수 없습니다.")
        if len(self.sha256) != 64 or any(character not in "0123456789abcdef" for character in self.sha256):
            raise ValueError("bundle fingerprint sha256 형식이 올바르지 않습니다.")


def calculate_bundle_fingerprint(entries: Iterable[FingerprintEntry]) -> str:
    """Return a stable SHA-256 fingerprint of immutable captured entries.

    The canonical JSON shape matches the prior fingerprint representation;
    only file reopening and path checks were removed. Snapshot capture owns
    those security-sensitive responsibilities before it creates each entry.
    """

    payload_entries = [
        {"path": entry.relative_path, "size": entry.size, "sha256": entry.sha256}
        for entry in entries
    ]
    payload_entries.sort(key=lambda entry: str(entry["path"]))
    payload = json.dumps(
        payload_entries,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
