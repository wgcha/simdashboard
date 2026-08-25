from __future__ import annotations

import hashlib

import duckdb
import pytest

from app.services.drop_video_demo import DROP_VIDEO_DEMO_SCENES
from app.services.media_integrity import (
    MAX_MEDIA_CHUNKS,
    MediaIntegrityError,
    _execute_parameters,
    media_inventory,
    require_media_integrity,
    validate_media_inventory_schema,
)


pytestmark = pytest.mark.unit


class _StreamingCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):  # pragma: no cover - assertion exercised by a regression
        raise AssertionError("media inventory must stream, not call fetchall")


class _StreamingConnection:
    backend = "duckdb"

    def __init__(self, connection):
        self._connection = connection
        self.statements: list[tuple[str, object | None]] = []

    def execute(self, statement: str, parameters=None):
        self.statements.append((statement, parameters))
        return _StreamingCursor(self._connection.execute(statement, parameters))


def _empty_database() -> _StreamingConnection:
    connection = duckdb.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE asset_blobs (
            id VARCHAR, sha256 VARCHAR, file_size BIGINT, chunk_size INTEGER, chunk_count INTEGER
        );
        CREATE TABLE asset_blob_chunks (
            blob_id VARCHAR, chunk_index INTEGER, content BLOB, content_length INTEGER, content_sha256 VARCHAR
        );
        CREATE TABLE media_assets (id VARCHAR, blob_id VARCHAR);
        CREATE TABLE drop_video_assets (video_id VARCHAR, load_case_id VARCHAR, blob_id VARCHAR);
        CREATE TABLE load_cases (id VARCHAR);
        """
    )
    return _StreamingConnection(connection)


def _streaming_database(*, corrupt_second_chunk: bool = False) -> _StreamingConnection:
    streaming = _empty_database()
    connection = streaming._connection
    first, second = b"a" * 4, b"b" * 3
    content = first + second
    blob_id = "blob-streaming"
    connection.execute(
        "INSERT INTO asset_blobs VALUES (?, ?, ?, ?, ?)",
        [blob_id, hashlib.sha256(content).hexdigest(), len(content), 4, 2],
    )
    second_hash = hashlib.sha256(second).hexdigest()
    if corrupt_second_chunk:
        second_hash = "0" * 64
    connection.execute(
        "INSERT INTO asset_blob_chunks VALUES (?, ?, ?, ?, ?), (?, ?, ?, ?, ?)",
        [
            blob_id,
            0,
            first,
            len(first),
            hashlib.sha256(first).hexdigest(),
            blob_id,
            1,
            second,
            len(second),
            second_hash,
        ],
    )
    connection.execute("INSERT INTO media_assets VALUES ('media-streaming', ?)", [blob_id])
    second_blob_id = "blob-second"
    second_content = b"zz"
    connection.execute(
        "INSERT INTO asset_blobs VALUES (?, ?, ?, ?, ?)",
        [second_blob_id, hashlib.sha256(second_content).hexdigest(), len(second_content), 4, 1],
    )
    connection.execute(
        "INSERT INTO asset_blob_chunks VALUES (?, ?, ?, ?, ?)",
        [second_blob_id, 0, second_content, len(second_content), hashlib.sha256(second_content).hexdigest()],
    )
    connection.execute("INSERT INTO media_assets VALUES ('media-second', ?)", [second_blob_id])
    return streaming


def _reference_demo_database(*, video_ids: list[str] | None = None) -> _StreamingConnection:
    connection = _streaming_database()
    connection._connection.execute("INSERT INTO load_cases VALUES ('loadcase-drop-bottom-001')")
    for video_id in video_ids if video_ids is not None else [scene.video_id for scene in DROP_VIDEO_DEMO_SCENES]:
        connection._connection.execute(
            "INSERT INTO drop_video_assets VALUES (?, 'loadcase-drop-bottom-001', 'blob-streaming')",
            [video_id],
        )
    return connection


def test_inventory_streams_multiple_chunks_without_fetchall() -> None:
    connection = _streaming_database()
    report = media_inventory(connection)

    assert report["blob_count"] == 2
    assert report["chunk_count"] == report["declared_chunk_count"] == 3
    assert report["total_blob_bytes"] == 9
    assert report["content_integrity_verified"] is True
    assert report["demo_contract_active"] is False
    assert report["demo_expected_count"] == 0
    assert report["demo_exact"] is True
    assert report["corrupt_blob_ids"] == []
    content_queries = [
        parameters
        for statement, parameters in connection.statements
        if "SELECT content, content_length, content_sha256" in statement
    ]
    assert content_queries == [
        ["blob-second", 0],
        ["blob-streaming", 0],
        ["blob-streaming", 1],
    ]


def test_database_only_gate_accepts_an_initially_empty_schema() -> None:
    """A production empty seed has no reference load case and needs no demo rows."""
    report = media_inventory(_empty_database())

    assert report["demo_contract_active"] is False
    assert report["demo_expected_count"] == 0
    assert report["demo_count"] == 0
    assert report["demo_exact"] is True
    require_media_integrity(report)


def test_database_only_gate_accepts_the_exact_reference_demo_allowlist() -> None:
    report = media_inventory(_reference_demo_database())

    assert report["demo_contract_active"] is True
    assert report["demo_expected_count"] == 20
    assert report["demo_count"] == 20
    assert report["demo_exact"] is True
    require_media_integrity(report)


@pytest.mark.parametrize("video_ids", [[], [DROP_VIDEO_DEMO_SCENES[0].video_id]])
def test_reference_demo_load_case_rejects_partial_or_empty_allowlist(video_ids: list[str]) -> None:
    report = media_inventory(_reference_demo_database(video_ids=video_ids))

    assert report["demo_contract_active"] is True
    assert report["demo_exact"] is False
    with pytest.raises(MediaIntegrityError, match="MEDIA_INTEGRITY_FAILED"):
        require_media_integrity(report)


def test_database_only_gate_rejects_demo_rows_without_the_reference_load_case() -> None:
    connection = _streaming_database()
    connection._connection.execute(
        "INSERT INTO drop_video_assets VALUES ('drop-analysis', 'loadcase-drop-bottom-001', 'blob-streaming')"
    )
    report = media_inventory(connection)

    assert report["demo_contract_active"] is False
    assert report["demo_expected_count"] == 0
    assert report["unexpected_demo_ids"] == ["drop-analysis"]
    assert report["demo_exact"] is False
    with pytest.raises(MediaIntegrityError, match="MEDIA_INTEGRITY_FAILED"):
        require_media_integrity(report)


def test_inventory_detects_chunk_checksum_corruption_while_streaming() -> None:
    report = media_inventory(_streaming_database(corrupt_second_chunk=True))

    assert report["content_integrity_verified"] is False
    assert report["corrupt_blob_count"] == 1
    assert report["corrupt_blob_ids"] == ["blob-streaming"]


def test_database_only_release_gate_rejects_gc_pending_orphans() -> None:
    report = {
        "format": "analysis-canvas-media-inventory",
        "format_version": 1,
        "blob_count": 1,
        "chunk_count": 1,
        "declared_chunk_count": 1,
        "total_blob_bytes": 1,
        "media_reference_count": 1,
        "drop_video_reference_count": 0,
        "reference_count": 1,
        "content_integrity_verified": True,
        "corrupt_blob_count": 0,
        "corrupt_blob_ids": [],
        "demo_expected_count": 20,
        "demo_contract_active": True,
        "demo_exact": True,
        "demo_count": 20,
        "missing_demo_ids": [],
        "unexpected_demo_ids": [],
        "unbound_media_asset_count": 0,
        "missing_media_blob_count": 0,
        "missing_drop_video_blob_count": 0,
        "missing_reference_count": 0,
        "orphan_blob_count": 1,
        "orphan_chunk_count": 0,
        "catalog_sha256": "a" * 64,
    }

    with pytest.raises(MediaIntegrityError, match="MEDIA_INTEGRITY_FAILED"):
        require_media_integrity(report)


def test_external_inventory_schema_rejects_missing_or_tampered_fields() -> None:
    report = {
        "format": "analysis-canvas-media-inventory",
        "format_version": 1,
        "blob_count": 0,
        "chunk_count": 0,
        "declared_chunk_count": 0,
        "total_blob_bytes": 0,
        "media_reference_count": 0,
        "drop_video_reference_count": 0,
        "reference_count": 0,
        "unbound_media_asset_count": 0,
        "missing_media_blob_count": 0,
        "missing_drop_video_blob_count": 0,
        "missing_reference_count": 0,
        "orphan_blob_count": 0,
        "orphan_chunk_count": 0,
        "corrupt_blob_count": 0,
        "corrupt_blob_ids": [],
        "content_integrity_verified": True,
        "demo_expected_count": 20,
        "demo_contract_active": True,
        "demo_count": 20,
        "missing_demo_ids": [],
        "unexpected_demo_ids": [],
        "demo_exact": True,
        "catalog_sha256": "a" * 64,
    }
    assert validate_media_inventory_schema(report) == report
    incomplete = dict(report)
    incomplete.pop("catalog_sha256")
    previous_contract = dict(report)
    previous_contract.pop("demo_contract_active")
    tampered = {**report, "reference_count": True}

    with pytest.raises(MediaIntegrityError, match="MEDIA_INVENTORY_SCHEMA_INVALID"):
        require_media_integrity(incomplete)
    with pytest.raises(MediaIntegrityError, match="MEDIA_INVENTORY_SCHEMA_INVALID"):
        require_media_integrity(previous_contract)
    with pytest.raises(MediaIntegrityError, match="MEDIA_INVENTORY_SCHEMA_INVALID"):
        require_media_integrity(tampered)


def test_raw_psycopg_parameter_helper_uses_pyformat() -> None:
    captured: dict[str, object] = {}

    class RawPsycopgConnection:
        def execute(self, statement: str, parameters: list[object]):
            captured.update({"statement": statement, "parameters": parameters})
            return _StreamingCursor(duckdb.connect(":memory:").execute("SELECT 1"))

    _execute_parameters(RawPsycopgConnection(), "SELECT 1 WHERE blob_id=?", ["blob-id"])

    assert captured == {"statement": "SELECT 1 WHERE blob_id=%s", "parameters": ["blob-id"]}


def test_inventory_rejects_huge_declared_chunk_count_without_content_queries() -> None:
    connection = _streaming_database()
    connection._connection.execute(
        "INSERT INTO asset_blobs VALUES (?, ?, ?, ?, ?)",
        ["blob-huge", "0" * 64, 1, 1, MAX_MEDIA_CHUNKS + 1],
    )
    connection._connection.execute("INSERT INTO media_assets VALUES ('media-huge', 'blob-huge')")

    report = media_inventory(connection)

    assert "blob-huge" in report["corrupt_blob_ids"]
    content_queries = [
        parameters
        for statement, parameters in connection.statements
        if "SELECT content, content_length, content_sha256" in statement
    ]
    assert all(parameters[0] != "blob-huge" for parameters in content_queries)


def test_catalog_checksum_includes_asset_to_blob_reference_mapping() -> None:
    connection = _streaming_database()
    before = media_inventory(connection)
    connection._connection.execute(
        """
        UPDATE media_assets
        SET blob_id=CASE id
            WHEN 'media-streaming' THEN 'blob-second'
            WHEN 'media-second' THEN 'blob-streaming'
        END
        WHERE id IN ('media-streaming', 'media-second')
        """
    )

    after = media_inventory(connection)

    assert before["blob_count"] == after["blob_count"]
    assert before["reference_count"] == after["reference_count"]
    assert before["catalog_sha256"] != after["catalog_sha256"]


def test_inventory_rejects_empty_blob_under_store_contract() -> None:
    connection = _streaming_database()
    connection._connection.execute(
        "INSERT INTO asset_blobs VALUES (?, ?, ?, ?, ?)",
        ["blob-empty", hashlib.sha256(b"").hexdigest(), 0, 4, 0],
    )
    connection._connection.execute("INSERT INTO media_assets VALUES ('media-empty', 'blob-empty')")

    report = media_inventory(connection)

    assert report["content_integrity_verified"] is False
    assert "blob-empty" in report["corrupt_blob_ids"]
