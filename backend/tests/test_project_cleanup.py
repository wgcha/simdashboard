"""Administrator project cleanup (contract depth-schema §16, D15 exception) and §13.2-5.

Runs on the isolated DuckDB copy of the seeded test database (demo projects present).
With ANALYSIS_TEST_POSTGRES=1 the API tests target the explicitly configured
PostgreSQL test database instead (conftest ``isolated_database``).
"""
from __future__ import annotations

import ast
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import database
from app.config import database_settings
from app.database_connection import connect
from app.services import project_cleanup
from tests.test_depth_schema import _build_distribution, _build_usage, _register
from tests.test_folder_environment_registration_delete import _delete, _preview, _tree_state, viewer_client  # noqa: F401
from tests.test_new_scene_registration import admin_client  # noqa: F401

ENV = "/api/folder-discovery/environments"
ORION = "project-tv-001"
SHOWCASE = "project-feature-showcase"
SHARED_BLOB_ASSETS = ("media-contour-001", "media-showcase-trust", "media-showcase-multitype")
REFERENCE_COLUMN = re.compile(r"(^|_)(project|request|load_case)(_|$)|target_id$|run_id$|^case_id$|^capture_id$")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _postgres() -> bool:
    return database_settings().backend == "postgresql"


def _columns(conn) -> dict[str, list[tuple[str, str]]]:
    where = "WHERE table_schema='public'" if _postgres() else "WHERE table_schema='main'"
    rows = conn.execute(f"SELECT table_name,column_name,data_type FROM information_schema.columns {where}").fetchall()
    tables: dict[str, list[tuple[str, str]]] = {}
    for table, column, data_type in rows:
        tables.setdefault(str(table), []).append((str(column), str(data_type).upper()))
    return tables


def _references(conn, ids: set[str], skip=frozenset({"audit_events"})) -> dict[str, int]:
    """Exact-match references to ``ids`` in every text column of every table."""
    found = {}
    values = sorted(ids)
    for table, columns in _columns(conn).items():
        if table in skip:
            continue
        for column, data_type in columns:
            if not any(kind in data_type for kind in ("CHAR", "TEXT", "STRING")):
                continue
            n = conn.execute(f'SELECT count(*) FROM "{table}" WHERE "{column}" IN ({",".join("?" for _ in values)})', values).fetchone()[0]
            if n:
                found[f"{table}.{column}"] = n
    return found


def _project_ids(conn, project_id: str) -> set[str]:
    requests = {r[0] for r in conn.execute("SELECT id FROM analysis_requests WHERE project_id=?", [project_id]).fetchall()}
    loads = {r[0] for r in conn.execute(f"SELECT id FROM load_cases WHERE request_id IN ({','.join('?' for _ in requests)})", sorted(requests)).fetchall()} if requests else set()
    runs = {r[0] for r in conn.execute(f"SELECT id FROM analysis_runs WHERE load_case_id IN ({','.join('?' for _ in loads)})", sorted(loads)).fetchall()} if loads else set()
    return {project_id, *requests, *loads, *runs}


def _candidates(client):
    response = client.get(ENV + "/project-cleanup")
    assert response.status_code == 200, response.text
    return {item["project_id"]: item for item in response.json()["items"]}


def _cleanup_preview(client, ids, status=200):
    response = client.post(ENV + "/project-cleanup/preview", json={"project_ids": ids})
    assert response.status_code == status, response.text
    return response.json()


def _cleanup_delete(client, ids, token, acknowledge=None):
    body = {"project_ids": ids, "confirm_token": token}
    if acknowledge is not None:
        body["acknowledge_data_project_ids"] = acknowledge
    return client.post(ENV + "/project-cleanup/delete", json=body)


def _make_project(conn, *, with_request=True) -> dict[str, str]:
    suffix = uuid4().hex[:8]
    ids = {"project": f"project-legacy-{suffix}", "request": f"request-legacy-{suffix}", "load_case": f"lc-legacy-{suffix}"}
    old = _now()
    conn.execute("INSERT INTO projects(id,name,product_name,description,created_at) VALUES(?,?,?,?,?)",
                 [ids["project"], f"예전 프로젝트 {suffix}", "", "", old])
    if with_request:
        conn.execute("INSERT INTO analysis_requests(id,project_id,title,status,owner,requested_at) VALUES(?,?,?,?,?,?)",
                     [ids["request"], ids["project"], "예전 의뢰", "IN_PROGRESS", "담당", old])
        conn.execute("INSERT INTO load_cases(id,request_id,name,analysis_type,status,parameters_json,created_at) VALUES(?,?,?,?,?,?,?)",
                     [ids["load_case"], ids["request"], "drop", "DROP", "READY", "{}", old])
    return ids


# ---- seeded demo: delete Orion -----------------------------------------------------------

def test_delete_orion_removes_every_reference_and_keeps_others(admin_client):
    client, root = admin_client
    _build_distribution(root)  # an SPDM tree that must stay byte-for-byte unchanged (D14)
    before_tree = _tree_state(root)
    with connect() as conn:
        orion_ids = _project_ids(conn, ORION)
        showcase_ids = _project_ids(conn, SHOWCASE)
        showcase_before = _references(conn, showcase_ids)
        shared_blob = conn.execute("SELECT blob_id FROM media_assets WHERE id=?", ["media-contour-001"]).fetchone()[0]
        video_blobs = {r[0] for r in conn.execute("SELECT blob_id FROM drop_video_assets WHERE load_case_id IN "
                                                  "(SELECT id FROM load_cases WHERE request_id IN (SELECT id FROM analysis_requests WHERE project_id=?))",
                                                  [ORION]).fetchall()}
        users_before = conn.execute("SELECT count(*) FROM users").fetchone()[0]
        layouts_before = conn.execute("SELECT count(*) FROM report_layouts").fetchone()[0]
    assert len(orion_ids) > 5 and _references_present(orion_ids)

    listed = _candidates(client)
    assert listed[ORION]["category"] == "DEMO" and listed[ORION]["selectable"] is True
    assert listed[ORION]["requests"] == 2 and listed[ORION]["user_data_total"] == 0
    assert listed[SHOWCASE]["category"] == "DEMO" and listed[SHOWCASE]["user_data_total"] == 0

    preview = _cleanup_preview(client, [ORION])
    item = preview["items"][0]
    assert item["deletable"] and item["blockers"] == [] and item["category"] == "DEMO"
    assert item["counts"]["projects"] == 1 and item["counts"]["analysis_requests"] == 2
    assert item["counts"]["dashboards"] == 3 and item["counts"]["load_cases"] >= 1
    assert "asset_blobs" in item["counts"]  # the Orion-only drop videos
    # M1: Orion hosts the system analysis pages; deleting it needs the typed-name confirmation.
    assert {page["id"] for page in item["system_pages"]} == project_cleanup.SYSTEM_ANALYSIS_PAGE_IDS
    assert item["requires_acknowledgement"] and "SYSTEM_ANALYSIS_PAGES" in item["acknowledge_reasons"]
    # N1: a pristine Orion (seed + system backfill versions only) shows no user edits.
    assert all(page["user_versions"] == 0 for page in item["system_pages"]), item["system_pages"]
    assert item["acknowledge_reasons"] == ["SYSTEM_ANALYSIS_PAGES"]
    with connect() as conn:  # backfill authors other than plain 'system' are not users (created_by is NOT NULL)
        conn.execute("UPDATE dashboard_versions SET created_by='system-video-grid-backfill' WHERE dashboard_id='dashboard-drop-default'")
    item = _cleanup_preview(client, [ORION])["items"][0]
    assert all(page["user_versions"] == 0 for page in item["system_pages"]) and "USER_DATA" not in item["acknowledge_reasons"]
    with connect() as conn:  # a person's edit is counted
        conn.execute("UPDATE dashboard_versions SET created_by='user-kim' WHERE dashboard_id='dashboard-run-comparison-default'")
    item = _cleanup_preview(client, [ORION])["items"][0]
    assert {page["id"]: page["user_versions"] for page in item["system_pages"]}["dashboard-run-comparison-default"] >= 1
    assert "USER_DATA" in item["acknowledge_reasons"]
    preview = _cleanup_preview(client, [ORION])
    unconfirmed = _cleanup_delete(client, [ORION], preview["confirm_token"])
    assert unconfirmed.status_code == 409 and unconfirmed.json()["detail"]["code"] == "PROJECT_CLEANUP_CONFIRM_REQUIRED"
    assert unconfirmed.json()["detail"]["project_ids"] == [ORION]

    response = _cleanup_delete(client, [ORION], preview["confirm_token"], acknowledge=[ORION])
    assert response.status_code == 200, response.text
    assert response.json()["deleted"] == [ORION]
    with connect() as conn:
        assert _references(conn, orion_ids) == {}
        assert _references(conn, showcase_ids) == showcase_before  # other projects unchanged
        # Shared media: the blob also used by the showcase survives; Orion-only video blobs are gone.
        assert conn.execute("SELECT count(*) FROM asset_blobs WHERE id=?", [shared_blob]).fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM asset_blob_chunks WHERE blob_id=?", [shared_blob]).fetchone()[0] >= 1
        assert conn.execute("SELECT count(*) FROM media_assets WHERE id IN (?,?)", list(SHARED_BLOB_ASSETS[1:])).fetchone()[0] == 2
        remaining = conn.execute(f"SELECT count(*) FROM asset_blobs WHERE id IN ({','.join('?' for _ in video_blobs)})",
                                 sorted(video_blobs)).fetchone()[0]
        assert remaining == 0
        assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == users_before
        assert conn.execute("SELECT count(*) FROM report_layouts").fetchone()[0] == layouts_before
        audit = conn.execute("SELECT detail_json FROM audit_events WHERE action='PROJECT_CLEANUP_DELETED'").fetchall()
        assert any(ORION in (json.loads(row[0]) if isinstance(row[0], str) else row[0])["project_ids"] for row in audit if row[0])
        assert ORION in project_cleanup.removed_demo_projects(conn)
    assert _tree_state(root) == before_tree


def _references_present(ids):
    with connect() as conn:
        return bool(_references(conn, ids))


# ---- table inventory -------------------------------------------------------------------

def test_every_reference_column_is_handled_or_kept():
    """Fail when a table gains a project/request/load-case/run/target reference the cleanup does not classify."""
    handled = project_cleanup.handled_columns()
    with connect() as conn:
        tables = _columns(conn)
    unclassified = sorted(f"{table}.{column}" for table, columns in tables.items() for column, _type in columns
                          if REFERENCE_COLUMN.search(column) and (table, column) not in handled
                          and (table, column) not in project_cleanup.KEEP_COLUMNS)
    assert unclassified == [], "classify these columns in project_cleanup (_STAGES or KEEP_COLUMNS): " + ", ".join(unclassified)
    assert all(reason for reason in project_cleanup.KEEP_COLUMNS.values())


# ---- blockers ---------------------------------------------------------------------------

def test_registered_project_is_listed_but_blocked(admin_client):
    client, root = admin_client
    _build_distribution(root, final=False)
    _scan, _preview_body, registered = _register(client, "DISTRIBUTION")
    project_id = registered["project_id"]
    listed = _candidates(client)
    assert listed[project_id]["category"] == "REGISTERED" and listed[project_id]["selectable"] is False
    preview = _cleanup_preview(client, [project_id])
    assert [b["reason"] for b in preview["items"][0]["blockers"]] == ["LIVE_REGISTRATION"]
    blocked = _cleanup_delete(client, [project_id], preview["confirm_token"])
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "PROJECT_CLEANUP_BLOCKED", blocked.text
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM projects WHERE id=?", [project_id]).fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM dashboard_cases WHERE project_id=?", [project_id]).fetchone()[0] >= 1


def test_running_execution_blocks_whole_request(admin_client):
    client, _root = admin_client
    with connect() as conn:
        ids = _make_project(conn)
        conn.execute("INSERT INTO workflow_runs(id,name,request_id,definition_json,execution_mode,status,progress,created_by,created_at,started_at) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?)", [f"wf-{uuid4().hex[:8]}", "run", ids["request"], "{}", "DEMO_ONLY", "RUNNING", 10,
                                                     "tester", _now(), _now()])
    preview = _cleanup_preview(client, [ids["project"], SHOWCASE])
    reasons = {item["project_id"]: [b["reason"] for b in item["blockers"]] for item in preview["items"]}
    assert reasons == {ids["project"]: ["RUNNING_EXECUTION"], SHOWCASE: []}
    assert _cleanup_delete(client, [ids["project"], SHOWCASE], preview["confirm_token"]).status_code == 409
    with connect() as conn:  # all-or-nothing: the deletable showcase is untouched as well
        assert conn.execute("SELECT count(*) FROM projects WHERE id IN (?,?)", [ids["project"], SHOWCASE]).fetchone()[0] == 2


def test_stale_token_and_validation(admin_client):
    client, _root = admin_client
    with connect() as conn:
        ids = _make_project(conn)
    preview = _cleanup_preview(client, [ids["project"]])
    assert preview["items"][0]["category"] == "EMPTY" and preview["items"][0]["deletable"]
    with connect() as conn:  # data added after the preview
        conn.execute("INSERT INTO load_cases(id,request_id,name,analysis_type,status,parameters_json,created_at) VALUES(?,?,?,?,?,?,?)",
                     [f"lc-late-{uuid4().hex[:6]}", ids["request"], "late", "DROP", "READY", "{}", _now()])
    stale = _cleanup_delete(client, [ids["project"]], preview["confirm_token"])
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "DELETE_PREVIEW_STALE", stale.text
    bad = _cleanup_delete(client, [ids["project"]], "0" * 64)
    assert bad.status_code == 409 and bad.json()["detail"]["code"] == "DELETE_PREVIEW_STALE"
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM projects WHERE id=?", [ids["project"]]).fetchone()[0] == 1
    assert client.post(ENV + "/project-cleanup/preview", json={"project_ids": []}).status_code == 422
    assert client.post(ENV + "/project-cleanup/preview", json={"project_ids": ["project-none"]}).status_code == 404


def test_non_admin_is_forbidden(viewer_client):
    viewer, client, _root = viewer_client
    with connect() as conn:
        ids = _make_project(conn, with_request=False)
    token = _cleanup_preview(client, [ids["project"]])["confirm_token"]
    for method, path, body in (("get", "/project-cleanup", None), ("post", "/project-cleanup/preview", {"project_ids": [ids["project"]]}),
                               ("post", "/project-cleanup/delete", {"project_ids": [ids["project"]], "confirm_token": token})):
        response = getattr(viewer, method)(ENV + path, **({"json": body} if body is not None else {}))
        assert response.status_code == 403, (path, response.text)
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM projects WHERE id=?", [ids["project"]]).fetchone()[0] == 1


# ---- legacy-only project (D15 exception) ---------------------------------------------------

def test_legacy_only_project_is_cleaned_with_its_links(admin_client):
    client, _root = admin_client
    now = _now()
    with connect() as conn:
        ids = _make_project(conn)
        suffix = uuid4().hex[:6]
        root_key = "root-test"
        conn.execute("INSERT INTO folder_discovery_registry(id,root_key,relative_path,role,role_kind,scope_key,code,name,analysis_type,"
                     "parent_target_id,target_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                     [f"reg-p-{suffix}", root_key, "P", "PROJECT", "PROJECT", "s", "P", "P", "", None, ids["project"], now])
        conn.execute("INSERT INTO folder_discovery_registry(id,root_key,relative_path,role,role_kind,scope_key,code,name,analysis_type,"
                     "parent_target_id,target_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                     [f"reg-r-{suffix}", root_key, "P/R", "RESULTS", "RESULTS", "s", "R", "R", "", ids["load_case"], f"results-{suffix}", now])
        conn.execute("INSERT INTO spdm_storage_project_parents(project_folder,project_id,created_at,updated_at) VALUES(?,?,?,?)",
                     [f"PF-{suffix}", ids["project"], now, now])
        conn.execute("INSERT INTO spdm_storage_request_parents(request_folder,project_folder,project_id,request_id,created_at,updated_at) "
                     "VALUES(?,?,?,?,?,?)", [f"PF-{suffix}/RF", f"PF-{suffix}", ids["project"], ids["request"], now, now])
        conn.execute("INSERT INTO spdm_storage_bindings(load_case_id,project_id,request_id,relative_path,created_at,updated_at) "
                     "VALUES(?,?,?,?,?,?)", [ids["load_case"], ids["project"], ids["request"], "PF/RF/LC", now, now])
        conn.execute("INSERT INTO semantic_folder_bindings(id,project_id,request_id,load_case_id,relative_path,role,recipe_ids_json,"
                     "template_id,created_at,updated_at,created_by,revision) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                     [f"bind-{suffix}", ids["project"], ids["request"], ids["load_case"], "PF/RF/LC", "RESULTS", "[]", None, now, now, "t", 1])
        conn.execute("INSERT INTO product_information VALUES(?,?,?,?,?,?,?)", [f"prod-{suffix}", ids["project"], "MODEL", "모델", "X", None, "{}"])
        profile = conn.execute("SELECT id FROM folder_environment_profiles LIMIT 1").fetchone()[0]
        scan_id = f"scan-{suffix}"
        conn.execute("INSERT INTO folder_environment_scans(id,root_key,relative_path,environment,profile_id,profile_revision,project_id,"
                     "request_id,status,tree_json,issues_json,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     [scan_id, root_key, "P", "DISTRIBUTION", profile, 1, ids["project"], ids["request"], "COMPLETE", "[]", "[]", "t", now])
    listed = _candidates(client)
    assert listed[ids["project"]]["category"] == "EMPTY" and listed[ids["project"]]["selectable"]
    preview = _cleanup_preview(client, [ids["project"]])
    counts = preview["items"][0]["counts"]
    assert counts["folder_discovery_registry"] == 2 and counts["spdm_storage_bindings"] == 1
    assert counts["semantic_folder_bindings"] == 1 and counts["folder_environment_scans.project_id"] == 1
    item = preview["items"][0]
    assert item["requires_acknowledgement"] and item["acknowledge_reasons"] == ["RETAINED_DATA"]
    assert item["retained_data"]["legacy_links"] >= 5
    refused = _cleanup_delete(client, [ids["project"]], preview["confirm_token"], acknowledge=["someone-else"])
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "PROJECT_CLEANUP_CONFIRM_REQUIRED"
    assert _cleanup_delete(client, [ids["project"]], preview["confirm_token"], acknowledge=[ids["project"]]).status_code == 200
    with connect() as conn:
        assert _references(conn, set(ids.values()) | {f"PF-{suffix}"}) == {}
        assert conn.execute("SELECT count(*) FROM folder_environment_scans WHERE id=?", [scan_id]).fetchone()[0] == 1
        assert project_cleanup.removed_demo_projects(conn).isdisjoint({ids["project"]})


# ---- re-seed prevention ------------------------------------------------------------------

@pytest.mark.skipif(os.getenv("ANALYSIS_TEST_POSTGRES") == "1", reason="DuckDB development bootstrap only")
def test_duckdb_restart_does_not_recreate_removed_demo(admin_client):
    client, _root = admin_client
    preview = _cleanup_preview(client, [ORION, SHOWCASE])
    assert all(item["deletable"] for item in preview["items"]), preview
    assert _cleanup_delete(client, [ORION, SHOWCASE], preview["confirm_token"], acknowledge=[ORION]).status_code == 200
    database.initialize_database()  # development restart on the same file
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM projects WHERE id IN (?,?)", [ORION, SHOWCASE]).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM analysis_requests WHERE id='request-drop-001'").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM drop_video_assets").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM dashboards WHERE project_id=?", [ORION]).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM product_information WHERE project_id=?", [ORION]).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM report_layouts").fetchone()[0] >= 1  # global catalog still seeded
        assert project_cleanup.removed_demo_projects(conn) == {ORION, SHOWCASE}


@pytest.mark.skipif(os.getenv("ANALYSIS_TEST_POSTGRES") == "1", reason="shared PostgreSQL test database is not fresh")
def test_fresh_database_still_has_demo():
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM projects WHERE id IN (?,?)", [ORION, SHOWCASE]).fetchone()[0] == 2
        assert project_cleanup.removed_demo_projects(conn) == set()


@pytest.mark.unit
def test_postgres_startup_never_seeds(monkeypatch):
    calls: list[str] = []

    class Connection:
        backend = "postgresql"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, statement, parameters=None):
            calls.append(statement)
            if "information_schema.columns" in statement:
                return SimpleNamespace(fetchall=lambda: [(name,) for name in _ALL_COLUMNS])
            return SimpleNamespace(fetchone=lambda: ("present",), fetchall=lambda: [])

    for name in ("ensure_default_content", "seed_database", "_ensure_canonical_orion_seed", "ensure_feature_examples",
                 "seed_reference_database"):
        monkeypatch.setattr(database, name, lambda *_a, _n=name, **_k: (_ for _ in ()).throw(AssertionError(f"{_n} called")))
    monkeypatch.setattr(database, "database_settings", lambda: SimpleNamespace(backend="postgresql"))
    monkeypatch.setattr(database, "connect", lambda: Connection())
    database.initialize_database()
    assert calls and not any("INSERT" in statement.upper() for statement in calls)


_ALL_COLUMNS = sorted({
    "root_key", "root_path", "relative_path", "tree_json", "issues_json", "scan_id", "rules_revision", "catalog_revision",
    "applied_json", "revision", "role_kind", "parent_target_id", "scope_key", "target_id", "roles_json", "analysis_types_json",
    "project_id", "request_id", "storage_root_id", "environment", "metadata_json", "case_id", "fingerprint", "recipe_version",
    "manifest_json", "payload_json", "capture_id", "sha256", "media_type", "content", "rules_json", "profile_id", "rows_json",
    "can_apply", "preview_id", "idempotency_key", "status", "deleted_at", "deleted_by", "created_targets", "registration_id",
    "path_key", "schema_parent_path", "schema_role_kind", "schema_scan_id", "schema_profile_id", "schema_profile_revision",
    "case_relative_path", "result_relative_path", "inspection_json", "approval_json", "draft_id", "size_bytes", "action",
    "detail_json", "actor", "occurred_at",
})


@pytest.mark.unit
def test_update_scripts_never_seed_demo():
    root = Path(__file__).resolve().parents[2]
    for name in ("update.ps1", "update.bat"):
        text = (root / name).read_text(encoding="utf-8", errors="ignore")
        assert "seed_database" not in text and "seed_reference_database" not in text, name
        assert not re.search(r"--seed-mode\s+'?(reference|demo|duckdb)", text), name


@pytest.mark.unit
def test_cleanup_module_has_no_filesystem_access():
    source = (Path(__file__).parents[1] / "app/services/project_cleanup.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported |= {(node.module or "").split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not imported & {"os", "shutil", "pathlib", "io", "tempfile", "glob"}
    called = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert "open" not in called
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not attributes & {"unlink", "rmdir", "rmtree", "remove", "rename", "write_bytes", "write_text", "mkdir"}


# ---- §13.2-5: one-by-one registration delete leaves no empty project ----------------------

def test_one_by_one_registration_delete_leaves_no_empty_project(admin_client):
    client, root = admin_client
    _build_usage(root)
    _build_distribution(root, final=False)
    _s1, _p1, usage = _register(client, "USAGE")
    _s2, _p2, dist = _register(client, "DISTRIBUTION")
    project_id = usage["project_id"]
    assert dist["project_id"] == project_id and dist["request_id"] != usage["request_id"]
    first = _preview(client, [usage["registration_id"]])
    assert first["items"][0]["counts"]["projects"] == 0  # the other live registration still uses the project
    assert _delete(client, [usage["registration_id"]], first["confirm_token"]).status_code == 200
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM projects WHERE id=?", [project_id]).fetchone()[0] == 1
    last = _preview(client, [dist["registration_id"]])
    assert last["items"][0]["deletable"], last
    assert last["items"][0]["counts"]["projects"] == 1  # owned through the DELETED creator of the same project
    assert _delete(client, [dist["registration_id"]], last["confirm_token"]).status_code == 200
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM projects WHERE id=?", [project_id]).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM analysis_requests WHERE project_id=?", [project_id]).fetchone()[0] == 0


# ---- review fixes (H1, M2, L1, L2, L4) ---------------------------------------------------

def _clone_run(conn, load_case_id: str, run_id: str) -> None:
    cols = [r[0] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name='analysis_runs' "
                                       + ("AND table_schema='public' " if _postgres() else "") + "ORDER BY ordinal_position").fetchall()]
    src = dict(zip(cols, conn.execute(f"SELECT {','.join(cols)} FROM analysis_runs LIMIT 1").fetchone()))
    src.update(id=run_id, load_case_id=load_case_id)
    conn.execute(f"INSERT INTO analysis_runs({','.join(cols)}) VALUES({','.join('?' for _ in cols)})", [src[c] for c in cols])
    conn.execute("INSERT INTO scalar_results(id,analysis_run_id,variable_key,display_name,value_double,unit,verdict) VALUES(?,?,?,?,?,?,?)",
                 [f"sr-{run_id}", run_id, "stress", "응력", 1.0, "MPa", "PASS"])


def test_legacy_project_with_imported_results_needs_typed_confirmation(admin_client):
    """H1: imported runs/results count as 보관 데이터, are not 'user data free', and need the name confirmation."""
    client, _root = admin_client
    with connect() as conn:
        ids = _make_project(conn)
        _clone_run(conn, ids["load_case"], f"run-legacy-{uuid4().hex[:6]}")
    listed = _candidates(client)[ids["project"]]
    assert listed["category"] == "EMPTY" and listed["retained_data"] == {"runs": 1, "results": 1}
    assert listed["requires_acknowledgement"] is True
    orion = _candidates(client).get(ORION)  # absent on a shared PostgreSQL test DB after the Orion test
    assert orion is None or orion["requires_acknowledgement"] is True  # system pages
    preview = _cleanup_preview(client, [ids["project"]])
    assert _cleanup_delete(client, [ids["project"]], preview["confirm_token"]).json()["detail"]["code"] == "PROJECT_CLEANUP_CONFIRM_REQUIRED"
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM projects WHERE id=?", [ids["project"]]).fetchone()[0] == 1
    assert _cleanup_delete(client, [ids["project"]], preview["confirm_token"], acknowledge=[ids["project"]]).status_code == 200


def test_project_removed_after_preview_is_stale_and_oversized_is_422(admin_client, monkeypatch):
    client, _root = admin_client
    with connect() as conn:
        ids = _make_project(conn, with_request=False)
    preview = _cleanup_preview(client, [ids["project"]])
    with connect() as conn:
        conn.execute("DELETE FROM project_memberships WHERE project_id=?", [ids["project"]])
        conn.execute("DELETE FROM projects WHERE id=?", [ids["project"]])
    stale = _cleanup_delete(client, [ids["project"]], preview["confirm_token"])
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "DELETE_PREVIEW_STALE", stale.text
    # Chunked IN lists give the same plan; an oversized selection is a 422, never a 500.
    full = _cleanup_preview(client, [SHOWCASE])
    monkeypatch.setattr(project_cleanup, "_IN_CHUNK", 2)
    chunked = _cleanup_preview(client, [SHOWCASE])
    assert chunked["totals"] == full["totals"] and chunked["confirm_token"] == full["confirm_token"]
    monkeypatch.setattr(project_cleanup, "MAX_PARAMS", 5)
    oversized = client.post(ENV + "/project-cleanup/preview", json={"project_ids": [SHOWCASE]})
    assert oversized.status_code == 422 and oversized.json()["detail"]["code"] == "PROJECT_CLEANUP_INVALID"


def _foreign_keys(conn) -> list[tuple[str, str, str]]:
    if _postgres():
        # pg_catalog: information_schema hides constraints of tables the app role does not own.
        rows = conn.execute(
            "SELECT c.conrelid::regclass::text, a.attname, c.confrelid::regclass::text FROM pg_constraint c "
            "JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum = ANY(c.conkey) "
            "WHERE c.contype='f' AND c.connamespace='public'::regnamespace").fetchall()
        return [(str(a), str(b), str(c)) for a, b, c in rows]
    rows = conn.execute("SELECT table_name, constraint_column_names, referenced_table FROM duckdb_constraints() "
                        "WHERE constraint_type='FOREIGN KEY'").fetchall()
    return [(str(table), str(column), str(parent)) for table, columns, parent in rows for column in columns]


def test_every_foreign_key_into_a_deleted_table_is_classified():
    """L2: any column with a foreign key to a table cleanup deletes from must be handled or kept with a reason."""
    handled = project_cleanup.handled_columns()
    deleted_tables = {table for stage in project_cleanup._STAGES for table, _pairs in stage if not table.startswith("*")}
    with connect() as conn:
        keys = _foreign_keys(conn)
    assert keys, "no foreign keys found: metadata query is broken"
    unclassified = sorted({f"{table}.{column} -> {parent}" for table, column, parent in keys
                           if parent in deleted_tables and (table, column) not in handled
                           and (table, column) not in project_cleanup.KEEP_COLUMNS})
    assert unclassified == [], "classify in project_cleanup (_STAGES or KEEP_COLUMNS): " + ", ".join(unclassified)


def test_inherited_ownership_is_quiet_when_manual_data_was_added(admin_client):
    """M2: a manual request under the inherited project keeps the project; the last delete still succeeds."""
    client, root = admin_client
    _build_usage(root)
    _build_distribution(root, final=False)
    _s1, _p1, usage = _register(client, "USAGE")
    _s2, _p2, dist = _register(client, "DISTRIBUTION")
    project_id = usage["project_id"]
    first = _preview(client, [usage["registration_id"]])
    assert _delete(client, [usage["registration_id"]], first["confirm_token"]).status_code == 200
    manual = f"request-manual-{uuid4().hex[:6]}"
    with connect() as conn:
        conn.execute("INSERT INTO analysis_requests(id,project_id,title,status,owner,requested_at) VALUES(?,?,?,?,?,?)",
                     [manual, project_id, "수동", "IN_PROGRESS", "u", _now()])
    last = _preview(client, [dist["registration_id"]])
    assert last["items"][0]["deletable"] and last["items"][0]["blockers"] == [], last
    assert last["items"][0]["counts"]["projects"] == 0
    assert _delete(client, [dist["registration_id"]], last["confirm_token"]).status_code == 200
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM projects WHERE id=?", [project_id]).fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM analysis_requests WHERE id=?", [manual]).fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM analysis_requests WHERE id=?", [dist["request_id"]]).fetchone()[0] == 0


@pytest.mark.skipif(os.getenv("ANALYSIS_TEST_POSTGRES") == "1", reason="synthetic tombstone without a preview row (PostgreSQL FK)")
def test_family_ownership_ignores_entities_created_after_the_tombstone():
    """L1: a DELETED registration only explains entities that existed before it was deleted."""
    from datetime import timedelta
    from app.services import folder_environment_deletion as deletion
    created = _now()
    project_id = f"project-family-{uuid4().hex[:6]}"
    with connect() as conn:
        conn.execute("INSERT INTO projects(id,name,product_name,description,created_at) VALUES(?,?,?,?,?)",
                     [project_id, "p", "", "", created])
        for suffix, deleted_at in (("early", created - timedelta(minutes=5)),):
            conn.execute("INSERT INTO folder_environment_registrations(id,preview_id,idempotency_key,environment,project_id,request_id,"
                         "status,created_by,created_at,deleted_at,deleted_by,created_targets) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                         [f"reg-{suffix}-{project_id}", "preview-x", f"k-{suffix}-{project_id}", "USAGE", project_id, None, "DELETED",
                          "t", created - timedelta(minutes=10), deleted_at, "t", json.dumps({"project_ids": [project_id]})])
        assert deletion._created_by_deleted_registration(conn, "project_id", project_id, "projects", "created_at") is False
        conn.execute("UPDATE folder_environment_registrations SET deleted_at=? WHERE project_id=?",
                     [created + timedelta(minutes=5), project_id])
        assert deletion._created_by_deleted_registration(conn, "project_id", project_id, "projects", "created_at") is True
