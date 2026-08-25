"""Canonical, immutable result-bundle publication contract.

The marker is deliberately small and boring: it is an untrusted declaration
which the importer compares with bytes captured from the same directory file
descriptor.  It never grants trust by itself.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, Mapping


READY_MARKER_NAME = ".simdashboard-ready.json"
STAGING_DIRECTORY_PREFIX = ".simdashboard-staging-"
READY_MARKER_MAX_BYTES = 16 * 1024
READY_MARKER_SCHEMA_ID = "simdashboard-result-bundle-ready"
READY_MARKER_VERSION = 1
READY_MARKER_STATE = "READY"

_MARKER_FIELDS = frozenset(
    {
        "schema_id",
        "version",
        "state",
        "bundle_path",
        "manifest_checksum",
        "bundle_fingerprint",
        "entry_count",
        "published_at",
    }
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PUBLICATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_RFC3339_UTC_SECONDS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class ReadyMarkerError(ValueError):
    """A stable, safe readiness-marker validation failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ReadyMarkerV1:
    schema_id: str
    version: int
    state: str
    bundle_path: str
    manifest_checksum: str
    bundle_fingerprint: str
    entry_count: int
    published_at: str


def _marker_error(suffix: str, message: str) -> ReadyMarkerError:
    return ReadyMarkerError(f"BUNDLE_READY_MARKER_{suffix}", message)


def validate_publication_id(publication_id: str) -> str:
    """Return a single safe canonical publication path segment."""

    if not isinstance(publication_id, str) or not _PUBLICATION_ID.fullmatch(publication_id):
        raise _marker_error("PUBLICATION_ID_INVALID", "publication_id는 안전한 단일 경로 식별자여야 합니다.")
    return publication_id


def manifest_target(raw: Mapping[str, Any]) -> tuple[str, str, str]:
    """Extract the canonical project/request/load-case identifier tuple."""

    context = raw.get("context")
    if not isinstance(context, Mapping):
        raise _marker_error("MANIFEST_TARGET_INVALID", "manifest context에 대상 식별자가 필요합니다.")

    def value(name: str) -> str:
        direct = context.get(name)
        nested = context.get(name.removesuffix("_id"))
        candidate = direct if isinstance(direct, str) else nested.get("id") if isinstance(nested, Mapping) else None
        if not isinstance(candidate, str):
            raise _marker_error("MANIFEST_TARGET_INVALID", "manifest context 대상 식별자가 올바르지 않습니다.")
        candidate = candidate.strip()
        # The physical canonical layout uses one POSIX path segment per ID.
        if not candidate or not _PUBLICATION_ID.fullmatch(candidate):
            raise _marker_error("MANIFEST_TARGET_INVALID", "manifest context 대상 식별자가 안전하지 않습니다.")
        return candidate

    return value("project_id"), value("request_id"), value("load_case_id")


def canonical_bundle_relative(raw_manifest: Mapping[str, Any], publication_id: str) -> str:
    """Build the four-segment canonical bundle path from manifest metadata."""

    return "/".join((*manifest_target(raw_manifest), validate_publication_id(publication_id)))


def _validate_bundle_path(value: object) -> str:
    if not isinstance(value, str) or "\\" in value or "\x00" in value:
        raise _marker_error("PATH_INVALID", "marker bundle_path가 올바르지 않습니다.")
    path = PurePosixPath(value)
    if path.is_absolute() or len(path.parts) != 4 or any(part in {"", ".", ".."} for part in path.parts):
        raise _marker_error("PATH_INVALID", "marker bundle_path는 네 개의 정규화된 상대 경로여야 합니다.")
    if "/".join(path.parts) != value or any(not _PUBLICATION_ID.fullmatch(part) for part in path.parts):
        raise _marker_error("PATH_INVALID", "marker bundle_path가 canonical 형식이 아닙니다.")
    return value


def _validate_timestamp(value: object) -> str:
    if not isinstance(value, str):
        raise _marker_error("FIELD_TYPE_INVALID", "marker published_at 형식이 올바르지 않습니다.")
    # Ready markers are an immutable interchange format.  Accept one
    # canonical RFC3339 spelling only, matching the publisher's seconds-level
    # UTC clock normalization, so equivalent textual timestamps cannot create
    # ambiguous producer output.
    if not _RFC3339_UTC_SECONDS.fullmatch(value):
        raise _marker_error("PUBLISHED_AT_INVALID", "marker published_at는 UTC Z 초 단위 RFC3339 표기여야 합니다.")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise _marker_error("PUBLISHED_AT_INVALID", "marker published_at 형식이 올바르지 않습니다.") from exc
    return value


def build_ready_marker(
    *,
    bundle_path: str,
    manifest_checksum: str,
    bundle_fingerprint: str,
    entry_count: int,
    published_at: str | None = None,
) -> ReadyMarkerV1:
    """Create a validated v1 marker suitable for deterministic serialization."""

    marker = ReadyMarkerV1(
        schema_id=READY_MARKER_SCHEMA_ID,
        version=READY_MARKER_VERSION,
        state=READY_MARKER_STATE,
        bundle_path=bundle_path,
        manifest_checksum=manifest_checksum,
        bundle_fingerprint=bundle_fingerprint,
        entry_count=entry_count,
        published_at=published_at or datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
    )
    _validate_marker(marker)
    return marker


def serialize_ready_marker(marker: ReadyMarkerV1) -> bytes:
    """Serialize only a fully validated marker, with stable JSON bytes."""

    _validate_marker(marker)
    return json.dumps(
        marker.__dict__, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def parse_ready_marker_bytes(payload: bytes) -> ReadyMarkerV1:
    if not isinstance(payload, bytes) or len(payload) > READY_MARKER_MAX_BYTES:
        raise _marker_error("FILE_LIMIT", "readiness marker 크기가 허용 한도를 초과했습니다.")
    try:
        raw = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_marker_object,
            parse_constant=_reject_noncanonical_json_constant,
        )
    except ReadyMarkerError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _marker_error("MALFORMED", "readiness marker를 읽을 수 없습니다.") from exc
    if not isinstance(raw, dict):
        raise _marker_error("MALFORMED", "readiness marker는 JSON 객체여야 합니다.")
    actual = frozenset(raw)
    if unknown := actual - _MARKER_FIELDS:
        raise _marker_error("UNKNOWN_FIELD", "readiness marker에 허용되지 않은 필드가 있습니다.")
    if missing := _MARKER_FIELDS - actual:
        raise _marker_error("MISSING_FIELD", "readiness marker 필드가 누락되었습니다.")
    marker = ReadyMarkerV1(**raw)
    _validate_marker(marker)
    return marker


def _unique_marker_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object without silently accepting duplicate field names."""
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise _marker_error("DUPLICATE_FIELD", "readiness marker에 중복 필드가 있습니다.")
        result[name] = value
    return result


def _reject_noncanonical_json_constant(value: str) -> Any:
    raise _marker_error("MALFORMED", f"readiness marker JSON 상수 {value}는 허용되지 않습니다.")


def _validate_marker(marker: ReadyMarkerV1) -> None:
    if not isinstance(marker.schema_id, str):
        raise _marker_error("FIELD_TYPE_INVALID", "marker schema_id 형식이 올바르지 않습니다.")
    if marker.schema_id != READY_MARKER_SCHEMA_ID:
        raise _marker_error("SCHEMA_INVALID", "지원하지 않는 readiness marker schema입니다.")
    # bool is an int subclass, and must never be accepted for numeric fields.
    if type(marker.version) is not int:
        raise _marker_error("FIELD_TYPE_INVALID", "marker version 형식이 올바르지 않습니다.")
    if marker.version != READY_MARKER_VERSION:
        raise _marker_error("VERSION_INVALID", "지원하지 않는 readiness marker version입니다.")
    if not isinstance(marker.state, str):
        raise _marker_error("FIELD_TYPE_INVALID", "marker state 형식이 올바르지 않습니다.")
    if marker.state != READY_MARKER_STATE:
        raise _marker_error("STATE_INVALID", "marker state가 READY가 아닙니다.")
    _validate_bundle_path(marker.bundle_path)
    for name, value in (("manifest_checksum", marker.manifest_checksum), ("bundle_fingerprint", marker.bundle_fingerprint)):
        if not isinstance(value, str):
            raise _marker_error("FIELD_TYPE_INVALID", f"marker {name} 형식이 올바르지 않습니다.")
        if not _SHA256.fullmatch(value):
            raise _marker_error("HASH_INVALID", f"marker {name} SHA-256 형식이 올바르지 않습니다.")
    if type(marker.entry_count) is not int:
        raise _marker_error("FIELD_TYPE_INVALID", "marker entry_count 형식이 올바르지 않습니다.")
    if marker.entry_count <= 0:
        raise _marker_error("ENTRY_COUNT_INVALID", "marker entry_count는 양수여야 합니다.")
    _validate_timestamp(marker.published_at)


def verify_ready_marker(
    marker: ReadyMarkerV1,
    *,
    bundle_path: str,
    manifest_checksum: str,
    bundle_fingerprint: str,
    entry_count: int,
) -> None:
    """Fail closed unless marker declarations exactly match captured bytes."""

    _validate_marker(marker)
    if marker.bundle_path != bundle_path:
        raise _marker_error("PATH_MISMATCH", "marker bundle_path가 발견된 bundle 경로와 다릅니다.")
    if marker.manifest_checksum != manifest_checksum:
        raise _marker_error("MANIFEST_CHECKSUM_MISMATCH", "marker manifest checksum이 captured manifest와 다릅니다.")
    if marker.bundle_fingerprint != bundle_fingerprint:
        raise _marker_error("BUNDLE_FINGERPRINT_MISMATCH", "marker bundle fingerprint가 captured bundle과 다릅니다.")
    if marker.entry_count != entry_count:
        raise _marker_error("ENTRY_COUNT_MISMATCH", "marker entry_count가 captured bundle과 다릅니다.")
