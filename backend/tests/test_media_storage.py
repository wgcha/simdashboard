from __future__ import annotations

import hashlib
import io
import json

from fastapi.testclient import TestClient

from app.database import connect, initialize_database
from app.main import app
from app.services.media_storage_service import attach_stored_media, store_stream


def test_chunk_storage_deduplicates_and_round_trips_bytes():
    initialize_database()
    payload = b"\x89PNG\r\n\x1a\n" + b"media-test" * 100
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO media_assets
                (id, analysis_run_id, asset_type, title, file_path, mime_type, file_size, checksum, metadata_json)
            VALUES ('media-test-storage', 'run-drop-001', 'IMAGE', '테스트 이미지', 'test.png', 'image/png', NULL, NULL, ?)
            """,
            [json.dumps({"test": True})],
        )
        first = store_stream(connection, io.BytesIO(payload), filename="원본.png", mime_type="image/png", asset_type="IMAGE")
        attach_stored_media(connection, "media-test-storage", first)
        second = store_stream(connection, io.BytesIO(payload), filename="other.png", mime_type="image/png", asset_type="IMAGE")
        assert first.blob.id == second.blob.id
        assert first.blob.chunk_count == 1
        assert first.blob.sha256 == hashlib.sha256(payload).hexdigest()
        assert connection.execute("SELECT count(*) FROM asset_blobs WHERE sha256=?", [first.blob.sha256]).fetchone()[0] == 1

    with TestClient(app) as client:
        response = client.get("/api/assets/media-test-storage")
        assert response.status_code == 200
        assert response.content == payload
        assert response.headers["etag"] == f'"{first.blob.sha256}"'
        assert client.get("/api/assets/media-test-storage/download").headers["content-disposition"].startswith("attachment;")
        assert client.get("/api/assets/media-test-storage", headers={"Range": "bytes=0-7"}).content == payload[:8]
        assert client.get("/api/assets/media-test-storage", headers={"Range": "bytes=0-7"}).status_code == 206
        assert client.get("/api/assets/media-test-storage", headers={"Range": "bytes=1-0"}).status_code == 416
        assert client.get("/api/assets/media-test-storage", headers={"If-None-Match": f'"{first.blob.sha256}"'}).status_code == 304

    with connect() as connection:
        connection.execute("DELETE FROM media_assets WHERE id='media-test-storage'")
