"""Read-only, backend-neutral media inventory and integrity verification.

The report is deliberately composed only of deterministic database facts.  It
is used by the operational verifier and backup/restore tooling so a backup
cannot claim a different definition of a healthy media catalogue than the
runtime gate.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from .drop_video_demo import DROP_VIDEO_DEMO_SCENES
from ..media_policy import ALLOWED_MEDIA


MAX_CHUNK_SIZE = 1024 * 1024
MAX_MEDIA_BYTES = max(limit for _extensions, limit in ALLOWED_MEDIA.values())
MAX_MEDIA_CHUNKS = (MAX_MEDIA_BYTES + MAX_CHUNK_SIZE - 1) // MAX_CHUNK_SIZE
MEDIA_INVENTORY_FORMAT = "analysis-canvas-media-inventory"
MEDIA_INVENTORY_FORMAT_VERSION = 1
_DEMO_LOAD_CASE_ID = "loadcase-drop-bottom-001"


class MediaIntegrityError(RuntimeError):
    """Raised when a media inventory is not safe to publish or restore."""


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_COUNT_FIELDS = (
    "blob_count",
    "chunk_count",
    "declared_chunk_count",
    "total_blob_bytes",
    "media_reference_count",
    "drop_video_reference_count",
    "reference_count",
    "unbound_media_asset_count",
    "missing_media_blob_count",
    "missing_drop_video_blob_count",
    "missing_reference_count",
    "orphan_blob_count",
    "orphan_chunk_count",
    "corrupt_blob_count",
    "demo_expected_count",
    "demo_count",
)
_REQUIRED_FIELDS = {
    "format",
    "format_version",
    *_COUNT_FIELDS,
    "corrupt_blob_ids",
    "content_integrity_verified",
    "demo_contract_active",
    "missing_demo_ids",
    "unexpected_demo_ids",
    "demo_exact",
    "catalog_sha256",
}


def _count(connection: Any, statement: str) -> int:
    row = connection.execute(statement).fetchone()
    return int(row[0]) if row else 0


def _execute_parameters(connection: Any, statement: str, parameters: list[object]):
    """Use the application adapter's qmark form or raw psycopg's pyformat."""
    if getattr(connection, "backend", None) in {"duckdb", "postgresql"}:
        return connection.execute(statement, parameters)
    return connection.execute(statement.replace("?", "%s"), parameters)


def _count_parameters(connection: Any, statement: str, parameters: list[object]) -> int:
    row = _execute_parameters(connection, statement, parameters).fetchone()
    return int(row[0]) if row else 0


@dataclass
class _BlobValidator:
    """Incrementally validate exactly one blob; never retain its chunks."""

    blob_id: str
    blob_sha256: str
    file_size: int
    chunk_size: int
    chunk_count: int
    digest: Any
    actual_chunks: int = 0
    expected_index: int = 0
    total: int = 0
    invalid: bool = False

    @classmethod
    def from_row(cls, row: Any) -> "_BlobValidator":
        return cls(
            blob_id=str(row[0]),
            blob_sha256=str(row[1]),
            file_size=int(row[2]),
            chunk_size=int(row[3]),
            chunk_count=int(row[4]),
            digest=hashlib.sha256(),
        )

    def add_chunk(self, row: Any) -> None:
        self.actual_chunks += 1
        try:
            chunk_index = int(row[5])
            content = bytes(row[6])
            content_length = int(row[7])
            content_sha256 = str(row[8])
            if chunk_index != self.expected_index:
                raise ValueError("chunk indices are not contiguous")
            if content_length != len(content) or content_length <= 0 or content_length > self.chunk_size:
                raise ValueError("invalid chunk length")
            if chunk_index < self.chunk_count - 1 and content_length != self.chunk_size:
                raise ValueError("non-final chunk has an invalid length")
            if hashlib.sha256(content).hexdigest() != content_sha256:
                raise ValueError("chunk checksum mismatch")
            self.digest.update(content)
            self.total += content_length
            self.expected_index += 1
        except (TypeError, ValueError):
            self.invalid = True

    def valid(self) -> bool:
        try:
            if (
                self.file_size <= 0
                or self.file_size > MAX_MEDIA_BYTES
                or self.chunk_size <= 0
                or self.chunk_size > MAX_CHUNK_SIZE
                or self.chunk_count < 0
                or self.chunk_count > MAX_MEDIA_CHUNKS
            ):
                return False
            expected_count = (self.file_size + self.chunk_size - 1) // self.chunk_size if self.file_size else 0
            return (
                not self.invalid
                and self.chunk_count == expected_count
                and self.actual_chunks == self.chunk_count
                and self.expected_index == self.chunk_count
                and self.total == self.file_size
                and self.digest.hexdigest() == self.blob_sha256
            )
        except (TypeError, ValueError):
            return False

    def metadata_is_bounded(self) -> bool:
        """Check before a metadata value can control the per-chunk loop."""
        try:
            if (
                self.file_size <= 0
                or self.file_size > MAX_MEDIA_BYTES
                or self.chunk_size <= 0
                or self.chunk_size > MAX_CHUNK_SIZE
                or self.chunk_count < 0
                or self.chunk_count > MAX_MEDIA_CHUNKS
            ):
                return False
            expected_count = (self.file_size + self.chunk_size - 1) // self.chunk_size if self.file_size else 0
            return self.chunk_count == expected_count
        except (TypeError, ValueError):
            return False


def media_inventory(connection: Any) -> dict[str, object]:
    """Build a deterministic full media report without changing the database.

    The SQL deliberately has no backend-specific placeholders, allowing this
    function to operate through the application adapter, DuckDB, and a raw
    psycopg snapshot connection used by ``pg_dump --snapshot``.
    """
    blob_cursor = connection.execute(
        """
        SELECT id, sha256, file_size, chunk_size, chunk_count
        FROM asset_blobs
        ORDER BY id
        """
    )
    # DuckDB's connection is also its result cursor: issuing a chunk query
    # while this result remains active would discard the remaining metadata
    # rows.  Metadata is bounded (no BYTEA columns), so collect it with
    # ``fetchone`` before issuing the per-chunk content reads below.
    metadata_rows: list[Any] = []
    while row := blob_cursor.fetchone():
        metadata_rows.append(row)
    corrupt_blob_ids: list[str] = []
    corrupt_blob_count = 0
    catalog = hashlib.sha256()
    declared_chunks = 0
    total_blob_bytes = 0
    blob_count = 0
    for row in metadata_rows:
        active = _BlobValidator.from_row(row)
        blob_count += 1
        declared_chunks += active.chunk_count
        total_blob_bytes += active.file_size
        catalog.update(
            f"blob\0{active.blob_id}\0{active.blob_sha256}\0{active.file_size}\0{active.chunk_size}\0{active.chunk_count}\n".encode("utf-8")
        )
        stored_chunk_count = _count_parameters(
            connection,
            "SELECT count(*) FROM asset_blob_chunks WHERE blob_id=?",
            [active.blob_id],
        )
        # Do not select a blob's full chunk list: a default psycopg client
        # cursor may pre-buffer all returned BYTEA rows.  One lookup per
        # expected index bounds the live content object to a single chunk.
        if not active.metadata_is_bounded():
            active.invalid = True
        for chunk_index in range(active.chunk_count if active.metadata_is_bounded() else 0):
            chunk_cursor = _execute_parameters(
                connection,
                """
                SELECT content, content_length, content_sha256
                FROM asset_blob_chunks
                WHERE blob_id=? AND chunk_index=?
                LIMIT 1
                """,
                [active.blob_id, chunk_index],
            )
            chunk = chunk_cursor.fetchone()
            if chunk is None:
                active.invalid = True
                continue
            catalog.update(
                f"chunk\0{active.blob_id}\0{chunk_index}\0{chunk[1]}\0{chunk[2]}\n".encode("utf-8")
            )
            active.add_chunk((None, None, None, None, None, chunk_index, chunk[0], chunk[1], chunk[2]))
        if stored_chunk_count != active.chunk_count:
            active.invalid = True
        if not active.valid():
            corrupt_blob_count += 1
            if len(corrupt_blob_ids) < 20:
                corrupt_blob_ids.append(active.blob_id)

    reference_demo_exists = bool(_count_parameters(
        connection,
        "SELECT count(*) FROM load_cases WHERE id=?",
        [_DEMO_LOAD_CASE_ID],
    ))
    expected_demo_ids = {scene.video_id for scene in DROP_VIDEO_DEMO_SCENES} if reference_demo_exists else set()
    demo_ids: set[str] = set()
    demo_cursor = connection.execute(
        "SELECT video_id FROM drop_video_assets "
        f"WHERE load_case_id='{_DEMO_LOAD_CASE_ID}' ORDER BY video_id"
    )
    while row := demo_cursor.fetchone():
        demo_ids.add(str(row[0]))
    unbound_media_assets = _count(connection, "SELECT count(*) FROM media_assets WHERE blob_id IS NULL")
    missing_media_blobs = _count(
        connection,
        """
        SELECT count(*) FROM media_assets m
        LEFT JOIN asset_blobs b ON b.id=m.blob_id
        WHERE m.blob_id IS NOT NULL AND b.id IS NULL
        """,
    )
    missing_drop_video_blobs = _count(
        connection,
        """
        SELECT count(*) FROM drop_video_assets d
        LEFT JOIN asset_blobs b ON b.id=d.blob_id
        WHERE b.id IS NULL
        """,
    )
    orphan_blobs = _count(
        connection,
        """
        SELECT count(*) FROM asset_blobs b
        WHERE NOT EXISTS (SELECT 1 FROM media_assets m WHERE m.blob_id=b.id)
          AND NOT EXISTS (SELECT 1 FROM drop_video_assets d WHERE d.blob_id=b.id)
        """,
    )
    orphan_chunks = _count(
        connection,
        """
        SELECT count(*) FROM asset_blob_chunks c
        LEFT JOIN asset_blobs b ON b.id=c.blob_id
        WHERE b.id IS NULL
        """,
    )
    media_references = _count(connection, "SELECT count(*) FROM media_assets WHERE blob_id IS NOT NULL")
    drop_video_references = _count(connection, "SELECT count(*) FROM drop_video_assets WHERE blob_id IS NOT NULL")
    # Counts alone cannot distinguish two assets whose blob IDs were swapped.
    # Include the stable logical-reference mapping in the same deterministic
    # catalogue hash, without reading media bytes.
    media_mapping_cursor = connection.execute(
        "SELECT id, blob_id FROM media_assets ORDER BY id"
    )
    while row := media_mapping_cursor.fetchone():
        blob_id = "<null>" if row[1] is None else str(row[1])
        catalog.update(f"media-reference\0{row[0]}\0{blob_id}\n".encode("utf-8"))
    drop_video_mapping_cursor = connection.execute(
        "SELECT video_id, load_case_id, blob_id FROM drop_video_assets "
        "ORDER BY video_id, load_case_id"
    )
    while row := drop_video_mapping_cursor.fetchone():
        blob_id = "<null>" if row[2] is None else str(row[2])
        catalog.update(f"drop-video-reference\0{row[0]}\0{row[1]}\0{blob_id}\n".encode("utf-8"))

    return {
        "format": MEDIA_INVENTORY_FORMAT,
        "format_version": MEDIA_INVENTORY_FORMAT_VERSION,
        "blob_count": blob_count,
        "chunk_count": _count(connection, "SELECT count(*) FROM asset_blob_chunks"),
        "declared_chunk_count": declared_chunks,
        "total_blob_bytes": total_blob_bytes,
        "media_reference_count": media_references,
        "drop_video_reference_count": drop_video_references,
        "reference_count": media_references + drop_video_references,
        "unbound_media_asset_count": unbound_media_assets,
        "missing_media_blob_count": missing_media_blobs,
        "missing_drop_video_blob_count": missing_drop_video_blobs,
        "missing_reference_count": missing_media_blobs + missing_drop_video_blobs,
        "orphan_blob_count": orphan_blobs,
        "orphan_chunk_count": orphan_chunks,
        "corrupt_blob_count": corrupt_blob_count,
        "corrupt_blob_ids": corrupt_blob_ids,
        "content_integrity_verified": corrupt_blob_count == 0,
        "demo_contract_active": reference_demo_exists,
        "demo_expected_count": 20 if reference_demo_exists else 0,
        "demo_count": len(demo_ids),
        "missing_demo_ids": sorted(expected_demo_ids - demo_ids),
        "unexpected_demo_ids": sorted(demo_ids - expected_demo_ids),
        "demo_exact": demo_ids == expected_demo_ids,
        "catalog_sha256": catalog.hexdigest(),
    }


def _schema_failure(report: object) -> None:
    raise MediaIntegrityError(
        json.dumps({"code": "MEDIA_INVENTORY_SCHEMA_INVALID", "media_inventory": report}, ensure_ascii=False, sort_keys=True)
    )


def validate_media_inventory_schema(report: object) -> dict[str, object]:
    """Fail closed when external inventory evidence is incomplete or tampered."""
    if not isinstance(report, dict) or not _REQUIRED_FIELDS.issubset(report):
        _schema_failure(report)
    if report["format"] != MEDIA_INVENTORY_FORMAT or type(report["format_version"]) is not int or report["format_version"] != MEDIA_INVENTORY_FORMAT_VERSION:
        _schema_failure(report)
    for name in _COUNT_FIELDS:
        value = report[name]
        if type(value) is not int or value < 0:
            _schema_failure(report)
    if (
        type(report["content_integrity_verified"]) is not bool
        or type(report["demo_contract_active"]) is not bool
        or type(report["demo_exact"]) is not bool
    ):
        _schema_failure(report)
    catalog_sha256 = report["catalog_sha256"]
    if not isinstance(catalog_sha256, str) or not _SHA256_PATTERN.fullmatch(catalog_sha256):
        _schema_failure(report)
    corrupt_ids = report["corrupt_blob_ids"]
    missing_demo_ids = report["missing_demo_ids"]
    unexpected_demo_ids = report["unexpected_demo_ids"]
    if not all(isinstance(value, list) for value in (corrupt_ids, missing_demo_ids, unexpected_demo_ids)):
        _schema_failure(report)
    if (
        any(not isinstance(value, str) for value in corrupt_ids + missing_demo_ids + unexpected_demo_ids)
        or len(corrupt_ids) != len(set(corrupt_ids))
        or len(missing_demo_ids) != len(set(missing_demo_ids))
        or len(unexpected_demo_ids) != len(set(unexpected_demo_ids))
        or missing_demo_ids != sorted(missing_demo_ids)
        or unexpected_demo_ids != sorted(unexpected_demo_ids)
        or len(corrupt_ids) != min(report["corrupt_blob_count"], 20)
    ):
        _schema_failure(report)
    if report["reference_count"] != report["media_reference_count"] + report["drop_video_reference_count"]:
        _schema_failure(report)
    if report["missing_reference_count"] != report["missing_media_blob_count"] + report["missing_drop_video_blob_count"]:
        _schema_failure(report)
    if report["chunk_count"] != report["declared_chunk_count"] or report["corrupt_blob_count"] > report["blob_count"]:
        _schema_failure(report)
    if report["content_integrity_verified"] != (report["corrupt_blob_count"] == 0):
        _schema_failure(report)
    expected_demo_count = 20 if report["demo_contract_active"] else 0
    if report["demo_expected_count"] != expected_demo_count:
        _schema_failure(report)
    if report["demo_count"] + len(missing_demo_ids) != expected_demo_count + len(unexpected_demo_ids):
        _schema_failure(report)
    if report["demo_exact"] != (
        report["demo_count"] == expected_demo_count and not missing_demo_ids and not unexpected_demo_ids
    ):
        _schema_failure(report)
    return report


def require_media_integrity(report: dict[str, object]) -> None:
    """Reject a report with broken references, bytes, demos, or orphan rows.

    This is the *database-only release* gate, executed only after migration or
    restore has completed.  Upload paths create and attach a blob within the
    same transaction, while ``gc_media_blobs.py`` is the explicit recovery
    tool for failed/interrupted work.  A valid but GC-pending orphan is useful
    during normal operation, but it is not acceptable in a backup/release
    inventory because it cannot be attributed to a restored media reference.
    """
    report = validate_media_inventory_schema(report)
    failing_counts = (
        "unbound_media_asset_count",
        "missing_reference_count",
        "orphan_blob_count",
        "orphan_chunk_count",
    )
    valid = (
        bool(report.get("content_integrity_verified"))
        and bool(report.get("demo_exact"))
        and all(int(report.get(name, 0)) == 0 for name in failing_counts)
        and int(report.get("chunk_count", 0)) == int(report.get("declared_chunk_count", -1))
    )
    if not valid:
        raise MediaIntegrityError(
            json.dumps({"code": "MEDIA_INTEGRITY_FAILED", "media_inventory": report}, ensure_ascii=False, sort_keys=True)
        )
