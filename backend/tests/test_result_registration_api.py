"""Integration coverage for isolated result-registration publication."""
from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database_connection import connect
from app.main import app
from app.security import hash_password
from app.services import dashboard_capture, folder_discovery_environment


pytestmark = pytest.mark.duckdb_integration
BASE = "/api/result-registration"


@pytest.fixture
def registration_client(tmp_path, monkeypatch, password_auth_bootstrap_admin):
    root = tmp_path / "shared"
    root.mkdir()
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root))
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "result-registration-isolated-test-secret-at-least-32")
    suffix = uuid4().hex[:10]
    project_id, request_id = f"result-registration-project-{suffix}", f"result-registration-request-{suffix}"
    project_folder = f"Project_Reg_{suffix}"
    request_folder = f"{project_folder}/WR_Reg_{suffix}_SimType1"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute("INSERT INTO projects(id,name,product_name,description,created_at) VALUES(?,?,?,?,?)",
                     [project_id, f"Registration {suffix}", "Synthetic", "Pipeline API fixture", now])
        conn.execute("""INSERT INTO analysis_requests
            (id,project_id,title,status,owner,owner_user_id,requested_at,due_at,overall_note)
            VALUES(?,?,?,'READY','test',NULL,?,?,?)""",
            [request_id, project_id, f"WR_Reg_{suffix}_SimType1", now, now, ""])
        conn.execute("INSERT INTO spdm_storage_project_parents(project_folder,project_id,created_at,updated_at) VALUES(?,?,?,?)",
                     [project_folder, project_id, now, now])
        conn.execute("INSERT INTO spdm_storage_request_parents(request_folder,project_folder,project_id,request_id,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                     [request_folder, project_folder, project_id, request_id, now, now])
    (root / request_folder).mkdir(parents=True)
    user_id, username, password = password_auth_bootstrap_admin
    try:
        with TestClient(app) as client:
            login = client.post("/api/auth/login", json={"username": username, "password": password})
            assert login.status_code == 200, login.text
            client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
            yield client, root, project_id, request_id, request_folder, user_id
    finally:
        with connect() as conn:
            drafts = [row[0] for row in conn.execute(
                "SELECT id FROM result_registration_drafts WHERE project_id=? AND request_id=?", [project_id, request_id]
            ).fetchall()]
            if drafts:
                marks = ",".join("?" for _ in drafts)
                conn.execute(f"DELETE FROM result_registration_events WHERE draft_id IN ({marks})", drafts)
                conn.execute(f"DELETE FROM result_registration_files WHERE draft_id IN ({marks})", drafts)
                conn.execute(f"DELETE FROM result_registration_drafts WHERE id IN ({marks})", drafts)
            captures = [row[0] for row in conn.execute(
                "SELECT c.id FROM dashboard_captures c JOIN dashboard_cases dc ON dc.id=c.case_id WHERE dc.project_id=? AND dc.request_id=?",
                [project_id, request_id],
            ).fetchall()]
            if captures:
                marks = ",".join("?" for _ in captures)
                conn.execute(f"DELETE FROM dashboard_assets WHERE capture_id IN ({marks})", captures)
                conn.execute(f"DELETE FROM dashboard_captures WHERE id IN ({marks})", captures)
            conn.execute("DELETE FROM dashboard_cases WHERE project_id=? AND request_id=?", [project_id, request_id])
            conn.execute("DELETE FROM result_registration_location_links WHERE project_id=? AND request_id=?", [project_id, request_id])
            conn.execute("DELETE FROM result_registration_paths WHERE project_id=? AND request_id=?", [project_id, request_id])
            scan_ids = [row[0] for row in conn.execute(
                "SELECT id FROM folder_environment_scans WHERE project_id=? AND request_id=?", [project_id, request_id]
            ).fetchall()]
            if scan_ids:
                marks = ",".join("?" for _ in scan_ids)
                conn.execute(f"DELETE FROM folder_environment_registry WHERE registration_id IN "
                             f"(SELECT id FROM folder_environment_registrations WHERE preview_id IN "
                             f"(SELECT id FROM folder_environment_previews WHERE scan_id IN ({marks})))", scan_ids)
                conn.execute(f"DELETE FROM folder_environment_registrations WHERE preview_id IN "
                             f"(SELECT id FROM folder_environment_previews WHERE scan_id IN ({marks}))", scan_ids)
                conn.execute(f"DELETE FROM folder_environment_previews WHERE scan_id IN ({marks})", scan_ids)
                conn.execute(f"DELETE FROM folder_environment_scans WHERE id IN ({marks})", scan_ids)
            conn.execute("DELETE FROM spdm_storage_request_parents WHERE request_id=?", [request_id])
            conn.execute("DELETE FROM spdm_storage_project_parents WHERE project_id=?", [project_id])
            conn.execute("DELETE FROM analysis_requests WHERE id=?", [request_id])
            conn.execute("DELETE FROM projects WHERE id=?", [project_id])


def _prepare(client, request_folder, project_id, request_id):
    _confirm_prepare_request_scope(request_folder, project_id, request_id, "USAGE")
    case_relative = f"{request_folder}/Assy_RES_Registration"
    segments = [{"role_kind": "SIMULATION_CASE", "name": "Assy_RES_Registration"},
                {"role_kind": "EVALUATION", "name": "Settle"}, {"role_kind": "RESULTS", "name": "results"}]
    request_body = {
        "project_id": project_id, "request_id": request_id, "environment": "USAGE",
        "parent_relative_path": request_folder, "segments": segments,
    }
    result_preview = client.post(BASE + "/folders/prepare", json={**request_body, "confirm_create": False})
    assert result_preview.status_code == 200, result_preview.text
    preview = result_preview.json()
    assert preview["status"] == "CONFIRM_REQUIRED"
    confirmed = client.post(BASE + "/folders/prepare", json={**request_body, "confirm_create": True})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["created_paths"] == [case_relative, f"{case_relative}/Settle", f"{case_relative}/Settle/results"]
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM dashboard_cases WHERE project_id=? AND request_id=?", [project_id, request_id]).fetchone()[0] == 0
    _confirm_folder_schema(request_folder, project_id, request_id, "USAGE", {
        case_relative: "SIMULATION_CASE",
        f"{case_relative}/Settle": "EVALUATION",
        f"{case_relative}/Settle/results": "RESULTS",
    })
    prepared = confirmed.json()
    locations = client.get(BASE + "/locations", params={
        "project_id": project_id, "request_id": request_id, "environment": "USAGE",
    })
    assert locations.status_code == 200, locations.text
    candidate = next(item for item in locations.json()["candidates"]
                     if item["relative_path"] == prepared["result_relative_path"])
    prepared["context"] = candidate["context"]
    return case_relative, prepared


def _confirm_folder_schema(request_folder, project_id, request_id, environment, roles):
    from types import SimpleNamespace

    root = Path(os.environ["SIMDASH_SPDM_ROOT"])
    project_folder = request_folder.split("/", 1)[0]
    with connect() as conn:
        scan = folder_discovery_environment.save_scan(
            conn, root, project_folder, environment, None, project_id, request_id, "test-user")
        by_path = {node["relative_path"]: node for node in scan["nodes"]}
        request_node = by_path[request_folder]
        assignments = [{"node_id": request_node["id"], "role_kind": "REQUEST", "confirm": True,
                        "target_mode": "LINK", "target_id": request_id}]
        assignments.extend({"node_id": by_path[path]["id"], "role_kind": role, "confirm": True}
                           for path, role in roles.items())
        preview = folder_discovery_environment.preview(conn, scan["id"], assignments, "test-user")
        assert preview["can_apply"] is True, preview
        folder_discovery_environment.register(
            conn, preview["id"], f"api-schema-{uuid4().hex}", None,
            SimpleNamespace(user_id="test-user"), root)


def _confirm_prepare_request_scope(request_folder, project_id, request_id, environment):
    """Give prepare an applied request-bound schema before it creates paths."""
    root = Path(os.environ["SIMDASH_SPDM_ROOT"])
    seed = (f"{request_folder}/Assy_RES_SchemaSeed" if environment == "USAGE"
            else f"{request_folder}/Package_SchemaSeed")
    seed_path = root.joinpath(*seed.split("/"))
    seed_path.mkdir(parents=True, exist_ok=True)
    try:
        _confirm_folder_schema(request_folder, project_id, request_id, environment,
                               {seed: "SIMULATION_CASE"})
    finally:
        seed_path.rmdir()


def _create_draft(client, project_id, request_id, prep, content):
    case_relative, folder = prep
    relative = "model_settle_result.json"
    body = {"project_id": project_id, "request_id": request_id, "environment": "USAGE",
            "case_relative_path": case_relative, "result_relative_path": folder["result_relative_path"],
            "context": folder["context"],
            "files": [{"relative_path": relative, "size": len(content), "sha256": None, "media_type": "application/json"}]}
    created = client.post(BASE + "/drafts", json=body)
    assert created.status_code == 201, created.text
    upload = client.post(BASE + f"/drafts/{created.json()['draft_id']}/files",
                         data={"relative_paths": relative},
                         files=[("files", (relative, content, "application/json"))])
    assert upload.status_code == 200, upload.text
    inspected = client.post(BASE + f"/drafts/{created.json()['draft_id']}/inspect")
    assert inspected.status_code == 200, inspected.text
    return created.json()["draft_id"], inspected.json()


def test_saved_result_location_links_follow_folder_schema_and_preserve_draft_and_files(registration_client):
    client, root, project_id, request_id, request_folder, _ = registration_client
    case_a = f"{request_folder}/Assy_RES_LinkCase_A"
    case_b = f"{request_folder}/Assy_RES_LinkCase_B"
    first = f"{case_a}/Settle/results"
    second = f"{case_b}/Wobble/output_data"
    for relative in (first, second):
        path = root.joinpath(*relative.split("/"))
        path.mkdir(parents=True)
        (path / "keep.txt").write_text(relative, encoding="utf-8")
    _confirm_folder_schema(request_folder, project_id, request_id, "USAGE", {
        case_a: "SIMULATION_CASE",
        f"{case_a}/Settle": "EVALUATION",
        first: "RESULTS",
        case_b: "SIMULATION_CASE",
        f"{case_b}/Wobble": "EVALUATION",
        second: "RESULTS",
    })

    target_response = client.get(BASE + "/targets", params={"environment": "USAGE"})
    assert target_response.status_code == 200, target_response.text
    target = next(item for item in target_response.json()["targets"] if item["request_id"] == request_id)
    assert {item["relative_path"] for item in target["cases"]} == {case_a, case_b}

    listed = client.get(BASE + "/locations", params={
        "project_id": project_id, "request_id": request_id, "environment": "USAGE",
    })
    assert listed.status_code == 200, listed.text
    candidates = {item["relative_path"]: item for item in listed.json()["candidates"]}
    assert set(candidates) == {first, second}
    assert candidates[second]["schema_parent_path"] == f"{case_b}/Wobble"

    draft = client.post(BASE + "/drafts", json={
        "project_id": project_id, "request_id": request_id, "environment": "USAGE",
        "case_relative_path": case_a, "result_relative_path": first,
        "context": candidates[first]["context"],
        "files": [{"relative_path": "result.json", "size": 2, "media_type": "application/json"}],
    })
    assert draft.status_code == 201, draft.text
    draft_id = draft.json()["draft_id"]

    payload = {"project_id": project_id, "request_id": request_id,
               "environment": "USAGE", "relative_path": first}
    created = client.post(BASE + "/locations", json=payload)
    assert created.status_code == 201, created.text
    link = created.json()
    duplicate = client.post(BASE + "/locations", json=payload)
    assert duplicate.status_code == 409

    invalid = client.post(BASE + "/locations", json={**payload, "relative_path": f"{case_a}/Settle/arbitrary"})
    assert invalid.status_code == 409
    updated = client.patch(BASE + f"/locations/{link['id']}", json={**payload, "relative_path": second,
        "revision": link["revision"]})
    assert updated.status_code == 200, updated.text
    assert updated.json()["relative_path"] == second
    assert updated.json()["revision"] == link["revision"] + 1
    stale = client.patch(BASE + f"/locations/{link['id']}", json={**payload, "relative_path": first,
        "revision": link["revision"]})
    assert stale.status_code == 409

    saved_draft = client.get(BASE + f"/drafts/{draft_id}")
    assert saved_draft.status_code == 200, saved_draft.text
    assert saved_draft.json()["result_relative_path"] == first
    deleted = client.delete(BASE + f"/locations/{link['id']}", params={
        "project_id": project_id, "request_id": request_id, "environment": "USAGE",
        "revision": updated.json()["revision"],
    })
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"] is True
    after_delete = client.get(BASE + "/locations", params={
        "project_id": project_id, "request_id": request_id, "environment": "USAGE",
    })
    assert after_delete.status_code == 200, after_delete.text
    assert after_delete.json()["links"] == []
    assert (root / first / "keep.txt").read_text(encoding="utf-8") == first
    assert (root / second / "keep.txt").read_text(encoding="utf-8") == second
    preserved_draft = client.get(BASE + f"/drafts/{draft_id}")
    assert preserved_draft.status_code == 200, preserved_draft.text
    assert preserved_draft.json()["result_relative_path"] == first


def test_schema_targets_and_saved_locations_reject_late_foreign_owner_conflicts(registration_client):
    client, root, project_id, request_id, request_folder, _ = registration_client
    case_a = f"{request_folder}/Assy_RES_OwnedCase_A"
    case_b = f"{request_folder}/Assy_RES_OwnedCase_B"
    result_a = f"{case_a}/Settle/results"
    result_b = f"{case_b}/Wobble/results"
    for relative in (result_a, result_b):
        root.joinpath(*relative.split("/")).mkdir(parents=True)
    _confirm_folder_schema(request_folder, project_id, request_id, "USAGE", {
        case_a: "SIMULATION_CASE", f"{case_a}/Settle": "EVALUATION", result_a: "RESULTS",
        case_b: "SIMULATION_CASE", f"{case_b}/Wobble": "EVALUATION", result_b: "RESULTS",
    })
    payload = {"project_id": project_id, "request_id": request_id,
               "environment": "USAGE", "relative_path": result_a}
    created = client.post(BASE + "/locations", json=payload)
    assert created.status_code == 201, created.text
    link = created.json()

    root_key = folder_discovery_environment.root_identity(root)
    owner_claim_ids = [f"late-owner-{uuid4().hex}" for _ in (case_a, case_b)]
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        for claim_id, case_path in zip(owner_claim_ids, (case_a, case_b)):
            conn.execute(
                "INSERT INTO result_registration_paths "
                "(id,root_key,project_id,request_id,environment,relative_path,path_key,parent_relative_path,"
                "role_kind,target_id,raw_name,option_status,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [claim_id, root_key, "other-project", "other-request", "USAGE", case_path,
                 case_path.casefold(), str(Path(case_path).parent).replace("\\", "/"),
                 "SIMULATION_CASE", f"other-case-{claim_id}", Path(case_path).name, None, "test-owner", now],
            )
    try:
        targets = client.get(BASE + "/targets", params={"environment": "USAGE"})
        assert targets.status_code == 200, targets.text
        target = next(item for item in targets.json()["targets"] if item["request_id"] == request_id)
        assert {item["relative_path"] for item in target["cases"]}.isdisjoint({case_a, case_b})

        folders = client.get(BASE + "/folders", params={
            "project_id": project_id, "request_id": request_id, "environment": "USAGE",
            "parent_relative_path": request_folder,
        })
        assert folders.status_code == 200, folders.text
        assert {item["relative_path"] for item in folders.json()["nodes"]}.isdisjoint({case_a, case_b})

        listed = client.get(BASE + "/locations", params={
            "project_id": project_id, "request_id": request_id, "environment": "USAGE",
        })
        assert listed.status_code == 200, listed.text
        assert listed.json()["candidates"] == []
        stale_link = next(item for item in listed.json()["links"] if item["id"] == link["id"])
        assert stale_link["is_current"] is False

        create_blocked = client.post(BASE + "/locations", json={**payload, "relative_path": result_b})
        assert create_blocked.status_code == 409
        assert create_blocked.json()["detail"]["code"] == "RESULT_LOCATION_SCHEMA_INVALID"
        update_blocked = client.patch(BASE + f"/locations/{link['id']}", json={
            **payload, "revision": link["revision"],
        })
        assert update_blocked.status_code == 409
        assert update_blocked.json()["detail"]["code"] == "RESULT_LOCATION_SCHEMA_INVALID"
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM result_registration_paths WHERE id IN (?,?)", owner_claim_ids)


def test_multipart_upload_preserves_binary_crlf_cr_nul_and_sha256(registration_client):
    client, _root, project_id, request_id, request_folder, _user_id = registration_client
    case_relative, prepared = _prepare(client, request_folder, project_id, request_id)
    content = b"abc\r\nxyz\rEND\x00\xff"
    relative_path = "tv_drop_simulation_variant_01.mp4"
    created = client.post(BASE + "/drafts", json={
        "project_id": project_id, "request_id": request_id, "environment": "USAGE",
        "case_relative_path": case_relative,
        "result_relative_path": prepared["result_relative_path"],
        "context": prepared["context"],
        "files": [{"relative_path": relative_path, "size": len(content),
                   "sha256": hashlib.sha256(content).hexdigest(), "media_type": "video/mp4"}],
    })
    assert created.status_code == 201, created.text
    draft_id = created.json()["draft_id"]

    uploaded = client.post(BASE + f"/drafts/{draft_id}/files",
                          data={"relative_paths": relative_path},
                          files=[("files", (relative_path, content, "video/mp4"))])
    assert uploaded.status_code == 200, uploaded.text
    with connect() as conn:
        row = conn.execute(
            "SELECT sha256,content FROM result_registration_files WHERE draft_id=? AND relative_path=?",
            [draft_id, relative_path],
        ).fetchone()
    assert row == (hashlib.sha256(content).hexdigest(), content)


def _approve_and_publish(client, draft_id, inspection, *, acknowledge=True, key=None):
    approved = client.post(BASE + f"/drafts/{draft_id}/approve", json={
        "inspection_revision": inspection["inspection_revision"], "acknowledge_partial": acknowledge,
    })
    return approved, client.post(BASE + f"/drafts/{draft_id}/publish", json={
        "inspection_revision": inspection["inspection_revision"], "idempotency_key": key or f"publish-{uuid4().hex}",
    })


def test_staged_usage_upload_isolated_until_approval_then_capture_is_idempotent(registration_client):
    client, root, project_id, request_id, request_folder, _ = registration_client
    prep = _prepare(client, request_folder, project_id, request_id)
    content = json.dumps({"Set Tilt Angle @ Settle (deg)": 1.18}).encode()
    draft_id, inspection = _create_draft(client, project_id, request_id, prep, content)
    assert inspection["blocking_count"] == 0
    assert inspection["missing_count"] > 0
    assert any(item["status"] == "READY" for item in inspection["metrics"])
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM dashboard_captures").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM dashboard_assets").fetchone()[0] == 0
    staged = client.get(BASE + f"/drafts/{draft_id}")
    assert staged.status_code == 200, staged.text
    assert staged.json()["status"] == "INSPECTED"
    assert (root / prep[1]["result_relative_path"] / "model_settle_result.json").exists() is False
    key = f"publish-{uuid4().hex}"
    approved, published = _approve_and_publish(client, draft_id, inspection, key=key)
    assert approved.status_code == 200, approved.text
    assert published.status_code == 200, published.text
    result = published.json()
    assert result["status"] == "PUBLISHED"
    assert result["asset_count"] == 1
    assert result["result_count"] == 1
    assert result["capture_id"]
    assert (root / prep[1]["result_relative_path"] / "model_settle_result.json").read_bytes() == content
    case_result = client.get(f"/api/dashboard/usage/cases/{result['case_id']}", params={"capture_id": result["capture_id"]})
    assert case_result.status_code == 200, case_result.text
    with connect() as conn:
        # The registration capture plus, since 2026-10-02, a whole-folder capture
        # from the post-publish refresh (the newest version reflects the folder).
        published_count = conn.execute("SELECT count(*) FROM dashboard_captures WHERE case_id=?", [result["case_id"]]).fetchone()[0]
    assert 1 <= published_count <= 2
    repeated = client.post(BASE + f"/drafts/{draft_id}/publish", json={
        "inspection_revision": inspection["inspection_revision"], "idempotency_key": key,
    })
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["capture_id"] == result["capture_id"]
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM dashboard_captures WHERE case_id=?", [result["case_id"]]).fetchone()[0] == published_count


def test_mirror_conflict_keeps_capture_and_retry_only_copies_files(registration_client):
    client, root, project_id, request_id, request_folder, _ = registration_client
    prep = _prepare(client, request_folder, project_id, request_id)
    content = json.dumps({"Set Tilt Angle @ Settle (deg)": 1.18}).encode()
    draft_id, inspection = _create_draft(client, project_id, request_id, prep, content)
    existing = root / prep[1]["result_relative_path"] / "model_settle_result.json"
    existing.write_bytes(b"different pre-existing file")
    approved, published = _approve_and_publish(client, draft_id, inspection)
    assert approved.status_code == 200, approved.text
    assert published.status_code == 200, published.text
    result = published.json()
    assert result["status"] == "MIRROR_CONFLICT"
    assert result["capture_id"] and result["case_id"]
    assert result["error"]["code"] == "MIRROR_CONFLICT"
    case_result = client.get(f"/api/dashboard/usage/cases/{result['case_id']}", params={"capture_id": result["capture_id"]})
    assert case_result.status_code == 200, case_result.text
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM dashboard_captures WHERE id=?", [result["capture_id"]]).fetchone()[0] == 1
    existing.unlink()
    retried = client.post(BASE + f"/drafts/{draft_id}/mirror/retry")
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "PUBLISHED"
    assert existing.read_bytes() == content


def test_changed_upload_invalidates_approval_and_failed_capture_is_retryable(registration_client, monkeypatch):
    client, root, project_id, request_id, request_folder, _ = registration_client
    prep = _prepare(client, request_folder, project_id, request_id)
    content = json.dumps({"Set Tilt Angle @ Settle (deg)": 1.18}).encode()
    draft_id, inspection = _create_draft(client, project_id, request_id, prep, content)
    approved = client.post(BASE + f"/drafts/{draft_id}/approve", json={
        "inspection_revision": inspection["inspection_revision"], "acknowledge_partial": True,
    })
    assert approved.status_code == 200, approved.text
    changed = json.dumps({"Set Tilt Angle @ Settle (deg)": 2.18}).encode()
    upload = client.post(BASE + f"/drafts/{draft_id}/files", data={"relative_paths": "model_settle_result.json"},
                         files=[("files", ("model_settle_result.json", changed, "application/json"))])
    assert upload.status_code == 200, upload.text
    read = client.get(BASE + f"/drafts/{draft_id}")
    assert read.status_code == 200
    assert read.json()["status"] == "DRAFT"
    stale = client.post(BASE + f"/drafts/{draft_id}/publish", json={
        "inspection_revision": inspection["inspection_revision"], "idempotency_key": f"stale-{uuid4().hex}",
    })
    assert stale.status_code == 409, stale.text

    inspected = client.post(BASE + f"/drafts/{draft_id}/inspect")
    assert inspected.status_code == 200, inspected.text
    approved = client.post(BASE + f"/drafts/{draft_id}/approve", json={
        "inspection_revision": inspected.json()["inspection_revision"], "acknowledge_partial": True,
    })
    assert approved.status_code == 200, approved.text
    original = dashboard_capture.create_capture
    attempts = {"count": 0}

    def fail_once(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise dashboard_capture.DashboardCaptureError("SYNTHETIC_CAPTURE_FAILURE", "synthetic failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(dashboard_capture, "create_capture", fail_once)
    key = f"retry-{uuid4().hex}"
    publish_payload = {"inspection_revision": inspected.json()["inspection_revision"], "idempotency_key": key}
    failed = client.post(BASE + f"/drafts/{draft_id}/publish", json=publish_payload)
    assert failed.status_code == 422, failed.text
    failed_state = client.get(BASE + f"/drafts/{draft_id}")
    assert failed_state.status_code == 200
    assert failed_state.json()["status"] == "PUBLISH_FAILED"
    assert failed_state.json()["capture_id"] is None
    retried = client.post(BASE + f"/drafts/{draft_id}/publish", json=publish_payload)
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "PUBLISHED"
    # One failed and one successful registration capture; the post-publish
    # whole-folder capture (2026-10-02) may add one more call.
    assert attempts["count"] in {2, 3}


def test_upload_rejects_traversal_undeclared_paths_and_client_hash_mismatch(registration_client):
    client, _, project_id, request_id, request_folder, _ = registration_client
    prep = _prepare(client, request_folder, project_id, request_id)
    case_relative, folder = prep
    content = json.dumps({"Set Tilt Angle @ Settle (deg)": 1.18}).encode()
    created = client.post(BASE + "/drafts", json={
        "project_id": project_id, "request_id": request_id, "environment": "USAGE",
        "case_relative_path": case_relative, "result_relative_path": folder["result_relative_path"],
        "context": folder["context"],
        "files": [{"relative_path": "expected.json", "size": len(content), "sha256": "a" * 64,
                   "media_type": "application/json"}],
    })
    assert created.status_code == 201, created.text
    draft_id = created.json()["draft_id"]

    def upload(relative: str, filename: str):
        return client.post(BASE + f"/drafts/{draft_id}/files", data={"relative_paths": relative},
                           files=[("files", (filename, content, "application/json"))])

    traversal = upload("../expected.json", "expected.json")
    assert traversal.status_code == 422, traversal.text
    assert traversal.json()["detail"]["code"] == "RESULT_PATH_INVALID"
    undeclared = upload("other.json", "other.json")
    assert undeclared.status_code == 422, undeclared.text
    assert undeclared.json()["detail"]["code"] == "RESULT_UPLOAD_NOT_DECLARED"
    bad_hash = upload("expected.json", "expected.json")
    assert bad_hash.status_code == 422, bad_hash.text
    assert bad_hash.json()["detail"]["code"] == "RESULT_FILE_HASH_MISMATCH"
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM result_registration_files WHERE draft_id=?", [draft_id]).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM dashboard_captures").fetchone()[0] == 0


def test_draft_media_and_publish_recheck_result_import_after_membership_revocation(registration_client):
    client, _, project_id, request_id, request_folder, _ = registration_client
    prep = _prepare(client, request_folder, project_id, request_id)
    content = json.dumps({"Set Tilt Angle @ Settle (deg)": 1.18}).encode()
    draft_id, inspection = _create_draft(client, project_id, request_id, prep, content)
    user_id = f"result-registration-uploader-{uuid4().hex[:12]}"
    username = user_id
    password = "registration-test-password"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute("""INSERT INTO users
            (id,username,password_hash,display_name,legacy_role,is_active,created_at,updated_at,account_status,is_global_admin)
            VALUES(?,?,?,?,?,true,?,?,'ACTIVE',false)""",
            [user_id, username, hash_password(password), "Registration Uploader", "editor", now, now])
        conn.execute("""INSERT INTO project_memberships
            (id,project_id,user_id,role,created_by,created_at,updated_by,updated_at)
            VALUES(?,?,?,'power','test',?,'test',?)""",
            [f"result-registration-membership-{uuid4().hex}", project_id, user_id, now, now])
    try:
        login = client.post("/api/auth/login", json={"username": username, "password": password})
        assert login.status_code == 200, login.text
        client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        assert client.get(BASE + f"/drafts/{draft_id}").status_code == 200
        with connect() as conn:
            conn.execute("DELETE FROM project_memberships WHERE project_id=? AND user_id=?", [project_id, user_id])
        assert client.get(BASE + f"/drafts/{draft_id}").status_code == 403
        media = client.get(BASE + f"/drafts/{draft_id}/media", params={"relative_path": "model_settle_result.json"})
        assert media.status_code == 403, media.text
        publish = client.post(BASE + f"/drafts/{draft_id}/publish", json={
            "inspection_revision": inspection["inspection_revision"], "idempotency_key": f"revoked-{uuid4().hex}",
        })
        assert publish.status_code == 403, publish.text
        with connect() as conn:
            assert conn.execute("SELECT count(*) FROM dashboard_captures").fetchone()[0] == 0
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM project_memberships WHERE project_id=? AND user_id=?", [project_id, user_id])
            conn.execute("DELETE FROM users WHERE id=?", [user_id])


def _prepare_distribution(client, request_folder, project_id, request_id):
    _confirm_prepare_request_scope(request_folder, project_id, request_id, "DISTRIBUTION")
    segments = [
        {"role_kind": "SIMULATION_CASE", "name": "Assy_RES_Distribution"},
        {"role_kind": "LOAD_CASE", "name": "Drop"},
        {"role_kind": "EXECUTION_RUN", "name": "run-a"},
        {"role_kind": "RUN_OPTION", "name": "INDIVIDUAL"},
        {"role_kind": "SCENE", "name": "1_Face_Drop_Scene01_Face1_1st"},
        {"role_kind": "RESULTS", "name": "results"},
    ]
    body = {"project_id": project_id, "request_id": request_id, "environment": "DISTRIBUTION",
            "parent_relative_path": request_folder, "segments": segments}
    preview = client.post(BASE + "/folders/prepare", json={**body, "confirm_create": False})
    assert preview.status_code == 200, preview.text
    prepared = client.post(BASE + "/folders/prepare", json={**body, "confirm_create": True})
    assert prepared.status_code == 200, prepared.text
    result = prepared.json()
    case = result["case_relative_path"]
    scene = result["result_relative_path"].rsplit("/", 1)[0]
    _confirm_folder_schema(request_folder, project_id, request_id, "DISTRIBUTION", {
        case: "SIMULATION_CASE",
        f"{case}/Drop": "LOAD_CASE",
        f"{case}/Drop/run-a": "EXECUTION_RUN",
        f"{case}/Drop/run-a/INDIVIDUAL": "RUN_OPTION",
        scene: "SCENE",
        result["result_relative_path"]: "RESULTS",
    })
    locations = client.get(BASE + "/locations", params={
        "project_id": project_id, "request_id": request_id, "environment": "DISTRIBUTION",
    })
    assert locations.status_code == 200, locations.text
    candidate = next(item for item in locations.json()["candidates"]
                     if item["relative_path"] == result["result_relative_path"])
    result["context"] = candidate["context"]
    return result


def test_distribution_inspection_and_capture_keep_metric_scene_and_media_context(registration_client):
    client, root, project_id, request_id, request_folder, _ = registration_client
    prepared = _prepare_distribution(client, request_folder, project_id, request_id)
    csv_path = "MAX_RESULT_Max_Stress_P1 (major)_Mid_C23_scene.h3d.csv"
    image_path = "CONTOUR_COMP23_Max_Stress_P1 (major)_Mid.jpg"
    csv_bytes = b"Position,Layer_1,Layer_2,Layer_3,Layer_4\nTOP,10,20,30,40\n"
    image_bytes = b"synthetic-contour-image"
    files = [
        {"relative_path": csv_path, "size": len(csv_bytes), "sha256": None, "media_type": "text/csv"},
        {"relative_path": image_path, "size": len(image_bytes), "sha256": None, "media_type": "image/jpeg"},
    ]
    created = client.post(BASE + "/drafts", json={
        "project_id": project_id, "request_id": request_id, "environment": "DISTRIBUTION",
        "case_relative_path": prepared["case_relative_path"],
        "result_relative_path": prepared["result_relative_path"], "context": prepared["context"], "files": files,
    })
    assert created.status_code == 201, created.text
    draft_id = created.json()["draft_id"]
    uploaded = client.post(BASE + f"/drafts/{draft_id}/files",
        data={"relative_paths": [csv_path, image_path]},
        files=[("files", (csv_path, csv_bytes, "text/csv")), ("files", (image_path, image_bytes, "image/jpeg"))])
    assert uploaded.status_code == 200, uploaded.text
    inspected = client.post(BASE + f"/drafts/{draft_id}/inspect")
    assert inspected.status_code == 200, inspected.text
    inspection = inspected.json()
    assert inspection["metric_count"] == 4
    assert all(item["status"] == "READY" for item in inspection["metrics"])
    assert {item["evaluation"] for item in inspection["metrics"]} == {"1_Face_Drop_Scene01_Face1_1st"}
    image_hash = next(item["sha256"] for item in inspection["manifest"] if item["relative_path"] == image_path)
    assert inspection["media"] == [{
        "relative_path": image_path, "sha256": image_hash,
        "size": len(image_bytes), "media_type": "image/jpeg", "kind": "IMAGE",
        "title": image_path, "status": "READY",
    }]
    approved = client.post(BASE + f"/drafts/{draft_id}/approve", json={
        "inspection_revision": inspection["inspection_revision"], "acknowledge_partial": False,
    })
    assert approved.status_code == 200, approved.text
    published = client.post(BASE + f"/drafts/{draft_id}/publish", json={
        "inspection_revision": inspection["inspection_revision"], "idempotency_key": f"distribution-{uuid4().hex}",
    })
    assert published.status_code == 200, published.text
    result = published.json()
    assert result["status"] == "PUBLISHED"
    assert result["result_count"] == 4
    assert result["media_count"] == result["image_count"] == 1
    with connect() as conn:
        saved = conn.execute("SELECT manifest_json,payload_json FROM dashboard_captures WHERE id=?", [result["capture_id"]]).fetchone()
        assert saved is not None
        manifest, payload = saved
        assert len(json.loads(manifest) if isinstance(manifest, str) else manifest) == 2
        decoded_payload = json.loads(payload) if isinstance(payload, str) else payload
        scenes = [scene for run in decoded_payload["runs"] for scene in run["scenes"]]
        assert len(scenes) == 1
        assert len(scenes[0]["observations"]) == 4
        assert len(scenes[0]["media"]) == 1
        assert conn.execute("SELECT count(*) FROM dashboard_assets WHERE capture_id=?", [result["capture_id"]]).fetchone()[0] == 2
    assert (root / prepared["result_relative_path"] / csv_path).read_bytes() == csv_bytes
    assert (root / prepared["result_relative_path"] / image_path).read_bytes() == image_bytes


def test_postgres_same_case_different_drafts_publish_serially(registration_client):
    with connect() as conn:
        if getattr(conn, "backend", None) != "postgresql":
            pytest.skip("PostgreSQL advisory transaction locking is verified only on isolated PostgreSQL.")
    client, _, project_id, request_id, request_folder, _ = registration_client
    case_relative, prepared = _prepare(client, request_folder, project_id, request_id)
    draft_values = []
    for value in (1.18, 2.18):
        content = json.dumps({"Set Tilt Angle @ Settle (deg)": value}).encode()
        draft_id, inspection = _create_draft(client, project_id, request_id, (case_relative, prepared), content)
        approved = client.post(BASE + f"/drafts/{draft_id}/approve", json={
            "inspection_revision": inspection["inspection_revision"], "acknowledge_partial": True,
        })
        assert approved.status_code == 200, approved.text
        draft_values.append((draft_id, inspection["inspection_revision"]))

    authorization = client.headers["Authorization"]

    def publish(item):
        draft_id, inspection_revision = item
        with TestClient(app) as concurrent_client:
            concurrent_client.headers["Authorization"] = authorization
            response = concurrent_client.post(BASE + f"/drafts/{draft_id}/publish", json={
                "inspection_revision": inspection_revision, "idempotency_key": f"parallel-{uuid4().hex}",
            })
            return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(publish, draft_values))
    assert all(status == 200 for status, _ in outcomes), outcomes
    results = [value for _, value in outcomes]
    assert {item["status"] for item in results} == {"PUBLISHED", "MIRROR_CONFLICT"}
    assert len({item["case_id"] for item in results}) == 1
    assert len({item["capture_id"] for item in results}) == 2
    case_id = results[0]["case_id"]
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM dashboard_captures WHERE case_id=?", [case_id]).fetchone()[0] == 2


def test_exclusions_are_inspected_frozen_and_required_again_for_approval(registration_client):
    client, _, project_id, request_id, request_folder, _ = registration_client
    case_relative, prepared = _prepare(client, request_folder, project_id, request_id)
    result_folder = prepared["result_relative_path"]
    files = [
        ("model_settle_result.json", json.dumps({"Set Tilt Angle @ Settle (deg)": 1.18}).encode(), "application/json"),
        ("other_settle_result.json", json.dumps({"Set Tilt Angle @ Settle (deg)": 9.99}).encode(), "application/json"),
    ]
    created = client.post(BASE + "/drafts", json={
        "project_id": project_id, "request_id": request_id, "environment": "USAGE",
        "case_relative_path": case_relative, "result_relative_path": result_folder,
        "context": prepared["context"],
        "files": [
            {"relative_path": path, "size": len(content), "media_type": media_type}
            for path, content, media_type in files
        ] + [{"relative_path": "missing_wobble_center_front_result.json", "size": 10, "media_type": "application/json"}],
    })
    assert created.status_code == 201, created.text
    draft_id = created.json()["draft_id"]
    uploaded = client.post(BASE + f"/drafts/{draft_id}/files",
        data={"relative_paths": [item[0] for item in files]},
        files=[("files", (path, content, media_type)) for path, content, media_type in files])
    assert uploaded.status_code == 200, uploaded.text

    exclusion = {"relative_path": "other_settle_result.json", "reason": "이 파일은 중복 원본입니다."}
    inspected = client.post(BASE + f"/drafts/{draft_id}/inspect", json={"exclusions": [exclusion]})
    assert inspected.status_code == 200, inspected.text
    inspection = inspected.json()
    assert inspection["exclusions"] == [exclusion]
    statuses = {item["relative_path"]: item["inspection_status"] for item in inspection["manifest"]}
    assert statuses == {
        "model_settle_result.json": "READY",
        "other_settle_result.json": "EXCLUDED",
        "missing_wobble_center_front_result.json": "PENDING",
    }
    assert inspection["result_count"] == 1
    assert any(item["status"] == "READY" and item["value"] == 1.18 for item in inspection["metrics"])
    assert not any(item.get("value") == 9.99 for item in inspection["metrics"])
    restored = client.get(BASE + f"/drafts/{draft_id}")
    assert restored.status_code == 200
    assert restored.json()["exclusions"] == [exclusion]
    assert restored.json()["inspection"]["result_count"] == 1

    mismatch = client.post(BASE + f"/drafts/{draft_id}/approve", json={
        "inspection_revision": inspection["inspection_revision"], "acknowledge_partial": True, "exclusions": [],
    })
    assert mismatch.status_code == 409, mismatch.text
    assert mismatch.json()["detail"]["code"] == "RESULT_EXCLUSIONS_STALE"
    approved = client.post(BASE + f"/drafts/{draft_id}/approve", json={
        "inspection_revision": inspection["inspection_revision"], "acknowledge_partial": True,
        "exclusions": [exclusion],
    })
    assert approved.status_code == 200, approved.text
    assert approved.json()["result_count"] == 1
    with connect() as conn:
        raw = conn.execute("SELECT approval_json FROM result_registration_drafts WHERE id=?", [draft_id]).fetchone()[0]
        approval = json.loads(raw) if isinstance(raw, str) else raw
        assert approval["exclusions"] == [exclusion]
        assert approval["counts"]["result_count"] == 1
        assert approval["inspection"]["result_count"] == 1

    key = f"exclude-{uuid4().hex}"
    published = client.post(BASE + f"/drafts/{draft_id}/publish", json={
        "inspection_revision": inspection["inspection_revision"], "idempotency_key": key,
    })
    assert published.status_code == 200, published.text
    assert published.json()["result_count"] == 1
    final_read = client.get(BASE + f"/drafts/{draft_id}")
    assert final_read.status_code == 200
    assert final_read.json()["exclusions"] == [exclusion]
    assert final_read.json()["result_count"] == published.json()["result_count"] == 1


def test_distribution_top_level_unsupported_issue_blocks_inspection_approval(registration_client):
    client, _, project_id, request_id, request_folder, _ = registration_client
    prepared = _prepare_distribution(client, request_folder, project_id, request_id)
    content = b'{"unsupported": true}'
    relative = "unsupported_result.json"
    created = client.post(BASE + "/drafts", json={
        "project_id": project_id, "request_id": request_id, "environment": "DISTRIBUTION",
        "case_relative_path": prepared["case_relative_path"],
        "result_relative_path": prepared["result_relative_path"], "context": prepared["context"],
        "files": [{"relative_path": relative, "size": len(content), "media_type": "application/json"}],
    })
    assert created.status_code == 201, created.text
    draft_id = created.json()["draft_id"]
    uploaded = client.post(BASE + f"/drafts/{draft_id}/files", data={"relative_paths": relative},
                           files=[("files", (relative, content, "application/json"))])
    assert uploaded.status_code == 200, uploaded.text
    inspected = client.post(BASE + f"/drafts/{draft_id}/inspect")
    assert inspected.status_code == 200, inspected.text
    inspection = inspected.json()
    assert inspection["blocking_count"] > 0
    assert any("UNPROCESSED_FILE" in issue["code"] and issue["severity"] == "ERROR" for issue in inspection["issues"])
    assert inspection["manifest"][0]["inspection_status"] == "UNSUPPORTED"
    approved = client.post(BASE + f"/drafts/{draft_id}/approve", json={
        "inspection_revision": inspection["inspection_revision"], "acknowledge_partial": True,
    })
    assert approved.status_code == 409, approved.text
    assert approved.json()["detail"]["code"] == "RESULT_INSPECTION_BLOCKING"


def test_manifest_marks_invalid_uploaded_source_as_error_and_unuploaded_as_pending(registration_client):
    client, _, project_id, request_id, request_folder, _ = registration_client
    case_relative, prepared = _prepare(client, request_folder, project_id, request_id)
    relative = "model_settle_result.json"
    content = json.dumps({"Set Tilt Angle @ Settle (deg)": "not numeric"}).encode()
    pending_path = "missing_wobble_center_front_result.json"
    created = client.post(BASE + "/drafts", json={
        "project_id": project_id, "request_id": request_id, "environment": "USAGE",
        "case_relative_path": case_relative, "result_relative_path": prepared["result_relative_path"],
        "context": prepared["context"],
        "files": [
            {"relative_path": relative, "size": len(content), "media_type": "application/json"},
            {"relative_path": pending_path, "size": 12, "media_type": "application/json"},
        ],
    })
    assert created.status_code == 201, created.text
    draft_id = created.json()["draft_id"]
    uploaded = client.post(BASE + f"/drafts/{draft_id}/files", data={"relative_paths": relative},
                           files=[("files", (relative, content, "application/json"))])
    assert uploaded.status_code == 200, uploaded.text
    inspected = client.post(BASE + f"/drafts/{draft_id}/inspect")
    assert inspected.status_code == 200, inspected.text
    statuses = {item["relative_path"]: item["inspection_status"] for item in inspected.json()["manifest"]}
    assert statuses == {relative: "ERROR", pending_path: "PENDING"}
    assert inspected.json()["blocking_count"] > 0


def test_empty_usage_upload_cannot_be_approved_but_video_only_partial_can(registration_client):
    client, _, project_id, request_id, request_folder, _ = registration_client
    case_relative, prepared = _prepare(client, request_folder, project_id, request_id)

    def create_uploaded(relative: str, content: bytes, media_type: str):
        created = client.post(BASE + "/drafts", json={
            "project_id": project_id, "request_id": request_id, "environment": "USAGE",
            "case_relative_path": case_relative, "result_relative_path": prepared["result_relative_path"],
            "context": prepared["context"],
            "files": [{"relative_path": relative, "size": len(content), "media_type": media_type}],
        })
        assert created.status_code == 201, created.text
        draft_id = created.json()["draft_id"]
        uploaded = client.post(BASE + f"/drafts/{draft_id}/files", data={"relative_paths": relative},
                               files=[("files", (relative, content, media_type))])
        assert uploaded.status_code == 200, uploaded.text
        inspected = client.post(BASE + f"/drafts/{draft_id}/inspect")
        assert inspected.status_code == 200, inspected.text
        return draft_id, inspected.json()

    empty_id, empty = create_uploaded("unmapped.json", b'{"not": "a result"}', "application/json")
    assert empty["result_count"] == 0
    assert empty["connected_media_count"] == 0
    blocked = client.post(BASE + f"/drafts/{empty_id}/approve", json={
        "inspection_revision": empty["inspection_revision"], "acknowledge_partial": True,
    })
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["detail"]["code"] == "RESULT_APPROVAL_EMPTY"

    video_id, video = create_uploaded("walkthrough.mp4", b"synthetic-video-bytes", "video/mp4")
    assert video["result_count"] == 0
    assert video["connected_media_count"] == 1
    approved = client.post(BASE + f"/drafts/{video_id}/approve", json={
        "inspection_revision": video["inspection_revision"], "acknowledge_partial": True,
    })
    assert approved.status_code == 200, approved.text
    published = client.post(BASE + f"/drafts/{video_id}/publish", json={
        "inspection_revision": video["inspection_revision"], "idempotency_key": f"video-{uuid4().hex}",
    })
    assert published.status_code == 200, published.text
    assert published.json()["result_count"] == 0
    assert published.json()["media_count"] == 1
