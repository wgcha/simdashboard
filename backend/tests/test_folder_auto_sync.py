"""Automatic Folder Schema sync for viewed screens (stage 2, 30 s polling)."""
from __future__ import annotations

import pytest

from app.database_connection import connect
from app.services import folder_auto_sync
from tests.test_new_scene_registration import CSV, CSV_BYTES, OPTION, _seed, admin_client  # noqa: F401

pytestmark = pytest.mark.duckdb_integration
SYNC = "/api/folder-discovery/environments/sync"


@pytest.fixture(autouse=True)
def _no_interval(monkeypatch):
    folder_auto_sync.reset_for_tests()
    monkeypatch.setattr(folder_auto_sync, "MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(folder_auto_sync, "FORCE_MIN_INTERVAL_SECONDS", 0.0)
    yield
    folder_auto_sync.reset_for_tests()


def _sync(client, project_id, request_id, **extra):
    response = client.post(SYNC, json={"project_id": project_id, "request_id": request_id,
                                       "environment": "DISTRIBUTION", **extra})
    assert response.status_code == 200, response.text
    return response.json()


def _snapshot_count(request_id):
    with connect() as conn:
        return conn.execute("SELECT count(*) FROM folder_environment_scans WHERE request_id=? AND id LIKE 'folder-refresh-%'",
                            [request_id]).fetchone()[0]


def _scenes(client, project_id, request_id):
    response = client.get("/api/dashboard/catalog", params={"project_id": project_id, "request_id": request_id,
                                                            "environment": "DISTRIBUTION"})
    assert response.status_code == 200
    return {str(scene.get("relative_path") or scene.get("label") or "").rsplit("/", 1)[-1] for scene in response.json()["scenes"]}


def test_copied_scene_appears_after_sync_and_quick_check_skips_unchanged_tree(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    first = _sync(client, project_id, request_id)
    assert first["status"] in {"REFRESHED", "UNCHANGED"}

    # Nothing changed: the quick check answers without a new snapshot.
    before = _snapshot_count(request_id)
    quiet = _sync(client, project_id, request_id)
    assert (quiet["status"], quiet["check_mode"]) == ("UNCHANGED", "QUICK")
    assert _snapshot_count(request_id) == before

    # A user copies a new Scene folder with results straight into the share.
    copied = root / OPTION / "6_Corner"
    copied.mkdir()
    (copied / CSV).write_bytes(CSV_BYTES)
    changed = _sync(client, project_id, request_id)
    assert (changed["status"], changed["changed"], changed["check_mode"]) == ("REFRESHED", True, "FULL")
    assert "6_Corner" in _scenes(client, project_id, request_id)

    # The new snapshot stores the quick fingerprint, so the next check is quick again.
    again = _sync(client, project_id, request_id)
    assert (again["status"], again["check_mode"]) == ("UNCHANGED", "QUICK")


def test_file_change_without_structure_change_is_detected(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    _sync(client, project_id, request_id)
    target = root / OPTION / "2_Face" / CSV
    target.write_bytes(CSV_BYTES + b"BOTTOM,21,21,21,21\n")
    result = _sync(client, project_id, request_id)
    assert result["check_mode"] == "FULL"
    assert result["status"] in {"REFRESHED", "UNCHANGED"}


def test_checks_are_coalesced_within_the_interval(admin_client, monkeypatch):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    monkeypatch.setattr(folder_auto_sync, "MIN_INTERVAL_SECONDS", 60.0)
    first = _sync(client, project_id, request_id)
    assert first["coalesced"] is False
    (root / OPTION / "7_Edge").mkdir()
    second = _sync(client, project_id, request_id)
    assert second["coalesced"] is True
    # A manual check is allowed sooner than the polling interval.
    forced = _sync(client, project_id, request_id, force=True)
    assert forced["coalesced"] is False and forced["check_mode"] == "FULL"
    assert "7_Edge" in _scenes(client, project_id, request_id)


def test_sync_rejects_project_request_mismatch(admin_client):
    client, root = admin_client
    _project_id, request_id = _seed(client, root)
    response = client.post(SYNC, json={"project_id": "other-project", "request_id": request_id,
                                       "environment": "DISTRIBUTION"})
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "FOLDER_SCHEMA_SCOPE_MISMATCH"


def test_sync_calls_request_view_guard(admin_client, monkeypatch):
    from fastapi import HTTPException
    from app.routers import folder_discovery_environment as router

    client, root = admin_client
    project_id, request_id = _seed(client, root)
    seen = []

    def denied(request, permission, kind, resource_id, **kwargs):
        seen.append((permission, kind, resource_id))
        raise HTTPException(403, detail={"code": "PROJECT_DATA_VIEW_DENIED"})

    monkeypatch.setattr(router, "require_resource_permission", denied)
    response = client.post(SYNC, json={"project_id": project_id, "request_id": request_id,
                                       "environment": "DISTRIBUTION"})
    assert response.status_code == 403
    assert seen == [("project.data.view", "request", request_id)]


def test_growing_solver_log_never_triggers_refresh(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    _sync(client, project_id, request_id)
    with connect() as conn:
        captures = conn.execute("SELECT count(*) FROM dashboard_captures").fetchone()[0]
    before = _snapshot_count(request_id)
    log = root / OPTION / "2_Face" / "solver.out"
    for line in range(3):
        with log.open("a", encoding="utf-8") as handle:
            handle.write(f"cycle {line}\n")
        result = _sync(client, project_id, request_id)
        assert (result["status"], result["check_mode"]) == ("UNCHANGED", "QUICK"), result
    assert _snapshot_count(request_id) == before
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM dashboard_captures").fetchone()[0] == captures


def test_manual_check_still_uses_quick_path(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    _sync(client, project_id, request_id)
    forced = _sync(client, project_id, request_id, force=True)
    assert (forced["status"], forced["check_mode"]) == ("UNCHANGED", "QUICK")


def test_snapshot_without_quick_fingerprint_is_backfilled(admin_client):
    import json
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    _sync(client, project_id, request_id)
    with connect() as conn:
        snap_id, tree = conn.execute(
            "SELECT id,tree_json FROM folder_environment_scans WHERE request_id=? AND id LIKE 'folder-refresh-%' "
            "ORDER BY created_at DESC,id DESC LIMIT 1", [request_id]).fetchone()
        snapshot = json.loads(tree)
        snapshot["schema"].pop("stat_fingerprint", None)
        conn.execute("UPDATE folder_environment_scans SET tree_json=? WHERE id=?",
                     [json.dumps(snapshot, ensure_ascii=False), snap_id])
    folder_auto_sync.reset_for_tests()
    first = _sync(client, project_id, request_id)
    assert (first["status"], first["check_mode"]) == ("UNCHANGED", "FULL")
    folder_auto_sync.reset_for_tests()  # e.g. a server restart: the stored field is used
    second = _sync(client, project_id, request_id)
    assert (second["status"], second["check_mode"]) == ("UNCHANGED", "QUICK")


def test_persistent_conflict_is_not_refreshed_again_until_folders_change(admin_client, monkeypatch):
    from app.services import folder_discovery_environment as fde
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    _sync(client, project_id, request_id)
    (root / OPTION / "8_New").mkdir()
    calls = []
    from app.services.folder_discovery_scan import scan, stat_fingerprint
    from tests.test_new_scene_registration import REQUEST

    def conflicting(conn, scan_root, *args, **kwargs):
        # A refresh that finds an ambiguous role keeps the previous snapshot active.
        calls.append(1)
        fresh = scan(scan_root, REQUEST, skip_descendants=fde._skip_final_archive(REQUEST))
        return {"status": "CONFLICT", "activated": False, "changed": False, "snapshot_id": None,
                "diff": {"added": 1, "removed": 0, "changed": 0}, "stat_fingerprint": stat_fingerprint(fresh)}

    monkeypatch.setattr(fde, "refresh_scope", conflicting)
    assert _sync(client, project_id, request_id)["status"] == "CONFLICT"
    again = _sync(client, project_id, request_id)
    assert (again["status"], again["check_mode"]) == ("CONFLICT", "QUICK")
    assert len(calls) == 1
    (root / OPTION / "9_Other").mkdir()
    _sync(client, project_id, request_id)
    assert len(calls) == 2
