from __future__ import annotations

from pathlib import Path

import pytest

from app.database import _ensure_seed_media_asset, connect, initialize_database


def _counts(connection):
    return (
        connection.execute("SELECT count(*) FROM media_assets").fetchone()[0],
        connection.execute("SELECT count(*) FROM asset_blobs").fetchone()[0],
        connection.execute("SELECT count(*) FROM asset_blob_chunks").fetchone()[0],
    )


def test_reference_seed_media_is_blob_bound_and_idempotent():
    initialize_database()
    seed_ids = ["media-contour-001", "media-showcase-trust", "media-showcase-multitype"]
    with connect() as connection:
        rows = connection.execute(
            "SELECT id, blob_id, original_filename, file_size, checksum FROM media_assets WHERE id IN (?, ?, ?)",
            seed_ids,
        ).fetchall()
        before = _counts(connection)
    assert {row[0] for row in rows} == set(seed_ids)
    assert all(row[1] and row[2] == "sample-contour.svg" and row[3] > 0 and row[4] for row in rows)

    initialize_database()

    with connect() as connection:
        after = _counts(connection)
        assert after == before
        assert connection.execute(
            "SELECT count(*) FROM media_assets WHERE id IN (?, ?, ?) AND blob_id IS NULL",
            seed_ids,
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    ("filename", "content", "expected", "asset_id"),
    [
        ("missing.svg", None, FileNotFoundError, "media-seed-missing-test"),
        ("invalid.png", b"not-a-png", ValueError, "media-seed-invalid-test"),
    ],
)
def test_seed_media_source_failure_leaves_rows_blobs_and_chunks_unchanged(
    tmp_path: Path,
    filename: str,
    content: bytes | None,
    expected: type[Exception],
    asset_id: str,
):
    initialize_database()
    source = tmp_path / filename
    if content is not None:
        source.write_bytes(content)
    with connect() as connection:
        before = _counts(connection)
        with pytest.raises(expected):
            _ensure_seed_media_asset(
                connection,
                asset_id=asset_id,
                analysis_run_id="run-drop-001",
                asset_type="CONTOUR_IMAGE",
                title="Seed failure test",
                file_path=filename,
                mime_type="image/png" if filename.endswith(".png") else "image/svg+xml",
                metadata_json="{}",
                source=source,
                filename=filename,
                own_transaction=True,
            )
        assert _counts(connection) == before
        assert connection.execute("SELECT count(*) FROM media_assets WHERE id=?", [asset_id]).fetchone()[0] == 0


def test_showcase_seed_row_failure_rolls_back_blob_and_chunks():
    initialize_database()
    source = Path(__file__).parents[1] / "assets" / "sample-contour.svg"

    class FailingMediaInsertConnection:
        backend = "duckdb"

        def __init__(self, delegate):
            self.delegate = delegate

        def execute(self, statement, parameters=None):
            if statement.lstrip().upper().startswith("INSERT INTO MEDIA_ASSETS"):
                raise RuntimeError("injected seed media row failure")
            return self.delegate.execute(statement, parameters)

    with connect() as connection:
        before = _counts(connection)
        with pytest.raises(RuntimeError, match="injected seed media row failure"):
            _ensure_seed_media_asset(
                FailingMediaInsertConnection(connection),
                asset_id="media-showcase-failure-test",
                analysis_run_id="run-drop-001",
                asset_type="IMAGE",
                title="Showcase failure test",
                file_path="sample-contour.svg",
                mime_type="image/svg+xml",
                metadata_json="{}",
                source=source,
                filename="sample-contour.svg",
                own_transaction=True,
            )
        assert _counts(connection) == before
        assert connection.execute(
            "SELECT count(*) FROM media_assets WHERE id='media-showcase-failure-test'"
        ).fetchone()[0] == 0
