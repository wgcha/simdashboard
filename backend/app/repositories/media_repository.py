from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator

from ..config import database_settings


CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class BlobRecord:
    id: str
    sha256: str
    file_size: int
    chunk_size: int
    chunk_count: int
    created_at: Any = None
    orphaned_at: Any = None


def _id(value: Any) -> str:
    return str(value)


def _record(row: Any) -> BlobRecord | None:
    if row is None:
        return None
    return BlobRecord(
        id=_id(row[0]),
        sha256=str(row[1]),
        file_size=int(row[2]),
        chunk_size=int(row[3]),
        chunk_count=int(row[4]),
        created_at=row[5] if len(row) > 5 else None,
        orphaned_at=row[6] if len(row) > 6 else None,
    )


def _lock_suffix() -> str:
    # DuckDB does not support PostgreSQL's row-lock syntax. PostgreSQL gets a
    # short lock only around dedup/GC decisions, never while streaming bytes.
    return " FOR UPDATE" if database_settings().backend == "postgresql" else ""


def get_blob(connection: Any, blob_id: str, *, lock: bool = False) -> BlobRecord | None:
    suffix = _lock_suffix() if lock else ""
    return _record(
        connection.execute(
            """
            SELECT id, sha256, file_size, chunk_size, chunk_count, created_at, orphaned_at
            FROM asset_blobs WHERE id=?
            """ + suffix,
            [blob_id],
        ).fetchone()
    )


def find_blob(connection: Any, sha256: str, file_size: int, *, lock: bool = False) -> BlobRecord | None:
    suffix = _lock_suffix() if lock else ""
    return _record(
        connection.execute(
            """
            SELECT id, sha256, file_size, chunk_size, chunk_count, created_at, orphaned_at
            FROM asset_blobs WHERE sha256=? AND file_size=?
            """ + suffix,
            [sha256, file_size],
        ).fetchone()
    )


def insert_blob(connection: Any, blob: BlobRecord) -> BlobRecord:
    connection.execute(
        """
        INSERT INTO asset_blobs
            (id, sha256, file_size, chunk_size, chunk_count, created_at, orphaned_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, NULL)
        ON CONFLICT (sha256, file_size) DO NOTHING
        """,
        [blob.id, blob.sha256, blob.file_size, blob.chunk_size, blob.chunk_count],
    )
    stored = find_blob(connection, blob.sha256, blob.file_size, lock=True)
    if stored is None:
        raise RuntimeError("미디어 blob 저장 후 조회에 실패했습니다.")
    return stored


def insert_chunk(connection: Any, blob_id: str, chunk_index: int, content: bytes) -> None:
    import hashlib

    connection.execute(
        """
        INSERT INTO asset_blob_chunks
            (blob_id, chunk_index, content, content_length, content_sha256)
        VALUES (?, ?, ?, ?, ?)
        """,
        [blob_id, chunk_index, content, len(content), hashlib.sha256(content).hexdigest()],
    )


def validate_blob_chunks(connection: Any, blob: BlobRecord) -> None:
    row = connection.execute(
        """
        SELECT count(*), COALESCE(sum(content_length), 0), min(chunk_index), max(chunk_index)
        FROM asset_blob_chunks WHERE blob_id=?
        """,
        [blob.id],
    ).fetchone()
    count, total, minimum, maximum = int(row[0]), int(row[1]), row[2], row[3]
    if count != blob.chunk_count or total != blob.file_size:
        raise ValueError("미디어 blob 청크 개수 또는 길이가 metadata와 일치하지 않습니다.")
    if count == 0:
        if blob.file_size != 0:
            raise ValueError("비어 있지 않은 blob에 청크가 없습니다.")
        return
    if int(minimum) != 0 or int(maximum) != count - 1:
        raise ValueError("미디어 blob 청크 순서가 연속적이지 않습니다.")
    bad = connection.execute(
        """
        SELECT chunk_index FROM asset_blob_chunks
        WHERE blob_id=? AND (content_length <> octet_length(content)
          OR content_length <= 0 OR content_length > ?)
        LIMIT 1
        """,
        [blob.id, CHUNK_SIZE],
    ).fetchone()
    if bad:
        raise ValueError(f"미디어 blob 청크 invariant가 깨졌습니다: {bad[0]}")


def attach_media_asset(
    connection: Any,
    asset_id: str,
    blob: BlobRecord,
    *,
    original_filename: str,
    mime_type: str,
) -> None:
    connection.execute(
        """
        UPDATE media_assets
        SET blob_id=?, original_filename=?, mime_type=?, file_size=?, checksum=?
        WHERE id=?
        """,
        [blob.id, original_filename, mime_type, blob.file_size, blob.sha256, asset_id],
    )
    if connection.execute("SELECT 1 FROM media_assets WHERE id=?", [asset_id]).fetchone() is None:
        raise ValueError("미디어 asset을 찾을 수 없습니다.")


def get_media_asset(connection: Any, asset_id: str) -> dict[str, Any] | None:
    cursor = connection.execute(
        """
        SELECT m.id, m.analysis_run_id, m.asset_type, m.title, m.file_path,
               m.mime_type, m.file_size, m.checksum, m.metadata_json,
               m.blob_id, m.original_filename,
               b.sha256 AS blob_sha256, b.chunk_size, b.chunk_count
        FROM media_assets m
        LEFT JOIN asset_blobs b ON b.id=m.blob_id
        WHERE m.id=?
        """,
        [asset_id],
    )
    columns = list(cursor.keys()) if hasattr(cursor, "keys") else [column[0] for column in cursor.description]
    row = cursor.fetchone()
    return dict(zip(columns, row)) if row else None


def get_drop_video(connection: Any, video_id: str) -> dict[str, Any] | None:
    cursor = connection.execute(
        """
        SELECT d.video_id, d.load_case_id, d.blob_id, d.original_filename, d.mime_type,
               d.scene_name, d.sort_order, d.metadata_json,
               b.sha256 AS blob_sha256, b.file_size, b.chunk_size, b.chunk_count
        FROM drop_video_assets d
        JOIN asset_blobs b ON b.id=d.blob_id
        WHERE d.video_id=?
        """,
        [video_id],
    )
    columns = list(cursor.keys()) if hasattr(cursor, "keys") else [column[0] for column in cursor.description]
    row = cursor.fetchone()
    return dict(zip(columns, row)) if row else None


def list_drop_videos(connection: Any, load_case_id: str) -> list[dict[str, Any]]:
    cursor = connection.execute(
        """
        SELECT d.video_id, d.load_case_id, d.blob_id, d.original_filename, d.mime_type,
               d.scene_name, d.sort_order, d.metadata_json,
               b.sha256 AS blob_sha256, b.file_size, b.chunk_size, b.chunk_count
        FROM drop_video_assets d
        JOIN asset_blobs b ON b.id=d.blob_id
        WHERE d.load_case_id=? ORDER BY d.sort_order, d.video_id
        """,
        [load_case_id],
    )
    columns = list(cursor.keys()) if hasattr(cursor, "keys") else [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def iter_blob_range(connection: Any, blob_id: str, start: int, end: int) -> Iterator[bytes]:
    blob = get_blob(connection, blob_id)
    if blob is None:
        raise FileNotFoundError(blob_id)
    first_chunk = start // blob.chunk_size
    last_chunk = end // blob.chunk_size
    cursor = connection.execute(
        """
        SELECT chunk_index, content, content_length
        FROM asset_blob_chunks
        WHERE blob_id=? AND chunk_index BETWEEN ? AND ?
        ORDER BY chunk_index
        """,
        [blob_id, first_chunk, last_chunk],
    )
    expected = first_chunk
    while True:
        row = cursor.fetchone()
        if row is None:
            break
        chunk_index, raw_content, content_length = int(row[0]), row[1], int(row[2])
        if chunk_index != expected:
            raise RuntimeError("미디어 blob 청크가 누락되었거나 순서가 바뀌었습니다.")
        content = bytes(raw_content)
        if len(content) != content_length:
            raise RuntimeError("미디어 blob 청크 길이가 손상되었습니다.")
        left = start - chunk_index * blob.chunk_size if chunk_index == first_chunk else 0
        right = end - chunk_index * blob.chunk_size + 1 if chunk_index == last_chunk else len(content)
        if right > left:
            yield content[left:right]
        expected += 1
    if expected != last_chunk + 1:
        raise RuntimeError("미디어 blob 청크 범위가 완전하지 않습니다.")


def mark_orphaned(connection: Any) -> int:
    cursor = connection.execute(
        """
        UPDATE asset_blobs
        SET orphaned_at=COALESCE(orphaned_at, CURRENT_TIMESTAMP)
        WHERE orphaned_at IS NULL
          AND NOT EXISTS (SELECT 1 FROM media_assets WHERE media_assets.blob_id=asset_blobs.id)
          AND NOT EXISTS (SELECT 1 FROM drop_video_assets WHERE drop_video_assets.blob_id=asset_blobs.id)
        """
    )
    return int(getattr(cursor, "rowcount", 0) or 0)


def delete_orphaned(connection: Any, *, older_than: Any) -> int:
    cursor = connection.execute(
        """
        DELETE FROM asset_blobs
        WHERE orphaned_at IS NOT NULL AND orphaned_at < ?
          AND NOT EXISTS (SELECT 1 FROM media_assets WHERE media_assets.blob_id=asset_blobs.id)
          AND NOT EXISTS (SELECT 1 FROM drop_video_assets WHERE drop_video_assets.blob_id=asset_blobs.id)
        """,
        [older_than],
    )
    return int(getattr(cursor, "rowcount", 0) or 0)
