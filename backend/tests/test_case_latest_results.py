"""Case results show every Scene under a Run option with its newest result (2026-10-02)."""
from __future__ import annotations

import hashlib
from uuid import uuid4

import pytest

from tests.test_new_scene_registration import (CSV, CSV_BYTES, OPTION, REG, _draft_body, _prepare_new_scene,  # noqa: F401
                                               _seed, admin_client)

pytestmark = pytest.mark.duckdb_integration


def _publish(client, project_id, request_id, prepared, payload=CSV_BYTES):
    files = [{"relative_path": CSV, "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest(), "media_type": "text/csv"}]
    created = client.post(REG + "/drafts", json={**_draft_body(project_id, request_id, prepared, prepared["context"]), "files": files})
    assert created.status_code == 201, created.text
    draft = created.json()["draft_id"]
    assert client.post(REG + f"/drafts/{draft}/files", data={"relative_paths": [CSV]},
                       files=[("files", (CSV, payload, "text/csv"))]).status_code == 200
    inspection = client.post(REG + f"/drafts/{draft}/inspect").json()
    assert client.post(REG + f"/drafts/{draft}/approve", json={"inspection_revision": inspection["inspection_revision"],
                                                               "acknowledge_partial": True}).status_code == 200
    published = client.post(REG + f"/drafts/{draft}/publish", json={"inspection_revision": inspection["inspection_revision"],
                                                                     "idempotency_key": uuid4().hex})
    assert published.status_code == 200, published.text
    return published.json()


def _catalog(client, project_id, request_id):
    response = client.get("/api/dashboard/catalog", params={"project_id": project_id, "request_id": request_id,
                                                            "environment": "DISTRIBUTION"})
    assert response.status_code == 200, response.text
    return response.json()


def test_latest_result_merges_all_scenes_of_a_run_option(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    prepared = _prepare_new_scene(client, project_id, request_id)
    published = _publish(client, project_id, request_id, prepared)
    catalog = _catalog(client, project_id, request_id)

    latest = [item for item in catalog["captures"] if item.get("kind") == "LATEST"]
    assert latest, catalog["captures"]
    case_latest = latest[0]
    # The merged view is listed first so it is the default selection.
    first_for_case = next(item for item in catalog["captures"] if item["case_id"] == case_latest["case_id"])
    assert first_for_case["id"] == case_latest["id"]
    assert case_latest["merged_capture_count"] >= 2
    scenes = {item["label"] for item in catalog["scenes"] if item.get("capture_id") == case_latest["id"]}
    assert {"2_Face", "3_Face", "4_Edge"} <= scenes, scenes
    # The single registration capture alone still only knows 4_Edge (history).
    only_new = {item["label"] for item in catalog["scenes"] if item.get("capture_id") == published["capture_id"]}
    assert only_new == {"4_Edge"}

    option = next(item for item in catalog["run_options"] if item["capture_id"] == case_latest["id"])
    component = next(item for item in catalog["components"] if item["capture_id"] == case_latest["id"])
    response = client.get(f"/api/dashboard/distribution/runs/{option['execution_run_id']}", params={
        "capture_id": case_latest["id"], "run_option_id": option["id"], "mode": option["mode"],
        "component_id": component["id"], "basis": "REPORTED_SUMMARY"})
    assert response.status_code == 200, response.text
    assert {scene["label"] for scene in response.json()["scenes"]} >= {"2_Face", "3_Face", "4_Edge"}


def test_newer_result_of_a_scene_replaces_the_older_one(admin_client):
    from app.database_connection import connect
    from app.services import dashboard_capture
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    prepared = _prepare_new_scene(client, project_id, request_id)
    _publish(client, project_id, request_id, prepared)
    newer = CSV_BYTES.replace(b"20,20,20,20", b"55,55,55,55")
    (root / OPTION / "4_Edge" / CSV).write_bytes(newer)
    client.post("/api/folder-discovery/environments/refresh", json={"project_id": project_id, "request_id": request_id,
                                                                    "environment": "DISTRIBUTION"})
    catalog = _catalog(client, project_id, request_id)
    case_latest = next(item for item in catalog["captures"] if item.get("kind") == "LATEST")
    with connect() as conn:
        merged = dashboard_capture.get_latest_capture(conn, case_latest["dashboard_case_id"])
    edges = [scene for run in merged["payload"]["runs"] for scene in run["scenes"] if scene["label"] == "4_Edge"]
    assert len(edges) == 1
    assert "55" in str(edges[0]) and edges[0]["source_capture_id"] != case_latest["id"]


def test_latest_capture_requires_view_permission(admin_client, monkeypatch):
    from fastapi import HTTPException
    from app.routers import dashboard as router
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    catalog = _catalog(client, project_id, request_id)
    case_latest = next(item for item in catalog["captures"] if item.get("kind") == "LATEST")

    def denied(*args, **kwargs):
        raise HTTPException(403, detail={"code": "PROJECT_DATA_VIEW_DENIED"})

    monkeypatch.setattr(router, "require_permission", denied)
    response = client.get(f"/api/dashboard/usage/cases/{case_latest['dashboard_case_id']}",
                          params={"capture_id": case_latest["id"]})
    assert response.status_code == 403
