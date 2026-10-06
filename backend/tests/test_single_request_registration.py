"""Manual registration is one request (depth-schema.md §14, D18-D19), API time offsets (§14.3)
and the request result-environment endpoint (case-results-environment.md §2)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.database_connection import connect
from app.security import hash_password
from tests.test_depth_schema import (DIST, DIST_CASE, DIST_TAIL, ENV, PROJECT, USAGE, USAGE_CASE, USAGE_FILES,
                                     _post)
from tests.test_new_scene_registration import CSV, CSV_BYTES, admin_client  # noqa: F401

pytestmark = pytest.mark.duckdb_integration

SECOND = "75R9J_PR"
SECOND_USAGE = f"{SECOND}/[WR-0003]_[사용_환경]"
SECOND_DIST = f"{SECOND}/[WR-0003]_[유통_환경]"
# Extra requests in the first project so a project-level scan holds 2+ requests per environment.
EXTRA_USAGE = f"{PROJECT}/[WR-0005]_[사용_환경]"
EXTRA_DIST = f"{PROJECT}/[WR-0006]_[유통_환경]"
TABLES = ("projects", "analysis_requests", "folder_environment_registrations", "folder_environment_registry",
          "folder_environment_capture_jobs", "dashboard_cases", "dashboard_captures")
PASSWORD = "single-request-registration-password"


def _usage(root, request_path):
    case = root / request_path / "Working" / USAGE_CASE
    for scene, files in USAGE_FILES.items():
        (case / scene).mkdir(parents=True)
        for name, payload in files.items():
            (case / scene / name).write_text(json.dumps(payload), encoding="utf-8")


def _distribution(root, request_path):
    scene = root / request_path / "Working" / DIST_CASE / DIST_TAIL / "2_Face"
    scene.mkdir(parents=True)
    (scene / CSV).write_bytes(CSV_BYTES)


def _real_tree(root, *, extra=False):
    _usage(root, USAGE)
    _distribution(root, DIST)
    _usage(root, SECOND_USAGE)
    _distribution(root, SECOND_DIST)
    if extra:
        _usage(root, EXTRA_USAGE)
        _distribution(root, EXTRA_DIST)


def _counts():
    with connect() as conn:
        return {table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in TABLES}


def _scan_preview(client, environment, relative_path, **scan_extra):
    scan = _post(client, ENV + "/scan", {"environment": environment, "relative_path": relative_path, **scan_extra})
    preview = _post(client, ENV + "/previews", {"scan_id": scan["id"], "assignments": []})
    return scan, preview


def _register(client, preview_id):
    return client.post(ENV + "/registrations", json={"preview_id": preview_id, "idempotency_key": f"single-{uuid4()}",
                                                       "capture": True})


def _has_utc_offset(value):
    return isinstance(value, str) and (value.endswith("Z") or value.endswith("+00:00"))


# ---- §14.4-18: root and project scans with 2+ requests are blocked --------------------

@pytest.mark.parametrize(("environment", "relative_path", "expected"), [
    ("USAGE", "", [USAGE, EXTRA_USAGE, SECOND_USAGE]),
    ("DISTRIBUTION", "", [DIST, EXTRA_DIST, SECOND_DIST]),
    ("USAGE", PROJECT, [USAGE, EXTRA_USAGE]),
    ("DISTRIBUTION", PROJECT, [DIST, EXTRA_DIST]),
])
def test_multiple_requests_block_preview_and_direct_register_writes_nothing(admin_client, environment, relative_path,
                                                                            expected):
    client, root = admin_client
    _real_tree(root, extra=True)
    _scan, preview = _scan_preview(client, environment, relative_path)
    assert preview["can_apply"] is False
    assert preview["blocking_code"] == "MULTIPLE_REQUESTS"
    assert preview["message"] == "의뢰 폴더별로 조사하세요. 여러 의뢰는 자동 탐색이 의뢰별로 등록합니다."
    assert sorted(preview["request_paths"]) == sorted(expected)
    before = _counts()
    response = _register(client, preview["id"])
    assert response.status_code == 409 and response.json()["detail"]["code"] == "MULTIPLE_REQUESTS", response.text
    # Defensive check also holds for a preview whose can_apply was forced on.
    with connect() as conn:
        conn.execute("UPDATE folder_environment_previews SET can_apply=? WHERE id=?", [True, preview["id"]])
    forced = _register(client, preview["id"])
    assert forced.status_code == 409 and forced.json()["detail"]["code"] == "MULTIPLE_REQUESTS", forced.text
    assert _counts() == before


def test_register_without_request_row_uses_preview_code(admin_client):
    """Register's defensive check reports the same code as the preview (REQUEST_MISSING), writing nothing."""
    client, root = admin_client
    _real_tree(root)
    _scan, preview = _scan_preview(client, "USAGE", USAGE)
    with connect() as conn:
        stored = json.loads(conn.execute("SELECT rows_json FROM folder_environment_previews WHERE id=?", [preview["id"]]).fetchone()[0])
        stored["rows"] = [row for row in stored["rows"] if row.get("role_kind") != "REQUEST"]
        conn.execute("UPDATE folder_environment_previews SET rows_json=?, can_apply=? WHERE id=?",
                     [json.dumps(stored, ensure_ascii=False), True, preview["id"]])
    before = _counts()
    response = _register(client, preview["id"])
    assert response.status_code == 409 and response.json()["detail"]["code"] == "REQUEST_MISSING", response.text
    assert _counts() == before


# ---- §14.4-19: request-level scan derives the project ---------------------------------

@pytest.mark.parametrize(("environment", "request_path"), [("USAGE", USAGE), ("DISTRIBUTION", DIST)])
def test_request_level_scan_derives_project_and_completes_all_cases(admin_client, environment, request_path):
    client, root = admin_client
    _real_tree(root)
    _scan, preview = _scan_preview(client, environment, request_path)
    assert preview["can_apply"] is True, preview
    assert preview["request_paths"] == [request_path] and preview["blocking_code"] is None
    project_row = next(row for row in preview["rows"] if row["role_kind"] == "PROJECT")
    assert project_row["relative_path"] == PROJECT and project_row["name"] == PROJECT
    request_row = next(row for row in preview["rows"] if row["role_kind"] == "REQUEST")
    assert request_row["parent_context"] == project_row["target_id"]
    response = _register(client, preview["id"])
    assert response.status_code == 200, response.text
    registered = response.json()
    assert registered["project_id"] == project_row["target_id"]
    assert registered["capture_jobs"] and all(job["status"] == "COMPLETED" for job in registered["capture_jobs"]), registered
    with connect() as conn:
        project_id = conn.execute("SELECT project_id FROM analysis_requests WHERE id=?",
                                  [registered["request_id"]]).fetchone()[0]
        assert project_id == registered["project_id"]
        assert conn.execute("SELECT name FROM projects WHERE id=?", [project_id]).fetchone()[0] == PROJECT
        assert conn.execute("SELECT count(*) FROM analysis_requests WHERE project_id IS NULL").fetchone()[0] == 0
        environments = {row[0] for row in conn.execute(
            "SELECT environment FROM dashboard_cases WHERE request_id=?", [registered["request_id"]]).fetchall()}
    assert environments == {environment}


def test_request_level_scan_links_already_linked_project(admin_client):
    client, root = admin_client
    _real_tree(root)
    _scan, first_preview = _scan_preview(client, "USAGE", USAGE)
    first = _register(client, first_preview["id"])
    assert first.status_code == 200, first.text
    project_id = first.json()["project_id"]
    _scan, preview = _scan_preview(client, "DISTRIBUTION", DIST)
    assert preview["can_apply"] is True, preview
    project_row = next(row for row in preview["rows"] if row["role_kind"] == "PROJECT")
    assert project_row["target_mode"] == "LINK" and project_row["target_id"] == project_id
    projects_before = _counts()["projects"]
    second = _register(client, preview["id"])
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["project_id"] == project_id
    assert all(job["status"] == "COMPLETED" for job in body["capture_jobs"]), body
    assert _counts()["projects"] == projects_before
    with connect() as conn:
        targets = conn.execute("SELECT created_targets FROM folder_environment_registrations WHERE id=?",
                               [body["registration_id"]]).fetchone()[0]
        assert conn.execute("SELECT project_id FROM analysis_requests WHERE id=?",
                            [body["request_id"]]).fetchone()[0] == project_id
    targets = json.loads(targets) if isinstance(targets, str) else targets
    assert targets["project_ids"] == [] and targets["request_ids"] == [body["request_id"]]


def test_request_folder_at_wrong_depth_is_request_level_mismatch(admin_client):
    client, root = admin_client
    nested = f"{PROJECT}/archive/[WR-0007]_[사용_환경]"
    _usage(root, nested)
    _scan, preview = _scan_preview(client, "USAGE", nested)
    assert preview["can_apply"] is False
    assert preview["blocking_code"] == "DEPTH_SCHEMA_REQUEST_LEVEL_MISMATCH"
    before = _counts()
    response = _register(client, preview["id"])
    assert response.status_code == 409
    assert _counts() == before


# ---- §14.4-20: API times carry a UTC offset ----------------------------------------------

def test_folder_environment_times_carry_utc_offset(admin_client):
    client, root = admin_client
    _real_tree(root)
    scan, preview = _scan_preview(client, "USAGE", USAGE)
    assert _has_utc_offset(scan["created_at"]) and _has_utc_offset(preview["created_at"])
    registered = _register(client, preview["id"]).json()
    assert _has_utc_offset(registered["created_at"]) and registered["deleted_at"] is None
    with connect() as conn:
        stored = conn.execute("SELECT created_at FROM folder_environment_registrations WHERE id=?",
                              [registered["registration_id"]]).fetchone()[0]
    stored = stored if stored.tzinfo else stored.replace(tzinfo=timezone.utc)
    assert datetime.fromisoformat(registered["created_at"].replace("Z", "+00:00")) == stored
    status = client.get(f"{ENV}/registrations/{registered['registration_id']}").json()
    assert _has_utc_offset(status["created_at"])
    history = client.get(ENV + "/history").json()["items"]
    assert history and all(_has_utc_offset(item["created_at"]) for item in history)
    depth = client.get(ENV + "/depth-schema").json()
    assert _has_utc_offset(depth["created_at"])
    preview_delete = _post(client, ENV + "/registrations/delete-preview",
                           {"registration_ids": [registered["registration_id"]]})
    deleted = client.post(ENV + "/registrations/delete", json={
        "registration_ids": [registered["registration_id"]], "confirm_token": preview_delete["confirm_token"]})
    assert deleted.status_code == 200, deleted.text
    assert _has_utc_offset(deleted.json()["deleted_at"])
    tombstones = client.get(ENV + "/history", params={"include_deleted": True}).json()["items"]
    entry = next(item for item in tombstones if item["registration_id"] == registered["registration_id"])
    assert _has_utc_offset(entry["deleted_at"])


# ---- case-results-environment.md §2 / §4-7: result-environments ----------------------------

def _environments_url(project_id, request_id):
    return f"/api/projects/{project_id}/requests/{request_id}/result-environments"


def _create_user(project_id=None, role="general"):
    user_id = f"result-env-{uuid4().hex[:10]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute("""INSERT INTO users (id,username,password_hash,display_name,legacy_role,account_status,
            is_global_admin,is_active,created_at,updated_at) VALUES (?,?,?,?,'viewer','ACTIVE',false,true,?,?)""",
                     [user_id, user_id, hash_password(PASSWORD), user_id, now, now])
        if project_id:
            conn.execute("""INSERT INTO project_memberships (id,project_id,user_id,role,created_by,created_at,updated_by,
                updated_at) VALUES (?,?,?,?,?,?,?,?)""",
                         [f"membership-{uuid4().hex[:10]}", project_id, user_id, role, user_id, now, user_id, now])
    return user_id


def _headers(client, username):
    response = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_result_environments_counts_order_empty_and_not_found(admin_client):
    client, root = admin_client
    _real_tree(root)
    _scan, preview = _scan_preview(client, "USAGE", USAGE)
    registered = _register(client, preview["id"]).json()
    project_id, request_id = registered["project_id"], registered["request_id"]
    response = client.get(_environments_url(project_id, request_id))
    assert response.status_code == 200, response.text
    assert response.json() == {"environments": ["USAGE"], "case_counts": {"USAGE": 1, "DISTRIBUTION": 0}}
    # Legacy mixed request (E4): both environments, USAGE first.
    with connect() as conn:
        for index in range(2):
            conn.execute("INSERT INTO dashboard_cases(id,project_id,request_id,storage_root_id,relative_path,environment,"
                         "source_name,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                         [f"mixed-case-{uuid4().hex}", project_id, request_id, "synthetic-root", f"mixed/{index}",
                          "DISTRIBUTION", f"mixed-{index}", "{}", datetime.now(timezone.utc).replace(tzinfo=None)])
    mixed = client.get(_environments_url(project_id, request_id)).json()
    assert mixed == {"environments": ["USAGE", "DISTRIBUTION"], "case_counts": {"USAGE": 1, "DISTRIBUTION": 2}}
    # A request without Cases.
    empty_id = f"empty-request-{uuid4().hex[:8]}"
    with connect() as conn:
        conn.execute("INSERT INTO analysis_requests(id,project_id,title,status,owner,requested_at,due_at,overall_note) "
                     "VALUES(?,?,'empty','READY','test',CURRENT_TIMESTAMP,NULL,'')", [empty_id, project_id])
    empty = client.get(_environments_url(project_id, empty_id))
    assert empty.status_code == 200 and empty.json() == {"environments": [],
                                                        "case_counts": {"USAGE": 0, "DISTRIBUTION": 0}}
    assert client.get(_environments_url(project_id, "missing-request")).status_code == 404
    assert client.get(_environments_url("other-project", request_id)).status_code == 404


def test_result_environments_permission_matches_catalog(admin_client):
    client, root = admin_client
    _real_tree(root)
    _scan, preview = _scan_preview(client, "USAGE", USAGE)
    registered = _register(client, preview["id"]).json()
    project_id, request_id = registered["project_id"], registered["request_id"]
    url = _environments_url(project_id, request_id)
    catalog = ("/api/dashboard/catalog", {"request_id": request_id, "environment": "USAGE"})
    member = _headers(client, _create_user(project_id))
    outsider = _headers(client, _create_user())
    for headers in (member, outsider):
        expected = client.get(catalog[0], params=catalog[1], headers=headers).status_code
        assert client.get(url, headers=headers).status_code == expected
    assert client.get(url, headers=member).json()["environments"] == ["USAGE"]
    # Unknown request: the shared permission check answers 404 before any count.
    assert client.get(_environments_url(project_id, "missing-request"), headers=member).status_code == 404
    assert client.get(url, headers={"Authorization": "Bearer invalid"}).status_code == 401
