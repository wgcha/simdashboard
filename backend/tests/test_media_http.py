from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import main as main_module
from app.config import media_storage_mode
from app.database import initialize_database
from app.database_connection import connect
from app.main import app
from app.routers import media as media_router
from app.repositories.media_repository import get_blob, get_drop_video
from app.services import media_http
from app.services.media_http import build_media_response


def _stored_demo_blob():
    with connect() as connection:
        stored = get_drop_video(connection, "drop-analysis")
        assert stored is not None
        blob = get_blob(connection, str(stored["blob_id"]))
        assert blob is not None
        return blob


def _media_request() -> Request:
    return Request({"type": "http", "method": "GET", "headers": [], "query_string": b"", "path": "/media"})


def test_media_storage_mode_is_bounded_and_request_read(monkeypatch):
    monkeypatch.delenv("SIMDASH_MEDIA_STORAGE_MODE", raising=False)
    assert media_storage_mode() == "dual-read"
    monkeypatch.setenv("SIMDASH_MEDIA_STORAGE_MODE", "database-only")
    assert media_storage_mode() == "database-only"
    monkeypatch.setenv("SIMDASH_MEDIA_STORAGE_MODE", "invalid")
    with pytest.raises(RuntimeError):
        media_storage_mode()
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/api/load-cases/loadcase-drop-bottom-001/drop-videos").status_code == 500


def test_demo_video_is_database_backed_and_supports_head_range_and_download():
    initialize_database()
    with TestClient(app) as client:
        response = client.get("/api/drop-videos/drop-analysis/content")
        assert response.status_code == 200
        assert response.headers["accept-ranges"] == "bytes"
        assert response.headers["content-type"] == "video/mp4"
        etag = response.headers["etag"]
        assert client.head("/api/drop-videos/drop-analysis/content").status_code == 200
        ranged = client.get("/api/drop-videos/drop-analysis/content", headers={"Range": "bytes=0-15"})
        assert ranged.status_code == 206
        assert len(ranged.content) == 16
        assert ranged.headers["content-range"].startswith("bytes 0-15/")
        assert client.get("/api/drop-videos/drop-analysis/content", headers={"If-Range": '"different"', "Range": "bytes=0-15"}).status_code == 200
        assert client.get("/api/drop-videos/drop-analysis/content", headers={"If-Range": f"W/{etag}", "Range": "bytes=0-15"}).status_code == 200
        assert client.get("/api/drop-videos/drop-analysis/content", headers={"If-None-Match": etag}).status_code == 304
        invalid_range = client.get("/api/drop-videos/drop-analysis/content", headers={"Range": "bytes=1-0"})
        assert invalid_range.status_code == 416
        assert invalid_range.headers["content-length"] == "0"
        download = client.get("/api/drop-videos/drop-analysis/download")
        assert download.status_code == 200
        assert "attachment" in download.headers["content-disposition"]
        assert "tv_drop_analysis_simulation.mp4" in download.headers["content-disposition"]


def test_media_storage_mode_controls_legacy_asset_and_drop_video_fallbacks(monkeypatch):
    initialize_database()
    legacy_asset_id = "media-mode-legacy-test"
    try:
        with TestClient(app) as client:
            # Insert after the app lifespan's seed/backfill pass so this row
            # remains a genuinely legacy file_path-only asset.
            with connect() as connection:
                connection.execute(
                    """
                    INSERT INTO media_assets
                        (id, analysis_run_id, asset_type, title, file_path, mime_type, file_size, checksum, metadata_json)
                    VALUES (?, 'run-drop-001', 'IMAGE', 'Legacy mode test', 'assets/sample-contour.svg', 'image/svg+xml', NULL, NULL, '{}')
                    """,
                    [legacy_asset_id],
                )
            monkeypatch.setattr(media_router, "media_storage_mode", lambda: "dual-read")
            assert client.get(f"/api/assets/{legacy_asset_id}").status_code == 200

            monkeypatch.setattr(media_router, "media_storage_mode", lambda: "database-only")
            monkeypatch.setattr(main_module, "media_storage_mode", lambda: "database-only")
            assert client.get(f"/api/assets/{legacy_asset_id}").status_code == 404

            monkeypatch.setattr(main_module, "list_drop_videos", lambda _connection, _load_case_id: [])
            catalog = client.get("/api/load-cases/loadcase-drop-bottom-001/drop-videos").json()
            assert catalog["source"] == "DATABASE"
            assert catalog["videos"] == []
            assert catalog["pagination"]["total_items"] == 0

            monkeypatch.setattr(main_module, "media_storage_mode", lambda: "dual-read")
            monkeypatch.setattr(media_router, "media_storage_mode", lambda: "dual-read")
            fallback_catalog = client.get("/api/load-cases/loadcase-drop-bottom-001/drop-videos").json()
            assert fallback_catalog["source"] == "EXAMPLE_ADAPTER"
            assert fallback_catalog["pagination"]["total_items"] == 20

            monkeypatch.setattr(main_module, "get_drop_video", lambda _connection, _video_id: None)
            assert client.get("/api/drop-videos/drop-analysis/content").status_code == 200
            monkeypatch.setattr(main_module, "media_storage_mode", lambda: "database-only")
            assert client.get("/api/drop-videos/drop-analysis/content").status_code == 404
            assert client.get("/api/drop-videos/drop-analysis/download").status_code == 404
    finally:
        with connect() as connection:
            connection.execute("DELETE FROM media_assets WHERE id=?", [legacy_asset_id])


def test_svg_response_uses_a_sandboxed_content_security_policy():
    initialize_database()
    with TestClient(app) as client:
        response = client.get("/api/assets/media-contour-001")
        assert response.status_code == 200
        assert response.headers["content-security-policy"].startswith("sandbox;")


def test_result_asset_stream_records_started_and_completed_audit_rows():
    initialize_database()
    asset_path = "/api/assets/media-contour-001"
    with TestClient(app) as client:
        response = client.get(asset_path)
        assert response.status_code == 200

    with connect() as connection:
        rows = connection.execute(
            "SELECT action, status_code, detail_json FROM audit_events "
            "WHERE path=? AND action LIKE 'MEDIA_STREAM_%' ORDER BY occurred_at",
            [asset_path],
        ).fetchall()
    assert [(row[0], row[1]) for row in rows] == [
        ("MEDIA_STREAM_STARTED", 200),
        ("MEDIA_STREAM_COMPLETED", 200),
    ]
    assert json.loads(rows[0][2]) == {"bytes_yielded_to_asgi": 0}
    assert json.loads(rows[1][2])["bytes_yielded_to_asgi"] == len(response.content)


def test_duckdb_video_streaming_releases_connection_before_concurrent_requests():
    initialize_database()
    with TestClient(app) as client:
        def request_status(index: int) -> int:
            if index % 2:
                return client.get("/api/health").status_code
            return client.get("/api/drop-videos/drop-analysis/content").status_code

        with ThreadPoolExecutor(max_workers=20) as executor:
            assert list(executor.map(request_status, range(20))) == [200] * 20


def test_duckdb_paused_stream_releases_connection_before_first_yield_and_records_abort():
    initialize_database()
    events: list[tuple[str, int, int]] = []
    response = build_media_response(
        _media_request(),
        blob=_stored_demo_blob(),
        mime_type="video/mp4",
        filename="demo.mp4",
        audit=lambda action, yielded, status: events.append((action, yielded, status)),
    )

    async def consume_one_then_abort() -> bytes:
        first = await anext(response.body_iterator)
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(lambda: _stored_demo_blob().id).result(timeout=2)
        await response.body_iterator.aclose()
        return first

    first = asyncio.run(consume_one_then_abort())
    assert events == [("STARTED", 0, 200), ("ABORTED", len(first), 200)]


def test_unstarted_duckdb_response_does_not_open_a_spool(monkeypatch):
    initialize_database()
    calls: list[object] = []
    monkeypatch.setattr(media_http, "_materialize_duckdb_range", lambda *_args: calls.append(object()))
    response = build_media_response(
        _media_request(), blob=_stored_demo_blob(), mime_type="video/mp4", filename="demo.mp4"
    )
    assert calls == []
    asyncio.run(response.body_iterator.aclose())
    assert calls == []


def test_duckdb_materialization_error_releases_the_connection(monkeypatch):
    initialize_database()
    blob = _stored_demo_blob()
    monkeypatch.setattr(media_http, "iter_blob_range", lambda *_args: (_ for _ in ()).throw(OSError("read failed")))
    response = build_media_response(_media_request(), blob=blob, mime_type="video/mp4", filename="demo.mp4")

    async def consume_error() -> None:
        with pytest.raises(OSError, match="read failed"):
            await anext(response.body_iterator)

    asyncio.run(consume_error())
    with connect() as connection:
        assert connection.execute("SELECT 1").fetchone() == (1,)
