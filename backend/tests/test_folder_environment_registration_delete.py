"""Depth-schema endpoints (§6) and administrator registration delete (§13).

Runs on DuckDB by default; on PostgreSQL when ANALYSIS_TEST_POSTGRES=1 and
ANALYSIS_DB_BACKEND=postgresql (the conftest isolated_database fixture then
targets the explicitly configured test database).
"""
from __future__ import annotations

import ast
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database_connection import connect
from app.main import app
from app.security import hash_password
from app.services import environment_folder_profiles as profiles
from tests.test_depth_schema import (DIST, DIST_CASE, DIST_TAIL, PROJECT, _build_distribution, _build_usage,
                                     _post, _register)
from tests.test_new_scene_registration import admin_client  # noqa: F401

# Unmarked tests are classified duckdb_integration by conftest; with ANALYSIS_TEST_POSTGRES=1 the same
# tests run against the explicitly configured PostgreSQL test database.

ENV = "/api/folder-discovery/environments"


@pytest.fixture(autouse=True)
def _restore_default_depth_schema(request):
    """PostgreSQL test runs share one database: put the default schema back after a test changed it."""
    yield
    if request.node.get_closest_marker("unit") is not None:
        return
    from app.services.folder_discovery import WRITE_LOCK
    from app.services.semantic_mapping import semantic_transaction
    with connect() as conn:
        current = profiles.get_depth_schema(conn)
        lowers = {env: item["lower"] for env, item in current["environments"].items()}
        if current["upper"] == profiles.DEFAULT_UPPER and lowers == profiles.DEFAULT_LOWER:
            return
        environments = {env: {"lower": profiles.DEFAULT_LOWER[env], "usage_sources": item["usage_sources"]}
                        for env, item in current["environments"].items()}
        with WRITE_LOCK, semantic_transaction(conn):
            profiles.save_depth_schema(conn, "test-restore", current["schema_set_id"], profiles.DEFAULT_UPPER, environments)


def _json(value):
    return json.loads(value) if isinstance(value, (str, bytes)) else value


def _tree_state(root: Path) -> dict[str, tuple[int, int]]:
    return {str(path.relative_to(root)): (path.stat().st_mtime_ns, path.stat().st_size if path.is_file() else -1)
            for path in sorted(root.rglob("*"))}


def _preview(client, ids, status=200):
    return _post(client, ENV + "/registrations/delete-preview", {"registration_ids": ids}, status)


def _delete(client, ids, token):
    return client.post(ENV + "/registrations/delete", json={"registration_ids": ids, "confirm_token": token})


def _count(conn, table, column, value):
    return conn.execute(f"SELECT count(*) FROM {table} WHERE {column}=?", [value]).fetchone()[0]


def _created_targets(registration_id):
    with connect() as conn:
        raw = conn.execute("SELECT created_targets FROM folder_environment_registrations WHERE id=?", [registration_id]).fetchone()[0]
    return _json(raw) if raw else None


# ---- §13.8-11: delete removes owned data, keeps tombstone, never touches SPDM --------

def test_delete_removes_owned_business_data_and_keeps_spdm_tree(admin_client):
    client, root = admin_client
    _build_distribution(root)
    _scan, _preview_body, registered = _register(client, "DISTRIBUTION")
    rid, project_id, request_id = registered["registration_id"], registered["project_id"], registered["request_id"]
    job = registered["capture_jobs"][0]
    targets = _created_targets(rid)
    assert targets["project_ids"] == [project_id] and targets["request_ids"] == [request_id]
    assert targets["case_ids"] == [job["case_id"]]
    before = _tree_state(root)

    preview = _preview(client, [rid])
    item = preview["items"][0]
    assert item["deletable"] and item["blockers"] == []
    assert {k: item["counts"][k] for k in ("projects", "requests", "cases", "captures")} == {
        "projects": 1, "requests": 1, "cases": 1, "captures": 1}
    assert item["counts"]["assets"] >= 1 and item["counts"]["finalizations"] == 0

    response = _delete(client, [rid], preview["confirm_token"])
    assert response.status_code == 200, response.text
    assert response.json()["deleted"] == [rid]
    with connect() as conn:
        assert _count(conn, "projects", "id", project_id) == 0
        assert _count(conn, "analysis_requests", "id", request_id) == 0
        assert _count(conn, "dashboard_cases", "id", job["case_id"]) == 0
        assert _count(conn, "dashboard_captures", "id", job["capture_id"]) == 0
        assert _count(conn, "dashboard_assets", "capture_id", job["capture_id"]) == 0
        assert _count(conn, "folder_environment_registry", "registration_id", rid) == 0
        assert _count(conn, "folder_environment_capture_jobs", "registration_id", rid) == 0
        assert _count(conn, "product_information", "project_id", project_id) == 0
        assert _count(conn, "folder_environment_scans", "request_id", request_id) == 0
        status, deleted_at, deleted_by = conn.execute(
            "SELECT status,deleted_at,deleted_by FROM folder_environment_registrations WHERE id=?", [rid]).fetchone()
        audit = conn.execute("SELECT detail_json FROM audit_events WHERE action='FOLDER_ENVIRONMENT_REGISTRATION_DELETED'").fetchall()
    assert status == "DELETED" and deleted_at is not None and deleted_by
    assert any(rid in _json(row[0]).get("registration_ids", []) for row in audit)
    assert _tree_state(root) == before  # D14: no SPDM file or folder changed

    # History hides tombstones by default; registration() keeps DELETED.
    hidden = client.get(ENV + "/history").json()
    assert rid not in {entry["registration_id"] for entry in hidden["items"]}
    shown = client.get(ENV + "/history", params={"include_deleted": True}).json()
    entry = next(entry for entry in shown["items"] if entry["registration_id"] == rid)
    assert entry["status"] == "DELETED" and entry["deleted_at"]

    # Idempotent: an already DELETED registration is skipped.
    again = _preview(client, [rid])
    repeat = _delete(client, [rid], again["confirm_token"])
    assert repeat.status_code == 200 and repeat.json()["deleted"] == []


def test_legacy_registration_without_created_targets_uses_inference(admin_client):
    client, root = admin_client
    _build_usage(root)
    _scan, _preview_body, registered = _register(client, "USAGE")
    rid = registered["registration_id"]
    with connect() as conn:  # an old-schema registration has no created_targets
        conn.execute("UPDATE folder_environment_registrations SET created_targets=NULL WHERE id=?", [rid])
    preview = _preview(client, [rid])
    assert preview["items"][0]["counts"]["projects"] == 1 and preview["items"][0]["counts"]["requests"] == 1
    assert _delete(client, [rid], preview["confirm_token"]).status_code == 200
    with connect() as conn:
        assert _count(conn, "projects", "id", registered["project_id"]) == 0


# ---- §13.8-12: a LINKed pre-existing project survives ------------------------------

def test_linked_existing_project_is_preserved(admin_client):
    client, root = admin_client
    _build_distribution(root, final=False)
    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=3)
    existing_project = f"project-existing-{uuid4().hex[:8]}"
    with connect() as conn:
        conn.execute("INSERT INTO projects(id,name,product_name,description,created_at) VALUES(?,?,?,?,?)",
                     [existing_project, "기존 프로젝트", "기존", "", old])
    scan = _post(client, ENV + "/scan", {"environment": "DISTRIBUTION", "relative_path": ""})
    project_node = next(node for node in scan["nodes"] if node["relative_path"] == PROJECT)
    preview = _post(client, ENV + "/previews", {"scan_id": scan["id"], "assignments": [
        {"node_id": project_node["id"], "role_kind": "PROJECT", "confirm": True, "target_mode": "LINK",
         "target_id": existing_project}]})
    assert preview["can_apply"], preview
    registered = _post(client, ENV + "/registrations", {"preview_id": preview["id"], "idempotency_key": f"t-{uuid4()}",
                                                        "capture": True})
    rid, request_id = registered["registration_id"], registered["request_id"]
    assert registered["project_id"] == existing_project
    assert _created_targets(rid)["project_ids"] == []
    delete_preview = _preview(client, [rid])
    assert delete_preview["items"][0]["counts"]["projects"] == 0
    assert delete_preview["items"][0]["counts"]["requests"] == 1
    assert _delete(client, [rid], delete_preview["confirm_token"]).status_code == 200
    with connect() as conn:
        assert _count(conn, "projects", "id", existing_project) == 1
        assert _count(conn, "analysis_requests", "id", request_id) == 0
        assert _count(conn, "dashboard_cases", "request_id", request_id) == 0
    # The same rule holds for old registrations inferred by created_at.
    with connect() as conn:
        conn.execute("DELETE FROM projects WHERE id=?", [existing_project])


# ---- §13.8-13: a Case shared with another live registration blocks everything -------

def test_case_shared_with_live_registration_blocks_without_changes(admin_client):
    client, root = admin_client
    _build_distribution(root, final=False)
    _scan, _p, first = _register(client, "DISTRIBUTION")
    _scan2, _p2, second = _register(client, "DISTRIBUTION")  # same request folder, same Case id
    assert first["capture_jobs"][0]["case_id"] == second["capture_jobs"][0]["case_id"]
    rid = first["registration_id"]
    preview = _preview(client, [rid])
    assert preview["items"][0]["deletable"] is False
    assert {b["reason"] for b in preview["items"][0]["blockers"]} >= {"CASE_SHARED_WITH_LIVE_REGISTRATION"}

    def snapshot():
        with connect() as conn:
            return {table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                    for table in ("projects", "analysis_requests", "dashboard_cases", "dashboard_captures",
                                  "dashboard_assets", "folder_environment_registry", "folder_environment_capture_jobs")}

    before = snapshot()
    blocked = _delete(client, [rid], preview["confirm_token"])
    assert blocked.status_code == 409, blocked.text
    detail = blocked.json()["detail"]
    assert detail["code"] == "REGISTRATION_DELETE_BLOCKED" and detail["items"][0]["blockers"]
    assert snapshot() == before
    with connect() as conn:
        assert conn.execute("SELECT status FROM folder_environment_registrations WHERE id=?", [rid]).fetchone()[0] != "DELETED"
    # Deleting both registrations together is atomic and allowed.
    both = _preview(client, [rid, second["registration_id"]])
    assert all(item["deletable"] for item in both["items"]), both
    done = _delete(client, [rid, second["registration_id"]], both["confirm_token"])
    assert done.status_code == 200, done.text
    assert sorted(done.json()["deleted"]) == sorted([rid, second["registration_id"]])


def test_independent_request_data_blocks_delete(admin_client):
    client, root = admin_client
    _build_distribution(root, final=False)
    _scan, _p, registered = _register(client, "DISTRIBUTION")
    with connect() as conn:
        conn.execute("INSERT INTO load_cases(id,request_id,name,analysis_type,status,parameters_json,created_at) "
                     "VALUES(?,?,?,?,?,?,?)", [f"lc-{uuid4().hex[:8]}", registered["request_id"], "manual", "DROP", "READY",
                                               "{}", datetime.now(timezone.utc).replace(tzinfo=None)])
    preview = _preview(client, [registered["registration_id"]])
    assert [b["reason"] for b in preview["items"][0]["blockers"]] == ["LOAD_CASE_DATA"]
    assert _delete(client, [registered["registration_id"]], preview["confirm_token"]).json()["detail"]["code"] == \
        "REGISTRATION_DELETE_BLOCKED"


# ---- §13.8-14: stale preview ----------------------------------------------------------

def test_registration_added_after_preview_makes_token_stale(admin_client):
    client, root = admin_client
    _build_distribution(root, final=False)
    _scan, _p, registered = _register(client, "DISTRIBUTION")
    rid = registered["registration_id"]
    preview = _preview(client, [rid])
    assert preview["items"][0]["deletable"]
    _register(client, "DISTRIBUTION")  # another registration of the same request appears
    stale = _delete(client, [rid], preview["confirm_token"])
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "DELETE_PREVIEW_STALE", stale.text
    with connect() as conn:
        assert conn.execute("SELECT status FROM folder_environment_registrations WHERE id=?", [rid]).fetchone()[0] != "DELETED"
    bad = _delete(client, [rid], "0" * 64)
    assert bad.status_code == 409 and bad.json()["detail"]["code"] == "DELETE_PREVIEW_STALE"


def test_delete_request_validation(admin_client):
    client, _root = admin_client
    assert client.post(ENV + "/registrations/delete-preview", json={"registration_ids": []}).status_code == 422
    assert client.post(ENV + "/registrations/delete-preview", json={"registration_ids": ["x"] * 201}).status_code == 422
    missing = client.post(ENV + "/registrations/delete-preview", json={"registration_ids": ["environment-registration-none"]})
    assert missing.status_code == 404


# ---- §13.8-16: non-admin -------------------------------------------------------------

@pytest.fixture
def viewer_client(admin_client):
    client, root = admin_client
    suffix = uuid4().hex[:8]
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute("INSERT INTO users (id, username, password_hash, display_name, legacy_role, account_status, "
                     "is_global_admin, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, 'viewer', 'ACTIVE', false, true, ?, ?)",
                     [f"viewer-{suffix}", f"viewer-{suffix}", hash_password("viewer-password"), "Viewer", now, now])
    with TestClient(app) as viewer:
        login = viewer.post("/api/auth/login", json={"username": f"viewer-{suffix}", "password": "viewer-password"})
        assert login.status_code == 200, login.text
        viewer.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        yield viewer, client, root
    with connect() as conn:
        if os.getenv("ANALYSIS_TEST_POSTGRES") != "1":
            conn.execute("DELETE FROM audit_events WHERE user_id=?", [f"viewer-{suffix}"])
        conn.execute("DELETE FROM users WHERE id=?", [f"viewer-{suffix}"])


def test_non_admin_is_forbidden_everywhere(viewer_client):
    viewer, _client, _root = viewer_client
    schema_body = {"upper": profiles.DEFAULT_UPPER, "environments": {}}
    calls = [
        ("get", ENV + "/depth-schema", None),
        ("put", ENV + "/depth-schema", {"expected_schema_set_id": "x", **schema_body}),
        ("post", ENV + "/depth-schema/samples", {"segment": "UPPER"}),
        ("post", ENV + "/depth-schema/check", schema_body),
        ("post", "/api/requests/request-any/reinterpret", {}),
        ("post", ENV + "/registrations/delete-preview", {"registration_ids": ["x"]}),
        ("post", ENV + "/registrations/delete", {"registration_ids": ["x"], "confirm_token": "t"}),
        ("get", ENV + "/history", None),
    ]
    for method, path, body in calls:
        response = getattr(viewer, method)(path, **({"json": body} if body is not None else {}))
        assert response.status_code == 403, (path, response.status_code, response.text)


# ---- §6 depth-schema endpoints ---------------------------------------------------------

def test_depth_schema_get_put_conflict_and_invalid(admin_client):
    client, _root = admin_client
    current = client.get(ENV + "/depth-schema")
    assert current.status_code == 200, current.text
    schema = current.json()
    assert set(schema["environments"]) == {"USAGE", "DISTRIBUTION"}
    assert schema["environments"]["DISTRIBUTION"]["environment_keyword"] == "유통"
    environments = {env: {"environment_keyword": item["environment_keyword"], "lower": item["lower"],
                          "usage_sources": item["usage_sources"]} for env, item in schema["environments"].items()}
    upper = {"levels": [{"level": 1, "role": "CONTAINER"}, {"level": 2, "role": "PROJECT"}, {"level": 3, "role": "REQUEST"}]}
    saved = client.put(ENV + "/depth-schema", json={"expected_schema_set_id": schema["schema_set_id"], "upper": upper,
                                                    "environments": environments})
    assert saved.status_code == 200, saved.text
    assert saved.json()["upper"] == upper and saved.json()["schema_set_id"] != schema["schema_set_id"]
    conflict = client.put(ENV + "/depth-schema", json={"expected_schema_set_id": schema["schema_set_id"], "upper": upper,
                                                       "environments": environments})
    assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "DEPTH_SCHEMA_CONFLICT"
    invalid = client.put(ENV + "/depth-schema", json={"expected_schema_set_id": saved.json()["schema_set_id"],
                                                      "upper": {"levels": []}, "environments": environments})
    assert invalid.status_code == 422 and invalid.json()["detail"]["code"] == "DEPTH_SCHEMA_INVALID"
    named = json.loads(json.dumps(environments))
    named["DISTRIBUTION"]["lower"]["levels"][2]["allowed_names"] = ["Drop"]
    assert client.post(ENV + "/depth-schema/check", json={"upper": upper, "environments": named}).status_code == 422
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM audit_events WHERE action='FOLDER_DEPTH_SCHEMA_SAVED'").fetchone()[0] >= 1


def test_depth_schema_samples_check_and_reinterpret_endpoints(admin_client):
    client, root = admin_client
    _build_distribution(root)
    other = root / PROJECT / "[WR-0003]_[유통_환경]" / "Working" / "Case" / "Drop" / "run" / "ALL" / "Scene"
    other.mkdir(parents=True)
    (root / PROJECT / "[WR-0004]_[유통_사용]" / "Working").mkdir(parents=True)
    before = _tree_state(root)
    samples = _post(client, ENV + "/depth-schema/samples", {"segment": "DISTRIBUTION"})
    assert {item["name"].casefold() for item in samples["run_option_names"]} == {"individual", "all"}
    assert samples["requests_sampled"] == 2
    upper_samples = _post(client, ENV + "/depth-schema/samples", {"segment": "UPPER"})
    assert upper_samples["levels"][0]["samples"] == [{"name": PROJECT, "count": 1}]
    assert client.post(ENV + "/depth-schema/samples", json={"segment": "OTHER"}).status_code == 422
    schema = client.get(ENV + "/depth-schema").json()
    draft = {env: {"lower": item["lower"]} for env, item in schema["environments"].items()}
    checked = _post(client, ENV + "/depth-schema/check", {"upper": schema["upper"], "environments": draft})
    assert checked["by_code"] == {"ENV_KEYWORD_BOTH": 1}
    assert _tree_state(root) == before  # read-only endpoints

    import shutil
    shutil.rmtree(root / PROJECT / "[WR-0003]_[유통_환경]")
    shutil.rmtree(root / PROJECT / "[WR-0004]_[유통_사용]")
    _scan, _p, registered = _register(client, "DISTRIBUTION")
    result = _post(client, f"/api/requests/{registered['request_id']}/reinterpret", {})
    assert result["registered"] is True and result["registration_id"], result
    (root / DIST / "Notes").mkdir()
    blocked = _post(client, f"/api/requests/{registered['request_id']}/reinterpret", {})
    assert blocked["registered"] is False and [d["code"] for d in blocked["deviations"]] == ["UNEXPECTED_REQUEST_CHILD"]
    missing = client.post("/api/requests/request-does-not-exist/reinterpret", json={})
    assert missing.status_code == 404 and missing.json()["detail"]["code"] == "REQUEST_NOT_FOUND"


def test_reinterpret_request_level_mismatch_is_422(admin_client):
    client, root = admin_client
    _build_distribution(root, final=False)
    _scan, _p, registered = _register(client, "DISTRIBUTION")
    schema = client.get(ENV + "/depth-schema").json()
    environments = {env: {"lower": item["lower"], "usage_sources": item["usage_sources"]}
                    for env, item in schema["environments"].items()}
    upper = {"levels": [{"level": 1, "role": "CONTAINER"}, {"level": 2, "role": "PROJECT"}, {"level": 3, "role": "REQUEST"}]}
    assert client.put(ENV + "/depth-schema", json={"expected_schema_set_id": schema["schema_set_id"], "upper": upper,
                                                   "environments": environments}).status_code == 200
    mismatch = client.post(f"/api/requests/{registered['request_id']}/reinterpret", json={})
    assert mismatch.status_code == 422 and mismatch.json()["detail"]["code"] == "DEPTH_SCHEMA_REQUEST_LEVEL_MISMATCH"


def test_deprecated_profile_endpoints_return_410_and_list_stays(admin_client):
    client, _root = admin_client
    for method, path in (("post", "/profiles"), ("put", "/profiles/any"), ("delete", "/profiles/any"),
                         ("post", "/profiles/from-legacy")):
        response = getattr(client, method)(ENV + path, **({"json": {}} if method != "delete" else {}))
        assert response.status_code == 410, (path, response.text)
        assert response.json()["detail"]["code"] == "DEPRECATED_USE_DEPTH_SCHEMA"
    listed = client.get(ENV)
    assert listed.status_code == 200 and listed.json()["items"]


# ---- D14 static guarantee ----------------------------------------------------------------

@pytest.mark.unit
def test_deletion_module_has_no_filesystem_access():
    source = (Path(__file__).parents[1] / "app/services/folder_environment_deletion.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported |= {(node.module or "").split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not imported & {"os", "shutil", "pathlib", "io", "tempfile", "glob"}
    called = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert "open" not in called
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not attributes & {"unlink", "rmdir", "rmtree", "remove", "rename", "write_bytes", "write_text", "mkdir"}
