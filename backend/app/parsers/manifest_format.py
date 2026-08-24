"""Shared manifest format detection for canonical and legacy import paths."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class ManifestFormat(str, Enum):
    """Supported manifest contracts."""

    CANONICAL_MAPPINGS = "mappings"
    LEGACY_RESULT_FILES = "result_files"


class ManifestFormatError(ValueError):
    """Raised when a manifest cannot be assigned to exactly one contract."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class LoadedManifest:
    """JSON data together with the format selected by the discriminator."""

    path: Path
    format: ManifestFormat
    data: dict[str, Any]


def resolve_manifest_path(root: Path, relative_path: str) -> Path:
    """Resolve a manifest below ``root`` without following unsafe manifest links."""

    root_path = root.resolve()
    candidate = root_path / relative_path
    if candidate.is_symlink():
        raise ManifestFormatError(
            "MANIFEST_SYMLINK_FORBIDDEN",
            "manifest.json 심볼릭 링크는 허용되지 않습니다.",
        )
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise FileNotFoundError("Manifest not found") from exc
    if resolved == root_path or root_path not in resolved.parents:
        raise ManifestFormatError(
            "MANIFEST_PATH_OUTSIDE_ROOT",
            "manifest.json은 설정된 import root 내부에 있어야 합니다.",
        )
    if not resolved.is_file():
        raise FileNotFoundError("Manifest not found")
    return resolved


def detect_manifest_format(data: Any) -> ManifestFormat:
    """Return one format, rejecting mixed, unknown, or non-object manifests."""

    if not isinstance(data, dict):
        raise ManifestFormatError("MANIFEST_ROOT_INVALID", "manifest.json은 객체여야 합니다.")
    has_mappings = "mappings" in data
    has_result_files = "result_files" in data
    if has_mappings and has_result_files:
        raise ManifestFormatError(
            "MANIFEST_FORMAT_MIXED",
            "mappings와 result_files를 함께 사용할 수 없습니다.",
        )
    if has_mappings:
        return ManifestFormat.CANONICAL_MAPPINGS
    if has_result_files:
        return ManifestFormat.LEGACY_RESULT_FILES
    raise ManifestFormatError(
        "MANIFEST_FORMAT_UNKNOWN",
        "mappings 또는 result_files 형식을 식별할 수 없습니다.",
    )


def load_manifest(path: Path, *, expected_format: ManifestFormat | None = None) -> LoadedManifest:
    """Load JSON and enforce the requested import contract, if supplied."""

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestFormatError("MANIFEST_JSON_INVALID", "manifest.json을 읽을 수 없습니다.") from exc
    manifest_format = detect_manifest_format(data)
    if expected_format is not None and manifest_format is not expected_format:
        raise ManifestFormatError(
            "MANIFEST_FORMAT_UNEXPECTED",
            f"{expected_format.value} 형식이 필요하지만 {manifest_format.value} 형식입니다.",
        )
    return LoadedManifest(path=path, format=manifest_format, data=data)
