"""Case results "영상" tab: every VIDEO of a Run option, newest result per Scene (2026-10-02)."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.database import initialize_database
from app.main import app
from app.services import dashboard_capture, dashboard_queries
from tests.test_dashboard_api_slice import _capture_body, _create_user, _login, _request_context

CSV = "MAX_RESULT_Max_Stress_P1 (major)_Mid_C23_scene.h3d.csv"
CSV_TEXT = "Position,Layer_1,Layer_2,Layer_3,Layer_4\nTOP,10,20,30,40\n"


def _scene(item_id, label, media):
    return {"id": item_id, "label": label, "scene_sequence_number": int(label.split("_")[0]), "media": media,
            "observations": []}


def _run(option_id, option_label, scenes):
    return {"id": "run-1", "source_name": "run-a", "load_case_id": "load-1", "load_case_name": "Drop",
            "mode": option_label.upper(), "run_option_id": option_id, "option_label": option_label,
            "option_status": "PRESENT", "scenes": scenes}


def _video(asset_id, name, kind="VIDEO"):
    return {"asset_id": asset_id, "relative_path": f"case/Drop/run-a/INDIVIDUAL/x/{name}", "kind": kind,
            "status": "READY", "component_id": None, "frame_role": "UNKNOWN"}


def _merged_capture():
    first = {"context": {}, "runs": [_run("opt-i", "Individual", [_scene("s1", "1_Face", [_video("a1", "one.mp4"), _video("i1", "one.jpg", "IMAGE")])]),
                                     _run("opt-c", "Cumulative", [_scene("c1", "1_Face", [_video("c1v", "cum.mp4")])])]}
    second = {"context": {}, "runs": [_run("opt-i", "Individual", [_scene("s2", "2_Edge", [_video("a2", "two.mp4"), _video("a3", "three.webm")])])]}
    payload = dashboard_capture.merge_latest_payload([("cap-1", first), ("cap-2", second)])
    payload["context"] = {"project_id": "p", "request_id": "r", "capture_id": "latest:case-1"}
    return {"id": "latest:case-1", "case_id": "case-1", "environment": "DISTRIBUTION", "source_name": "case", "payload": payload}


def test_latest_merged_capture_lists_videos_of_all_scenes_in_order():
    result = dashboard_queries.run_videos(_merged_capture(), "run-1", run_option_id="opt-i")
    assert result["contract_version"] == 1
    assert [(v["scene_label"], v["asset_id"], v["source_capture_id"]) for v in result["videos"]] == [
        ("1_Face", "a1", "cap-1"), ("2_Edge", "a2", "cap-2"), ("2_Edge", "a3", "cap-2")]
    assert result["videos"][0]["title"] == "one.mp4"
    assert result["context"]["run_option_id"] == "opt-i" and result["context"]["option_label"] == "Individual"
    assert result["pagination"] == {"page": 1, "page_size": 20, "total_items": 3, "total_pages": 1,
                                    "has_previous": False, "has_next": False}


def test_option_filter_mode_fallback_and_paging():
    capture = _merged_capture()
    cumulative = dashboard_queries.run_videos(capture, "run-1", run_option_id="opt-c")
    assert [v["asset_id"] for v in cumulative["videos"]] == ["c1v"]
    assert [v["asset_id"] for v in dashboard_queries.run_videos(capture, "run-1", mode="CUMULATIVE")["videos"]] == ["c1v"]
    paged = dashboard_queries.run_videos(capture, "run-1", run_option_id="opt-i", page=2, page_size=2)
    assert [v["asset_id"] for v in paged["videos"]] == ["a3"]
    assert paged["pagination"]["has_previous"] and not paged["pagination"]["has_next"]
    with pytest.raises(dashboard_capture.DashboardCaptureError):
        dashboard_queries.run_videos(capture, "run-1")  # two options, none selected
    with pytest.raises(dashboard_capture.DashboardCaptureError):
        dashboard_queries.run_videos(capture, "run-1", run_option_id="missing")


def test_option_without_videos_returns_empty_page():
    capture = _merged_capture()
    capture["payload"]["runs"][1]["scenes"][0]["media"] = []
    result = dashboard_queries.run_videos(capture, "run-1", run_option_id="opt-c")
    assert result["videos"] == [] and result["pagination"]["total_items"] == 0 and result["pagination"]["total_pages"] == 0


@pytest.mark.duckdb_integration
def test_run_videos_endpoint_merges_captures_and_guards_permission(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "shared"
    option = root / "video-case" / "Drop" / "run-a" / "INDIVIDUAL"
    first = option / "1_Face_Drop_Scene01_Face1_1st"
    first.mkdir(parents=True)
    (first / CSV).write_text(CSV_TEXT, encoding="utf-8")
    (first / "BEHAVIOR_scene01.mp4").write_bytes(b"synthetic-video-1")
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root))
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "dashboard-video-test-secret-key-at-least-32-characters")
    initialize_database()
    project_id, request_id = _request_context()
    admin = _create_user(project_id, role="admin", global_admin=True)
    with TestClient(app) as client:
        headers = _login(client, admin)
        body = _capture_body(project_id, request_id, "video-case", "DISTRIBUTION")
        first_capture = client.post("/api/dashboard/captures", headers=headers, json=body)
        assert first_capture.status_code == 200, first_capture.text
        # The second capture only sees the newly copied Scene folder.
        shutil.rmtree(first)
        second = option / "2_Edge_Drop_Scene02_Edge1_1st"
        second.mkdir()
        (second / CSV).write_text(CSV_TEXT, encoding="utf-8")
        (second / "BEHAVIOR_scene02.mp4").write_bytes(b"synthetic-video-2")
        second_capture = client.post("/api/dashboard/captures", headers=headers, json=body)
        assert second_capture.status_code == 200, second_capture.text
        case_id = second_capture.json()["case_id"]
        latest_id = dashboard_capture.latest_capture_id(case_id)
        catalog = client.get("/api/dashboard/catalog", headers=headers,
                             params={"request_id": request_id, "environment": "DISTRIBUTION"}).json()
        run_option = next(item for item in catalog["run_options"] if item["capture_id"] == latest_id)
        url = f"/api/dashboard/distribution/runs/{run_option['execution_run_id']}/videos"
        response = client.get(url, headers=headers, params={"capture_id": latest_id, "run_option_id": run_option["id"]})
        assert response.status_code == 200, response.text
        videos = response.json()["videos"]
        assert [v["scene_label"] for v in videos] == ["1_Face_Drop_Scene01_Face1_1st", "2_Edge_Drop_Scene02_Edge1_1st"]
        assert videos[0]["source_capture_id"] == first_capture.json()["id"]
        assert videos[1]["source_capture_id"] == second_capture.json()["id"]
        content = client.get(f"/api/dashboard/assets/{videos[0]['asset_id']}", headers=headers)
        assert content.status_code == 200 and content.content == b"synthetic-video-1"
        # The exact (historical) second capture alone only knows its own Scene.
        exact = client.get(url, headers=headers, params={"capture_id": second_capture.json()["id"], "mode": "INDIVIDUAL"})
        assert exact.status_code == 200, exact.text
        assert [v["scene_label"] for v in exact.json()["videos"]] == ["2_Edge_Drop_Scene02_Edge1_1st"]
        assert client.get(url, headers=headers, params={"capture_id": latest_id, "page_size": 21}).status_code == 422
        assert client.get(url, headers=headers, params={"capture_id": latest_id, "run_option_id": "missing"}).status_code == 422

        from app.routers import dashboard as router

        def denied(*args, **kwargs):
            raise HTTPException(403, detail={"code": "PROJECT_DATA_VIEW_DENIED"})

        monkeypatch.setattr(router, "require_permission", denied)
        assert client.get(url, headers=headers, params={"capture_id": latest_id, "run_option_id": run_option["id"]}).status_code == 403
        assert client.get(url, headers=headers, params={"capture_id": second_capture.json()["id"], "mode": "INDIVIDUAL"}).status_code == 403
        assert client.get(url, headers=headers, params={"capture_id": "missing-capture"}).status_code == 404
        client.cookies.clear()
        assert client.get(url, params={"capture_id": latest_id}).status_code == 401
