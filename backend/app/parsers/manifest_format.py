"""Shared manifest format detection for canonical and legacy import paths."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import ijson

from .streaming_json import (
    StreamingJsonComplexityLimitError,
    StreamingJsonError,
    StreamingJsonSizeLimitError,
    iter_json_events,
)


class ManifestFormat(str, Enum):
    """Supported manifest contracts."""

    CANONICAL_MAPPINGS = "mappings"
    LEGACY_RESULT_FILES = "result_files"


class ManifestFormatError(ValueError):
    """Raised when a manifest cannot be assigned to exactly one contract."""

    def __init__(self, code: str, message: str, *, data: dict[str, Any] | None = None):
        self.code = code
        self.data = data
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


def load_manifest(
    path: Path,
    *,
    expected_format: ManifestFormat | None = None,
    max_bytes: int | None = None,
    max_mapping_count: int | None = None,
) -> LoadedManifest:
    """Load JSON and enforce the requested import contract, if supplied."""

    if max_bytes is not None or max_mapping_count is not None:
        return _load_manifest_incrementally(
            path,
            expected_format=expected_format,
            max_bytes=max_bytes,
            max_mapping_count=max_mapping_count,
        )

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


def _load_manifest_incrementally(
    path: Path,
    *,
    expected_format: ManifestFormat | None,
    max_bytes: int | None,
    max_mapping_count: int | None,
) -> LoadedManifest:
    """Rebuild a bounded manifest while stopping before an excess mapping.

    The event pass feeds an ``ObjectBuilder`` and checks ``mappings.item``
    before forwarding the cap+1 event to it. Format detection runs after the
    bounded build, preserving the historical errors for ordinary manifests;
    an oversized mixed document is intentionally rejected at the resource
    ceiling before its later discriminator can be materialized.
    """

    if max_bytes is None:
        try:
            # A mapping-only limit still needs a finite byte ceiling.  Using
            # the observed size avoids handing an effectively unbounded read
            # request to the ijson backend and rejects post-stat growth.
            byte_limit = path.stat().st_size
        except OSError as exc:
            raise ManifestFormatError(
                "MANIFEST_JSON_INVALID",
                "manifest.json을 읽을 수 없습니다.",
            ) from exc
    else:
        byte_limit = max_bytes
    try:
        data = _build_streaming_manifest(
            path,
            byte_limit,
            max_mapping_count=max_mapping_count,
            max_events=(
                256 + 64 * max_mapping_count
                if max_mapping_count is not None
                else None
            ),
        )
    except ManifestFormatError:
        raise
    except StreamingJsonSizeLimitError as exc:
        raise ManifestFormatError(
            "MANIFEST_SIZE_LIMIT",
            "manifest.json 크기가 허용 한도를 초과했습니다.",
        ) from exc
    except (StreamingJsonError, OSError, UnicodeDecodeError, ijson.JSONError) as exc:
        raise ManifestFormatError(
            "MANIFEST_JSON_INVALID",
            "manifest.json을 읽을 수 없습니다.",
        ) from exc

    # Keep the same root/format validation semantics as the legacy loader.
    detected = detect_manifest_format(data)
    if expected_format is not None and detected is not expected_format:
        raise ManifestFormatError(
            "MANIFEST_FORMAT_UNEXPECTED",
            f"{expected_format.value} 형식이 필요하지만 {detected.value} 형식입니다.",
        )
    return LoadedManifest(path=path, format=detected, data=data)


def _build_streaming_manifest(
    path: Path,
    max_bytes: int,
    *,
    max_mapping_count: int | None,
    max_events: int | None,
) -> dict[str, Any]:
    """Build a manifest object, checking the mapping cap before each item."""

    builder = ijson.ObjectBuilder()
    mapping_count = 0
    events = iter(
        iter_json_events(
            path,
            max_bytes=max_bytes,
            max_events=max_events,
        )
    )
    try:
        try:
            first_prefix, first_event, first_value = next(events)
        except StopIteration as exc:
            raise ManifestFormatError("MANIFEST_JSON_INVALID", "manifest.json을 읽을 수 없습니다.") from exc
        if first_prefix != "" or first_event != "start_map":
            # Do not build a dense non-object root, but still drain a valid
            # document so malformed/trailing input keeps the JSON error code.
            for _event in events:
                pass
            raise ManifestFormatError("MANIFEST_ROOT_INVALID", "manifest.json은 객체여야 합니다.")
        builder.event(first_event, first_value)
        for prefix, event, value in events:
            # A mapping object emits both ``start_map`` and ``end_map`` at the
            # exact item prefix. Count only the first/root event so one item
            # consumes one unit of the cap.
            if prefix == "mappings.item" and event in {
                "start_map",
                "start_array",
                "string",
                "number",
                "boolean",
                "null",
            }:
                mapping_count += 1
                if max_mapping_count is not None and mapping_count > max_mapping_count:
                    partial = builder.value
                    raise ManifestFormatError(
                        "MANIFEST_MAPPING_COUNT_LIMIT",
                        "manifest mapping 개수가 허용 한도를 초과했습니다.",
                        data=partial if isinstance(partial, dict) else None,
                    )
            builder.event(event, value)
    except StreamingJsonComplexityLimitError as exc:
        partial = builder.value
        raise ManifestFormatError(
            "MANIFEST_COMPLEXITY_LIMIT",
            "manifest.json 구조 복잡도가 허용 한도를 초과했습니다.",
            data=partial if isinstance(partial, dict) else None,
        ) from exc
    finally:
        events.close()

    data = builder.value
    if not isinstance(data, dict):
        raise ManifestFormatError("MANIFEST_ROOT_INVALID", "manifest.json은 객체여야 합니다.")
    return data
