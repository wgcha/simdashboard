from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.database import initialize_database
from app.database_connection import connect
from app.main import app
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
        assert client.get("/api/drop-videos/drop-analysis/content", headers={"If-None-Match": etag}).status_code == 304
        download = client.get("/api/drop-videos/drop-analysis/download")
        assert download.status_code == 200
        assert "attachment" in download.headers["content-disposition"]
        assert "tv_drop_analysis_simulation.mp4" in download.headers["content-disposition"]


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
