"""Deterministic fingerprints for validated result bundle files.

The fingerprint deliberately contains file metadata as well as content hashes.
Callers are responsible for deciding which files belong to a bundle and for
validating their manifest paths before constructing :class:`FingerprintFile`.
This module performs a second, cheap containment/link check immediately before
reading each file so a path replaced between validation and hashing is not
silently trusted.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class FingerprintFile:
    """A validated bundle file and its bundle-relative path."""

    relative_path: str
    path: Path


def calculate_bundle_fingerprint(root: Path, files: Iterable[FingerprintFile]) -> str:
    """Return a stable SHA-256 fingerprint for a bundle's files.

    Entries are sorted by relative path and serialized as canonical JSON.  The
    bytes of each file are read only after checking that it is a regular,
    non-symlink file contained by ``root``.
    """

    try:
        resolved_root = root.resolve(strict=True)
    except OSError as exc:
        raise ValueError("번들 fingerprint root를 확인할 수 없습니다.") from exc

    entries: list[dict[str, int | str]] = []
    for item in files:
        path = item.path
        if path.is_symlink():
            raise ValueError("번들 fingerprint 대상 파일에 심볼릭 링크는 허용되지 않습니다.")
        try:
            resolved_path = path.resolve(strict=True)
            resolved_path.relative_to(resolved_root)
        except (OSError, ValueError) as exc:
            raise ValueError("번들 fingerprint 대상 파일이 root 밖에 있습니다.") from exc
        if not resolved_path.is_file():
            raise ValueError("번들 fingerprint 대상 파일을 찾을 수 없습니다.")

        entries.append(
            {
                "path": item.relative_path,
                "size": resolved_path.stat().st_size,
                "sha256": _sha256(resolved_path),
            }
        )

    entries.sort(key=lambda entry: str(entry["path"]))
    payload = json.dumps(
        entries,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
