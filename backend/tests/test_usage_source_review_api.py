"""Focused, isolated review-to-capture contracts for usage sources."""
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app

pytestmark = pytest.mark.duckdb_integration
BASE = "/api/folder-discovery/environments"


@pytest.fixture
def reviewed_client(tmp_path, monkeypatch, password_auth_bootstrap_admin):
    root = tmp_path / "shared"
    root.mkdir()
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root))
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "usage-review-test-secret-at-least-32-characters")
    _, username, password = password_auth_bootstrap_admin
    with TestClient(app) as client:
        response = client.post("/api/auth/login", json={"username": username, "password": password})
        assert response.status_code == 200, response.text
        client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
        yield client, root


def post(client, path, payload):
    response = client.post(BASE + path, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def prepare(client, root, values=None):
    relative = "Project_review/WR_review_SimType1/Assy_RES_review"
    target = root / relative / "Settle" / "75R9J_Set0907_Stand0907_Force_spg0.1_settle_result.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(values if values is not None else {"Set Tilt Angle @ Settle (deg)": 1.18}), encoding="utf-8")
    (target.parent / "settings.json").write_text("not JSON and deliberately ignored", encoding="utf-8")
    scan = post(client, "/scan", {"environment": "USAGE", "relative_path": ""})
    preview = post(client, "/previews", {"scan_id": scan["id"], "assignments": [], "require_usage_review": True})
    assert preview["can_apply"], preview
    return preview, target


def review(client, preview, target, root, **changes):
    body = {"case_relative_path": target.parent.parent.relative_to(root).as_posix(), "acknowledge_partial": True, **changes}
    return post(client, f"/previews/{preview['id']}/usage-review", body)


def register(client, preview):
    return post(client, "/registrations", {"preview_id": preview["id"], "idempotency_key": f"review-{uuid4()}", "capture": True})


def test_review_then_publish_preserves_exact_settle_and_ignores_settings(reviewed_client):
    client, root = reviewed_client
    preview, target = prepare(client, root)
    result = review(client, preview, target, root, acknowledge_partial=False)
    assert result["blocking_count"] == 0 and result["missing_count"] > 0
    assert not result["can_publish"]
    result = review(client, preview, target, root)
    assert result["can_publish"]
    entry = next(e for e in result["entries"] if e["evaluation"] == "Settle")
    assert entry["metrics"][0]["value"] == 1.18
    saved = register(client, preview)
    job = saved["capture_jobs"][0]
    assert job["status"] == "COMPLETED", saved
    response = client.get(f"/api/dashboard/usage/cases/{job['case_id']}", params={"capture_id": job["capture_id"]})
    assert response.status_code == 200, response.text
    assert response.json()["evaluations"][0]["common"]["value"] == 1.18
    case_path = target.parent.parent.relative_to(root).as_posix()
    history = client.get(BASE + f"/registrations/{saved['registration_id']}").json()
    assert history["usage_source_reviews"][case_path]["entries"][0]["metrics"][0]["value"] == 1.18
    frozen = client.post(BASE + f"/previews/{preview['id']}/usage-review", json={"case_relative_path": case_path, "acknowledge_partial": True})
    assert frozen.status_code in {409, 422}, frozen.text


def test_review_mapping_and_metric_exclusion_do_not_hide_valid_slope(reviewed_client):
    client, root = reviewed_client
    preview, target = prepare(client, root, {"a.b": {"actual angle": -1.18}})
    slope = target.parent.parent / "Slope_Angle" / "model_slope_angle_front_result.json"
    slope.parent.mkdir()
    slope.write_text(json.dumps({"Slope Angle (deg)": 0}), encoding="utf-8")
    # Refresh the structural snapshot after adding the evaluation folder.
    scan = post(client, "/scan", {"environment": "USAGE", "relative_path": ""})
    preview = post(client, "/previews", {"scan_id": scan["id"], "assignments": [], "require_usage_review": True})
    result = review(client, preview, target, root)
    assert result["blocking_count"] > 0 and not result["can_publish"]
    result = review(client, preview, target, root,
        metric_paths={"Settle:common:Set Tilt Angle @ Settle (deg)": ["a.b", "actual angle"]},
        excludes={"Slope_Angle:front:OK/NG": "원본 판정 미제공"})
    assert result["can_publish"], result
    saved = register(client, preview)
    job = saved["capture_jobs"][0]
    assert job["status"] == "COMPLETED", saved
    data = client.get(f"/api/dashboard/usage/cases/{job['case_id']}", params={"capture_id": job["capture_id"]}).json()
    assert data["evaluations"][0]["common"]["value"] == -1.18
    cell = data["evaluations"][3]["front"]
    assert cell["value"] == 0 and cell["value_status"] == "READY"
    assert cell["verdict"] is None and cell["verdict_status"] == "EXCLUDED"


def test_source_change_after_review_cannot_publish_unreviewed_bytes(reviewed_client):
    client, root = reviewed_client
    preview, target = prepare(client, root)
    review(client, preview, target, root)
    target.write_text(json.dumps({"Set Tilt Angle @ Settle (deg)": 999}), encoding="utf-8")
    response = client.post(BASE + "/registrations", json={"preview_id": preview["id"], "idempotency_key": f"stale-{uuid4()}", "capture": True})
    if response.status_code == 200:
        job = response.json()["capture_jobs"][0]
        assert job["status"] == "FAILED" and not job["capture_id"], response.text
        retry = post(client, f"/registrations/{response.json()['registration_id']}/capture/retry", {})
        assert retry["capture_jobs"][0]["status"] == "FAILED"
        assert not retry["capture_jobs"][0]["capture_id"]
    else:
        assert response.status_code in {409, 422}, response.text


def test_invalid_review_cannot_be_published_by_direct_api(reviewed_client):
    client, root = reviewed_client
    preview, target = prepare(client, root, {"wrong key": 1})
    unreviewed = client.post(BASE + "/registrations", json={"preview_id": preview["id"], "idempotency_key": f"no-review-{uuid4()}", "capture": True})
    assert unreviewed.status_code in {409, 422}, unreviewed.text
    result = review(client, preview, target, root)
    assert not result["can_publish"]
    response = client.post(BASE + "/registrations", json={"preview_id": preview["id"], "idempotency_key": f"invalid-{uuid4()}", "capture": True})
    if response.status_code == 200:
        assert all(job["status"] == "FAILED" and not job["capture_id"] for job in response.json()["capture_jobs"])
    else:
        assert response.status_code in {409, 422}, response.text


def test_reviewed_retry_rejects_changed_profile(reviewed_client, monkeypatch):
    from app.database_connection import connect
    from app.services import dashboard_capture
    client, root = reviewed_client
    preview, target = prepare(client, root)
    result = review(client, preview, target, root)
    original = dashboard_capture.create_capture
    def fail_capture(*args, **kwargs):
        raise dashboard_capture.DashboardCaptureError("INJECTED_FAILURE", "isolated retry fixture")
    monkeypatch.setattr(dashboard_capture, "create_capture", fail_capture)
    saved = register(client, preview)
    assert saved["capture_jobs"][0]["status"] == "FAILED"
    monkeypatch.setattr(dashboard_capture, "create_capture", original)
    with connect() as conn:
        conn.execute("UPDATE folder_environment_profiles SET revision=revision+1 WHERE id=?", [result["contract"]["profile_id"]])
    response = client.post(BASE + f"/registrations/{saved['registration_id']}/capture/retry", json={})
    if response.status_code == 200:
        assert response.json()["capture_jobs"][0]["status"] == "FAILED"
        assert not response.json()["capture_jobs"][0]["capture_id"]
    else:
        assert response.status_code in {409, 422}, response.text
