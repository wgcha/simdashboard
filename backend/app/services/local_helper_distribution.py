"""Read and verify the Windows local-helper release placed beside the server.

The application can run on Linux.  It never builds or executes the Windows
binary; a CI/release Windows job produces the ZIP and this service only serves
an already verified, secret-free archive.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


ARTIFACT_NAME = "SimulationWorkbenchLocalHelper.zip"
MANIFEST_NAME = "distribution-manifest.json"
DOWNLOAD_URL = "/api/local-helper/distribution/download"
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9._-]+\.zip$")
_verification_lock = Lock()
_verified_hashes: OrderedDict[tuple[object, ...], bool] = OrderedDict()


def _fingerprint(path: Path) -> tuple[int, int, int, int]:
    info = path.stat()
    return info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino


def _verify_archive(path: Path, size: int, sha256: str) -> bool:
    """Hash each immutable release once; invalidate on replacement or modification.

    Release publishers must use new versioned filenames and atomically replace
    the small manifest after the archive is complete. A changed file is never
    accepted from a previous cache entry. Failed hashes are also bounded/cached.
    """
    with _verification_lock:
        fingerprint = _fingerprint(path)
        if fingerprint[0] != size:
            return False
        key = (str(path.resolve()), fingerprint, size, sha256)
        if key in _verified_hashes:
            _verified_hashes.move_to_end(key)
            return _verified_hashes[key]
        valid = _sha256(path) == sha256 and _fingerprint(path) == fingerprint
        _verified_hashes[key] = valid
        while len(_verified_hashes) > 8:
            _verified_hashes.popitem(last=False)
        return valid


@dataclass(frozen=True)
class DistributionArtifact:
    version: str
    filename: str
    sha256: str
    size_bytes: int
    released_at: datetime
    path: Path

    def response(self) -> dict[str, Any]:
        return {
            "status": "ready",
            "version": self.version,
            "filename": self.filename,
            "artifact_url": DOWNLOAD_URL,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "released_at": self.released_at,
        }


def distribution_directory() -> Path:
    """Return the configured or runtime import directory.

    This remains the writable target used by the release importer.  Serving a
    checked-in fallback is deliberately handled in ``load_distribution`` so an
    import never replaces the bundled archive.
    """
    configured = os.environ.get("LOCAL_HELPER_DISTRIBUTION_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return runtime_distribution_directory()


def runtime_distribution_directory() -> Path:
    return Path(__file__).resolve().parents[3] / "dist" / "local-helper" / "windows-x64"


def bundled_distribution_directory() -> Path:
    return Path(__file__).resolve().parents[3] / "deploy" / "windows" / "local-helper" / "windows-x64"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _unavailable(message: str) -> tuple[None, str]:
    return None, message


def load_distribution(directory: Path | None = None) -> tuple[DistributionArtifact | None, str | None]:
    """Return only a fully verified artifact; incomplete releases stay hidden."""
    if directory is not None:
        return _load_distribution(directory)

    # An explicit configuration is authoritative.  In particular, an invalid
    # configured release must not be masked by the checked-in fallback.
    if os.environ.get("LOCAL_HELPER_DISTRIBUTION_DIR", "").strip():
        return _load_distribution(distribution_directory())

    runtime_root = runtime_distribution_directory()
    # A runtime manifest means a publisher has begun (or completed) a release.
    # Validate it as-is; only a missing manifest permits the bundled fallback.
    runtime_manifest = runtime_root / MANIFEST_NAME
    if runtime_manifest.exists() or runtime_manifest.is_symlink():
        return _load_distribution(runtime_root)

    bundled_root = bundled_distribution_directory()
    if (bundled_root / MANIFEST_NAME).is_file():
        return _load_distribution(bundled_root)
    return _load_distribution(runtime_root)


def _load_distribution(root: Path) -> tuple[DistributionArtifact | None, str | None]:
    """Validate exactly one release directory without selecting a fallback."""
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        return _unavailable("Windows 로컬 도우미 배포본이 아직 준비되지 않았습니다.")
    try:
        if manifest_path.stat().st_size > 65_536:
            return _unavailable("Windows 로컬 도우미 배포 manifest가 너무 큽니다.")
        # Older Windows PowerShell writes UTF-8 JSON with a BOM by default.
        raw = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        if not isinstance(raw, dict) or raw.get("format") != 1:
            return _unavailable("Windows 로컬 도우미 배포 manifest 형식이 올바르지 않습니다.")
        version = str(raw["version"]).strip()
        filename = str(raw["filename"])
        expected_hash = str(raw["sha256"]).lower()
        expected_size = int(raw["size_bytes"])
        released_at = datetime.fromisoformat(str(raw["released_at"]).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
        return _unavailable("Windows 로컬 도우미 배포 manifest를 읽을 수 없습니다.")
    if not version or not _SAFE_FILENAME.fullmatch(filename) or not _SHA256.fullmatch(expected_hash) or expected_size <= 0:
        return _unavailable("Windows 로컬 도우미 배포 manifest 값이 올바르지 않습니다.")
    archive = root / filename
    if not archive.is_file() or archive.name != filename:
        return _unavailable("Windows 로컬 도우미 배포 파일이 없습니다.")
    try:
        if not archive.resolve().is_relative_to(root.resolve()) or not _verify_archive(archive, expected_size, expected_hash):
            return _unavailable("Windows 로컬 도우미 배포 파일 검증에 실패했습니다.")
    except OSError:
        return _unavailable("Windows 로컬 도우미 배포 파일을 읽을 수 없습니다.")
    return DistributionArtifact(version, filename, expected_hash, expected_size, released_at, archive), None
