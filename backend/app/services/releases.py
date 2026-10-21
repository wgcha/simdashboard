"""Version board data: ``RELEASES.md`` entries and the build info of this process.

* ``RELEASES.md`` (repository root, ``release/<id>/RELEASES.md`` in the offline bundle) is written
  only by deployment work, newest entry first, ``## <version> — YYYY-MM-DD`` plus ``- `` bullets.
  Nothing in the web UI edits it.
* Build info (commit and date) comes from ``build-info.json`` next to ``RELEASES.md`` when the
  offline bundle stamped one (``scripts/windows/build-offline-bundle.ps1``); a source deployment has
  none and asks git for HEAD once.  No file and no git: the build info is simply empty.

Reading never fails the request: a missing or unreadable file gives an empty list.
``docs/features/version-board.md``.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..version import __version__

RELEASES_FILE = "RELEASES.md"
BUILD_INFO_FILE = "build-info.json"
COMMIT_DISPLAY_LENGTH = 12
GIT_TIMEOUT_SECONDS = 5

_HEADING = re.compile(r"^##\s+(?P<version>\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)\s+[—–-]\s+(?P<date>\d{4}-\d{2}-\d{2})\s*$")
_BULLET = re.compile(r"^[-*]\s+(?P<text>\S.*)$")
_COMMIT = re.compile(r"^[0-9a-fA-F]{7,64}$")


@dataclass
class ReleaseEntry:
    version: str
    date: str
    notes: list[str] = field(default_factory=list)


def release_root() -> Path:
    """Directory holding ``RELEASES.md``: the repository root, or ``release/<id>`` in the offline bundle."""
    return Path(__file__).resolve().parents[3]


def version_tuple(version: str) -> tuple[int, int, int]:
    core = re.split(r"[-+]", version, maxsplit=1)[0]
    major, minor, patch = (int(part) for part in core.split("."))
    return major, minor, patch


def parse_releases(text: str, *, strict: bool = False) -> list[ReleaseEntry]:
    """Entries in file order.  ``strict`` raises ``ValueError`` on a malformed ``## `` heading or a
    version repeated twice; otherwise such a heading (and its bullets) is skipped."""
    entries: list[ReleaseEntry] = []
    current: ReleaseEntry | None = None
    seen: set[str] = set()
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        if line.startswith("## "):
            current = None
            match = _HEADING.match(line)
            problem = ""
            if match is None:
                problem = "heading must be '## <version> — YYYY-MM-DD'"
            else:
                try:
                    date.fromisoformat(match["date"])
                except ValueError:
                    problem = f"invalid date {match['date']}"
                if match["version"] in seen:
                    problem = f"version {match['version']} appears twice"
            if problem:
                if strict:
                    raise ValueError(f"RELEASES.md line {number}: {problem}: {line!r}")
                continue
            current = ReleaseEntry(version=match["version"], date=match["date"])
            seen.add(current.version)
            entries.append(current)
            continue
        if current is None:
            continue
        bullet = _BULLET.match(line)
        if bullet:
            current.notes.append(bullet["text"].strip())
        elif line[:1] in (" ", "\t") and line.strip() and current.notes:
            current.notes[-1] = f"{current.notes[-1]} {line.strip()}"
    return entries


def load_releases(root: Path | None = None) -> list[ReleaseEntry]:
    path = (root or release_root()) / RELEASES_FILE
    try:
        return parse_releases(path.read_text(encoding="utf-8-sig"))
    except OSError:
        return []


def _iso_utc(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _short_commit(value: Any) -> str | None:
    if not isinstance(value, str) or not _COMMIT.match(value.strip()):
        return None
    return value.strip().lower()[:COMMIT_DISPLAY_LENGTH]


def _stamped_build_info(root: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((root / BUILD_INFO_FILE).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    commit = _short_commit(data.get("commit"))
    built_at = _iso_utc(data.get("built_at"))
    if commit is None and built_at is None:
        return None
    return {"commit": commit, "built_at": built_at, "source": "bundle"}


def _checkout_build_info(root: Path) -> dict[str, Any] | None:
    """Source deployment (``update.bat`` keeps a Git checkout): HEAD commit and its commit date."""
    if not (root / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "-c", f"safe.directory={root}", "-C", str(root), "log", "-1", "--format=%H%n%cI"],
            capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS, check=False,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    lines = result.stdout.strip().splitlines()
    commit = _short_commit(lines[0]) if lines else None
    if commit is None:
        return None
    # A source deployment is not "built": the date is the commit date of the checked-out HEAD.
    return {"commit": commit, "built_at": _iso_utc(lines[1]) if len(lines) > 1 else None, "source": "git"}


def read_build_info(root: Path | None = None) -> dict[str, Any]:
    base = root or release_root()
    return _stamped_build_info(base) or _checkout_build_info(base) or {"commit": None, "built_at": None, "source": None}


@lru_cache(maxsize=1)
def _process_build_info() -> dict[str, Any]:
    return read_build_info()


def releases_response() -> dict[str, Any]:
    """Payload of ``GET /api/releases``; build info is read once per process (a deployment restarts it)."""
    entries = load_releases()
    released_at = next((entry.date for entry in entries if entry.version == __version__), None)
    return {
        "version": {"current": __version__, "released_at": released_at},
        "build": dict(_process_build_info()),
        "releases": [{"version": entry.version, "date": entry.date, "notes": list(entry.notes)} for entry in entries],
    }
