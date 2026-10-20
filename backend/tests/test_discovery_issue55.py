"""Issue #55 (SCX drive, real server): project whitespace twins, projects without a registrable request,
[저장 폴더 새로고침] re-applying the current depth schema, and bulk "제외" from the rescan result.

Synthetic local roots and the in-memory fake drive only.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from app.database_connection import connect
from app.services import environment_folder_profiles as profiles
from app.services import folder_auto_discovery, project_cleanup, storage_rescan
from tests.test_depth_schema import _restore_default_depth_schema  # noqa: F401  (autouse)
from tests.test_drive_discovery_tree import (  # noqa: F401  (fixtures and helpers)
    ENV, _discovery as _drive_discovery, _request_tree, scx,
)
from tests.test_folder_auto_discovery import (  # noqa: F401  (fixtures and helpers)
    CONTAINER, RESCAN, _container_schema, _count, _discover, _distribution_request, _isolate_project_names,
    _no_interval, _rescan, inline_rescan, admin_client,
)

pytestmark = pytest.mark.duckdb_integration
EXCLUSIONS = "/api/folder-discovery/environments/exclusions"
DUPLICATE = "FOLDER_NAME_WHITESPACE_DUPLICATE"


def _tag() -> str:
    return uuid.uuid4().hex[:6]


def _links(project_id: str | None = None) -> list[tuple[str, str]]:
    sql, params = "SELECT relative_path,project_id FROM folder_project_links", []
    if project_id:
        sql, params = sql + " WHERE project_id=?", [project_id]
    with connect() as conn:
        return sorted((str(row[0]), str(row[1])) for row in conn.execute(sql, params).fetchall())


def _project_rows(root_prefix: str) -> list[tuple[str, str]]:
    """(relative_path, project_id) of every PROJECT registry row of live registrations below the prefix."""
    with connect() as conn:
        return sorted({(str(row[0]), str(row[1])) for row in conn.execute(
            "SELECT g.relative_path,g.target_id FROM folder_environment_registry g JOIN folder_environment_registrations r "
            "ON r.id=g.registration_id WHERE g.role_kind='PROJECT' AND r.status<>'DELETED'").fetchall()
            if str(row[0]).startswith(root_prefix)})


# --- 1. project whitespace twins are one project ------------------------------------------------------

def test_project_whitespace_twins_are_one_project_with_requests_of_both(admin_client):
    client, root = admin_client
    _container_schema()
    tag = _tag()
    spaced, joined = f"{CONTAINER}/PROJ-{tag}_R95J 개발 과제", f"{CONTAINER}/PROJ-{tag}_R95J개발과제"
    for request in ("WR-0000800_유통환경", "WR-0000804_유통환경"):
        _distribution_request(root, f"{joined}/{request}")
    for request in ("WR-0000801_유통환경", "WR-0000805_유통환경"):
        _distribution_request(root, f"{spaced}/{request}")
    projects_before = _count("SELECT count(*) FROM projects")

    result = _discover(client, force=True)
    assert result["needs_review"] == [], result["needs_review"]
    [project] = result["created_projects"]
    assert sorted(item["name"] for item in result["created_requests"]) == [
        "WR-0000800_유통환경", "WR-0000801_유통환경", "WR-0000804_유통환경", "WR-0000805_유통환경"]
    assert {item["project_id"] for item in result["created_requests"]} == {project["id"]}
    assert _count("SELECT count(*) FROM projects") == projects_before + 1
    # Every spelling is linked to the same project (registry rows of the registrations).
    assert _project_rows(f"{CONTAINER}/PROJ-{tag}") == sorted([(spaced, project["id"]), (joined, project["id"])])

    folder_auto_discovery.reset_for_tests()
    again = _discover(client, force=True)
    assert (again["created_projects"], again["created_requests"], again["needs_review"]) == ([], [], [])
    assert _count("SELECT count(*) FROM projects") == projects_before + 1


def test_project_twins_without_a_registered_request_are_linked_by_link_rows(admin_client):
    client, root = admin_client
    _container_schema()
    tag = _tag()
    spaced, joined = f"{CONTAINER}/PROJ-{tag}_가 나", f"{CONTAINER}/PROJ-{tag}_가나"
    (root / spaced / "WR-0000001_기타").mkdir(parents=True)
    (root / joined / "WR-0000002_기타").mkdir(parents=True)
    result = _discover(client, force=True)
    [project] = result["created_projects"]
    assert _links(project["id"]) == sorted([(spaced, project["id"]), (joined, project["id"])])
    assert {item["code"] for item in result["needs_review"]} == {"ENV_KEYWORD_NONE"}
    folder_auto_discovery.reset_for_tests()
    again = _discover(client, force=True)
    assert again["created_projects"] == [] and len(_links(project["id"])) == 2


def test_same_request_key_under_both_project_spellings_stays_ambiguous(admin_client):
    client, root = admin_client
    _container_schema()
    tag = _tag()
    spaced, joined = f"{CONTAINER}/PROJ-{tag}_가 나", f"{CONTAINER}/PROJ-{tag}_가나"
    _distribution_request(root, f"{spaced}/WR-0000900_유통환경")
    _distribution_request(root, f"{joined}/WR-0000900_유통환경")
    _distribution_request(root, f"{joined}/WR-0000901_유통환경")
    result = _discover(client, force=True)
    twins = {item["relative_path"] for item in result["needs_review"] if item["code"] == DUPLICATE}
    assert twins == {f"{spaced}/WR-0000900_유통환경", f"{joined}/WR-0000900_유통환경"}
    assert [item["name"] for item in result["created_requests"]] == ["WR-0000901_유통환경"]
    assert len(result["created_projects"]) == 1


def test_registered_project_gets_the_link_for_a_new_whitespace_twin(admin_client):
    client, root = admin_client
    _container_schema()
    tag = _tag()
    spaced, joined = f"{CONTAINER}/PROJ-{tag}_R95J 개발 과제", f"{CONTAINER}/PROJ-{tag}_R95J개발과제"
    _distribution_request(root, f"{spaced}/WR-0000800_유통환경")
    first = _discover(client, force=True)
    [project] = first["created_projects"]
    _distribution_request(root, f"{joined}/WR-0000801_유통환경")
    folder_auto_discovery.reset_for_tests()
    second = _discover(client, force=True)
    assert second["created_projects"] == [] and second["needs_review"] == [], second
    assert [item["project_id"] for item in second["created_requests"]] == [project["id"]]
    assert {project_id for _path, project_id in _project_rows(f"{CONTAINER}/PROJ-{tag}")} == {project["id"]}
    assert _count("SELECT count(*) FROM projects WHERE name LIKE ?", [f"PROJ-{tag}%"]) == 1


# --- 2. every project-level folder gets a project ---------------------------------------------------

def test_project_without_a_registrable_request_is_added_and_stays_stable(admin_client):
    client, root = admin_client
    _container_schema()
    name = f"PROJ-{_tag()}_testProject"
    (root / CONTAINER / name / "WR-00000004_내충격 강성강도" / "Working").mkdir(parents=True)
    (root / CONTAINER / name / "WR-00000005_유통환경").mkdir(parents=True)   # no Working: depth deviation
    result = _discover(client, force=True)
    [project] = result["created_projects"]
    assert project["name"] == name and result["created_requests"] == []
    assert {item["code"] for item in result["needs_review"]} == {"ENV_KEYWORD_NONE", "DEPTH_DEVIATION"}
    assert _count("SELECT count(*) FROM project_memberships WHERE project_id=?", [project["id"]]) == 0
    assert any(item["id"] == project["id"] for item in client.get("/api/projects").json())
    folder_auto_discovery.reset_for_tests()
    again = _discover(client, force=True)
    assert again["created_projects"] == [] and _count("SELECT count(*) FROM projects WHERE name=?", [name]) == 1
    # A request that becomes registrable later joins the same project.
    (root / CONTAINER / name / "WR-00000005_유통환경" / "Working").mkdir()
    folder_auto_discovery.reset_for_tests()
    later = _discover(client, force=True)
    assert [item["project_id"] for item in later["created_requests"]] == [project["id"]]


def test_unlinked_same_name_project_still_blocks_the_automatic_project(admin_client):
    client, root = admin_client
    _container_schema()
    name = f"PROJ-{_tag()}_exists"
    assert client.post("/api/projects", json={"name": name, "product_name": "TV"}).status_code == 201
    before = _count("SELECT count(*) FROM projects")
    (root / CONTAINER / name / "WR-00000004_내충격 강성강도").mkdir(parents=True)
    result = _discover(client, force=True)
    assert result["created_projects"] == []
    assert [(item["relative_path"], item["code"]) for item in result["needs_review"]] == [
        (f"{CONTAINER}/{name}", "PROJECT_NAME_EXISTS")]
    assert _count("SELECT count(*) FROM projects") == before


def test_cleanup_lists_a_request_less_project_as_empty_and_rediscovery_recreates_it(admin_client):
    client, root = admin_client
    _container_schema()
    name = f"PROJ-{_tag()}_testProject"
    (root / CONTAINER / name / "WR-00000004_내충격 강성강도").mkdir(parents=True)
    [project] = _discover(client, force=True)["created_projects"]
    with connect() as conn:
        item = next(entry for entry in project_cleanup.candidates(conn)["items"] if entry["project_id"] == project["id"])
        assert (item["category"], item["selectable"]) == ("EMPTY", True)
        plan = project_cleanup.preview(conn, [project["id"]])
        project_cleanup.delete(conn, [project["id"]], plan["confirm_token"], None, actor="admin")
    assert _links(project["id"]) == [] and _count("SELECT count(*) FROM projects WHERE id=?", [project["id"]]) == 0
    folder_auto_discovery.reset_for_tests()
    again = _discover(client, force=True)   # the folder still exists: the project is added again (expected)
    assert [entry["name"] for entry in again["created_projects"]] == [name]


# --- 3. the rescan re-applies the current depth schema -------------------------------------------------

def _resave_schema(**lower):
    """A new depth schema set (same structure unless ``lower`` changes an environment's lower section)."""
    with connect() as conn:
        current = profiles.get_depth_schema(conn)
        environments = {env: {"lower": item["lower"], "usage_sources": item["usage_sources"]}
                        for env, item in current["environments"].items()}
        for env, value in lower.items():
            environments[env]["lower"] = value
        return profiles.save_depth_schema(conn, "admin", current["schema_set_id"], current["upper"], environments)


def test_rescan_reinterprets_requests_registered_with_an_older_schema(admin_client, inline_rescan):
    client, root = admin_client
    _container_schema()
    request = f"{CONTAINER}/PROJ-{_tag()}_가/WR-0000700_유통환경"
    _distribution_request(root, request)
    first = _rescan(client)
    [created] = first["created_requests"]
    assert first["reinterpreted"] == [] and first["reinterpret_blocked"] == [] and first["reinterpret_unchanged"] == 1
    registrations = _count("SELECT count(*) FROM folder_environment_registrations WHERE request_id=?", [created["id"]])

    _resave_schema()                                       # a new schema set id; D9: nothing changes by itself
    assert _count("SELECT count(*) FROM folder_environment_registrations WHERE request_id=?", [created["id"]]) == registrations
    second = _rescan(client)
    [done] = second["reinterpreted"]
    assert (done["request_id"], done["request_name"], done["environment"]) == (
        created["id"], "WR-0000700_유통환경", "DISTRIBUTION") and done["registration_id"]
    assert second["reinterpret_blocked"] == [] and second["reinterpret_unchanged"] == 0
    assert second["phase"] == "SYNC" and second["failed"] == []
    assert _count("SELECT count(*) FROM folder_environment_registrations WHERE request_id=?", [created["id"]]) == registrations + 1
    third = _rescan(client)                                # already on the current schema
    assert third["reinterpreted"] == [] and third["reinterpret_unchanged"] == 1
    assert _count("SELECT count(*) FROM folder_environment_registrations WHERE request_id=?", [created["id"]]) == registrations + 1


def test_rescan_reports_a_blocked_reinterpretation_and_changes_nothing(admin_client, inline_rescan):
    client, root = admin_client
    _container_schema()
    request = f"{CONTAINER}/PROJ-{_tag()}_나/WR-0000710_유통환경"
    _distribution_request(root, request)
    [created] = _rescan(client)["created_requests"]
    _resave_schema()
    (root / request / "Notes").mkdir()                     # UNEXPECTED_REQUEST_CHILD (blocking)
    registrations = _count("SELECT count(*) FROM folder_environment_registrations WHERE request_id=?", [created["id"]])
    result = _rescan(client)
    assert result["reinterpreted"] == []
    [blocked] = result["reinterpret_blocked"]
    assert blocked["request_id"] == created["id"] and blocked["code"] == "UNEXPECTED_REQUEST_CHILD"
    assert blocked["deviation_count"] == 1 and blocked["deviations"][0]["relative_path"].endswith("/Notes")
    assert _count("SELECT count(*) FROM folder_environment_registrations WHERE request_id=?", [created["id"]]) == registrations
    (root / request / "Notes").rmdir()                      # fixed: the next rescan applies the schema
    fixed = _rescan(client)
    assert [item["request_id"] for item in fixed["reinterpreted"]] == [created["id"]] and fixed["reinterpret_blocked"] == []


def test_rescan_reinterpret_phase_stops_at_its_time_budget(admin_client, inline_rescan, monkeypatch):
    client, root = admin_client
    _container_schema()
    _distribution_request(root, f"{CONTAINER}/PROJ-{_tag()}_다/WR-0000720_유통환경")
    _rescan(client)
    _resave_schema()
    monkeypatch.setattr(storage_rescan, "REINTERPRET_BUDGET_SECONDS", -1.0)
    result = _rescan(client)
    assert result["reinterpreted"] == [] and result["reinterpret_deferred"] == 1 and result["partial"] is True


# --- 4. bulk "제외" from the rescan result --------------------------------------------------------------

def test_excluded_folders_move_to_admin_excluded_and_can_be_restored(admin_client, inline_rescan):
    client, root = admin_client
    _container_schema()
    project = f"{CONTAINER}/PROJ-{_tag()}_testProject"
    none_a, none_b = f"{project}/WR-00000004_내충격 강성강도", f"{project}/WR-00000006_기타"
    for path in (none_a, none_b):
        (root / path / "Working").mkdir(parents=True)
    registered = f"{project}/WR-00000007_유통환경"
    _distribution_request(root, registered)
    state = _rescan(client)
    assert {item["relative_path"]: item["code"] for item in state["needs_review"]} == {
        none_a: "ENV_KEYWORD_NONE", none_b: "ENV_KEYWORD_NONE"}

    response = client.post(EXCLUSIONS, json={"relative_paths": [none_a, none_b, registered, "Other/Elsewhere/X", project]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert sorted(body["excluded"]) == sorted([none_a, none_b])
    assert {item["relative_path"]: item["code"] for item in body["skipped"]} == {
        registered: "NOT_LISTED", "Other/Elsewhere/X": "NOT_LISTED", project: "NOT_REQUEST_FOLDER"}
    # The rescan state shows them as excluded at once (restorable) ...
    shown = client.get(RESCAN).json()["needs_review"]
    assert {item["code"] for item in shown} == {"ADMIN_EXCLUDED"} and all(item["restorable"] for item in shown)
    assert _count("SELECT count(*) FROM audit_events WHERE action='FOLDER_DISCOVERY_EXCLUDED'") == 1
    # ... and every later rescan or discovery lists them as ADMIN_EXCLUDED, never as ENV_KEYWORD_NONE.
    again = _rescan(client)
    assert {item["relative_path"]: (item["code"], item["restorable"]) for item in again["needs_review"]} == {
        none_a: ("ADMIN_EXCLUDED", True), none_b: ("ADMIN_EXCLUDED", True)}
    discovered = _discover(client, force=True)
    assert {item["code"] for item in discovered["needs_review"]} == {"ADMIN_EXCLUDED"}

    restored = client.post(EXCLUSIONS + "/restore", json={"relative_paths": [none_a, registered]})
    assert restored.status_code == 200, restored.text
    assert restored.json()["restored"] == [none_a]
    assert [item["code"] for item in restored.json()["skipped"]] == ["NOT_RESTORABLE"]
    after = _rescan(client)
    assert {item["relative_path"]: item["code"] for item in after["needs_review"]} == {
        none_a: "ENV_KEYWORD_NONE", none_b: "ADMIN_EXCLUDED"}


def test_exclusion_is_refused_for_ambiguous_folders_and_needs_a_global_administrator(admin_client, inline_rescan, monkeypatch):
    client, root = admin_client
    _container_schema()
    project = f"{CONTAINER}/PROJ-{_tag()}_testProject"
    twins = [f"{project}/WR-0000900_유통 환경", f"{project}/WR-0000900_유통환경"]
    for path in twins:
        (root / path / "Working").mkdir(parents=True)
    _rescan(client)
    body = client.post(EXCLUSIONS, json={"relative_paths": twins}).json()
    assert body["excluded"] == [] and {item["code"] for item in body["skipped"]} == {"AMBIGUOUS_FOLDER"}

    from app.routers import folder_discovery_environment as router

    def denied(request, permission, project_id=None, **kwargs):
        raise HTTPException(403, detail={"code": "PERMISSION_DENIED"})

    monkeypatch.setattr(router, "require_permission", denied)
    assert client.post(EXCLUSIONS, json={"relative_paths": twins}).status_code == 403
    assert client.post(EXCLUSIONS + "/restore", json={"relative_paths": twins}).status_code == 403


# --- SCX drive: twins and the rescan -----------------------------------------------------------------

def test_drive_rescan_merges_project_twins_and_adds_request_less_projects(scx):
    _container_schema()
    tag = _tag()
    spaced, joined = f"{CONTAINER}/PROJ-{tag}_R95J 개발 과제", f"{CONTAINER}/PROJ-{tag}_R95J개발과제"
    for request in ("WR-00000800_유통환경", "WR-00000804_유통환경"):
        _request_tree(scx, f"{joined}/{request}", scenes=("2_Face",))
    for request in ("WR-00000801_유통환경", "WR-00000805_유통환경"):
        _request_tree(scx, f"{spaced}/{request}", scenes=("2_Face",))
    empty = f"{CONTAINER}/PROJ-{tag}_testProject"
    scx.drive.add_dir(scx.path(f"{empty}/WR-00000004_내충격 강성강도"))

    scx.post(ENV + "/rescan", {})
    state = scx.client.get(ENV + "/rescan").json()
    assert state["status"] == "DONE", state
    assert sorted(item["name"] for item in state["created_projects"]) == sorted(
        [f"PROJ-{tag}_R95J 개발 과제", f"PROJ-{tag}_testProject"])
    assert len(state["created_requests"]) == 4 and state["failed"] == [], state
    assert {item["project_id"] for item in state["created_requests"]} == {
        item["id"] for item in state["created_projects"] if "R95J" in item["name"]}
    assert [(item["code"], item["relative_path"]) for item in state["needs_review"]] == [
        ("ENV_KEYWORD_NONE", f"{empty}/WR-00000004_내충격 강성강도")]
    again = scx.post(ENV + "/discover", {"force": True})
    assert (again["created_projects"], again["created_requests"]) == ([], [])


def test_drive_rescan_reinterprets_with_the_new_schema_in_read_sessions(scx):
    _container_schema()
    request = f"{CONTAINER}/PROJ-{_tag()}_R95J 개발 과제/WR-00000700_유통 환경"
    _request_tree(scx, request, scenes=("2_Face",))
    scx.post(ENV + "/rescan", {})
    [created] = scx.client.get(ENV + "/rescan").json()["created_requests"]
    _resave_schema()
    scx.post(ENV + "/rescan", {})
    result = scx.client.get(ENV + "/rescan").json()
    assert result["status"] == "DONE", result
    assert [item["request_id"] for item in result["reinterpreted"]] == [created["id"]], result
    assert result["reinterpret_blocked"] == [] and result["failed"] == []
    scx.post(ENV + "/rescan", {})
    assert scx.client.get(ENV + "/rescan").json()["reinterpret_unchanged"] == 1
