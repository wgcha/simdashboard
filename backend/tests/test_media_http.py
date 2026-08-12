from __future__ import annotations

from fastapi.testclient import TestClient

from app.database import initialize_database
from app.main import app


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
