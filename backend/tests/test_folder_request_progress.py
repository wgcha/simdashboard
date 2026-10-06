"""Folder-registered request progress (docs/contracts/folder-request-progress.md §5).

Synthetic DEPTH_V1 trees in an isolated temp SPDM root; never a real DB or service.
"""
from __future__ import annotations

import builtins
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from app.database_connection import connect
from app.services import case_finalization, dashboard_capture, folder_request_progress
from tests.test_case_finalization_reports import HTML, _confirm, _preview, _upload
from tests.test_depth_schema import USAGE, USAGE_CASE, _build_usage, _register
from tests.test_environment_folder_flow_api import synthetic_pptx
from tests.test_new_scene_registration import CSV, CSV_BYTES, admin_client  # noqa: F401

pytestmark = pytest.mark.duckdb_integration

DIST = "75R9J_PV/[WR-0002]_[유통_환경]"
TAIL = "Drop/85qn80h_ref_organized/INDIVIDUAL"
CASE_A = f"{DIST}/Working/Package_Model_SetCase2_CushionCase2_조건표시"
CASE_B = f"{DIST}/Working/Package_Model_SetCase3_CushionCase3_조건표시"


@pytest.fixture(autouse=True)
def _fresh_memo():
    folder_request_progress.clear_memo()
    yield
    folder_request_progress.clear_memo()


def _progress(client, ids, status=200):
    folder_request_progress.clear_memo()
    response = client.get(f"/api/projects/{ids['project_id']}/requests/{ids['request_id']}/folder-progress")
    assert response.status_code == status, response.text
    return response.json()


def _steps(body):
    return {step["key"]: (step["status"], step["detail"]) for step in body["steps"]}


def _snapshot(root: Path) -> dict[str, int]:
    return {path.relative_to(root).as_posix(): path.lstat().st_mtime_ns for path in root.rglob("*")}


class _NoContentReads:
    """Fails on any open() of a path under the SPDM Working tree during progress calls."""

    def __init__(self, monkeypatch, root: Path):
        self.opened: list[str] = []
        working = str(root)

        def guard(original):
            def wrapped(file, *args, **kwargs):
                if isinstance(file, (str, os.PathLike)) and "/Working/" in os.fspath(file) and os.fspath(file).startswith(working):
                    self.opened.append(os.fspath(file))
                return original(file, *args, **kwargs)
            return wrapped

        monkeypatch.setattr(builtins, "open", guard(builtins.open))
        monkeypatch.setattr(io, "open", guard(io.open))


def _build_distribution(root: Path) -> None:
    for scene in ("2_Face", "3_Face"):
        folder = root / CASE_A / TAIL / scene
        folder.mkdir(parents=True)
        (folder / CSV).write_bytes(CSV_BYTES)
    # Case B: a Scene without any result table, so its collection never yields result assets.
    (root / CASE_B / TAIL / "2_Face").mkdir(parents=True)


def _ids(registered):
    return {"project_id": registered["project_id"], "request_id": registered["request_id"]}


def test_distribution_tree_steps_inputs_partial_results_final_and_report(admin_client, monkeypatch):
    client, root = admin_client
    _build_distribution(root)
    _scan, _preview_body, registered = _register(client, "DISTRIBUTION")
    ids = _ids(registered)

    body = _progress(client, ids)
    assert body["applicable"] is True and body["environment"] == "DISTRIBUTION" and body["total"] == 5
    assert [step["key"] for step in body["steps"]] == ["REGISTERED", "MODELING", "RESULTS", "FINAL", "REPORT"]
    assert [step["label"] for step in body["steps"]] == ["의뢰 등록", "해석 모델링", "해석 결과", "Final 지정", "보고서"]
    steps = _steps(body)
    assert steps["REGISTERED"] == ("DONE", None)
    assert steps["MODELING"] == ("WAITING", "Case 0/2 입력 있음")
    # Partial collection: only Case A has result assets.
    assert steps["RESULTS"] == ("IN_PROGRESS", "Case 1/2 결과 있음")
    assert body["current_key"] == "MODELING" and body["next_action"] == "입력 파일 대기 Case 2개"
    assert body["checked_at"].endswith("+00:00")

    # Upper-case .RAD directly in a Scene counts; .inc and a .rad below the Scene do not.
    (root / CASE_A / TAIL / "2_Face" / "model.RAD").write_text("synthetic", encoding="utf-8")
    scene_b = root / CASE_B / TAIL / "2_Face"
    (scene_b / "part.inc").write_text("synthetic", encoding="utf-8")
    (scene_b / "sub").mkdir()
    (scene_b / "sub" / "nested.rad").write_text("synthetic", encoding="utf-8")
    (scene_b / "model.rad.bak").write_text("synthetic", encoding="utf-8")
    body = _progress(client, ids)
    assert _steps(body)["MODELING"] == ("IN_PROGRESS", "Case 1/2 입력 있음")
    assert body["next_action"] == "입력 파일 대기 Case 1개"

    (scene_b / "solver.Fem").write_text("synthetic", encoding="utf-8")
    before = _snapshot(root)
    guard = _NoContentReads(monkeypatch, root)
    body = _progress(client, ids)
    assert guard.opened == []
    assert _snapshot(root) == before  # no SPDM writes: same entries and mtimes
    assert _steps(body)["MODELING"] == ("DONE", "Case 2/2 입력 있음")
    assert body["completed"] == 2 and body["current_key"] == "RESULTS"
    assert body["next_action"] == "결과 대기 Case 1개"
    assert _steps(body)["FINAL"] == ("WAITING", None) and _steps(body)["REPORT"] == ("WAITING", None)

    # Final designation of Case A with saved reports through the real finalization flow.
    with connect() as conn:
        case_a = conn.execute("SELECT id FROM dashboard_cases WHERE request_id=? AND relative_path=?",
                              [ids["request_id"], CASE_A]).fetchone()[0]
    ctx = {**ids, "environment": "DISTRIBUTION", "case_id": case_a,
           "capture_id": dashboard_capture.latest_capture_id(case_a)}
    plan = _preview(client, ctx)
    assert _upload(client, ctx, plan["operation_id"], "pptx", synthetic_pptx()).status_code == 200
    assert _upload(client, ctx, plan["operation_id"], "html", HTML).status_code == 200
    assert _confirm(client, ctx, plan["operation_id"], ["pptx", "html"]).status_code == 200
    body = _progress(client, ids)
    assert _steps(body)["FINAL"] == ("DONE", None) and _steps(body)["REPORT"] == ("DONE", None)
    # Facts are shown as they are: later steps are DONE although RESULTS is not.
    assert body["completed"] == 4 and body["current_key"] == "RESULTS"

    # The Reports folder cannot be read: FINAL stays DONE from the signed record, REPORT carries the reason.
    def unreadable(*_args, **_kwargs):
        raise case_finalization.CaseFinalizationError("FINALIZATION_SOURCE_UNAVAILABLE", "synthetic")
    with monkeypatch.context() as patch:
        patch.setattr(case_finalization, "_reports_directory_entries", unreadable)
        body = _progress(client, ids)
    assert _steps(body)["FINAL"] == ("DONE", None)
    assert _steps(body)["REPORT"] == ("WAITING", "Final 보고서 폴더를 읽을 수 없습니다.")

    # §15 D21 Verifier 1: the reports live under Final/Report; Final/Reports is never created.
    final_dir = root / DIST / "Final"
    assert sorted(path.name for path in (final_dir / "Report" / plan["case_label"] / plan["operation_id"]).iterdir()) == sorted(
        plan["report_files"].values())
    assert not (final_dir / "Reports").exists()

    # §15 D21 Verifier 2: a record signed with the legacy Final/Reports layout still stands
    # (FINAL done), its Final/Reports folder is not read (REPORT waiting), and nothing errors.
    metadata = root / plan["metadata_relative_path"]
    complete = json.loads((metadata / "complete.json").read_bytes())
    legacy_reports = complete["output_paths"]["Reports"].replace("/Final/Report/", "/Final/Reports/")
    (root / legacy_reports).parent.mkdir(parents=True)
    (root / complete["output_paths"]["Reports"]).rename(root / legacy_reports)
    complete["output_paths"]["Reports"] = legacy_reports
    (metadata / "complete.json").write_bytes(case_finalization._encode(
        case_finalization._signed_record(complete, "complete_signature", case_finalization.COMPLETE_DOMAIN)))
    body = _progress(client, ids)
    assert _steps(body)["FINAL"] == ("DONE", None) and _steps(body)["REPORT"] == ("WAITING", None)
    status = client.get("/api/dashboard/finalizations/status",
                        params={k: v for k, v in ctx.items() if k != "capture_id"})
    assert status.status_code == 200, status.text
    latest = status.json()["selected_case_latest"]
    assert latest["operation_id"] == plan["operation_id"] and latest["reports"] == []
    assert status.json()["unverified_records"] == 0
    # Renaming the folder to Final/Report (the user's fix) makes the reports count again.
    (final_dir / "Report" / plan["case_label"]).rmdir()
    (final_dir / "Reports").rename(final_dir / "Report")
    body = _progress(client, ids)
    assert _steps(body)["FINAL"] == ("DONE", None) and _steps(body)["REPORT"] == ("DONE", None)

    # A Final record whose Final/Report folder holds no pptx/html: FINAL done, REPORT waiting.
    record, _names = None, None
    with connect() as conn:
        record, _names = case_finalization.latest_completed(conn, **ids, environment="DISTRIBUTION")
    assert record and record["operation_id"] == plan["operation_id"]
    monkeypatch.setattr(case_finalization, "latest_completed", lambda *a, **k: (record, ["notes.txt"]))
    body = _progress(client, ids)
    assert _steps(body)["FINAL"] == ("DONE", None) and _steps(body)["REPORT"] == ("WAITING", None)


def test_usage_tree_all_done_until_final(admin_client):
    client, root = admin_client
    _build_usage(root)
    (root / USAGE / "Working" / USAGE_CASE / "Settle" / "settle.rad").write_text("synthetic", encoding="utf-8")
    _scan, _preview_body, registered = _register(client, "USAGE")
    body = _progress(client, _ids(registered))
    steps = _steps(body)
    assert body["environment"] == "USAGE"
    assert steps["MODELING"] == ("DONE", "Case 1/1 입력 있음")
    assert steps["RESULTS"] == ("DONE", "Case 1/1 결과 있음")
    assert body["completed"] == 3 and body["current_key"] == "FINAL"
    assert body["next_action"] == "대표 Case를 Final로 지정하세요"


def test_memo_reuses_result_for_thirty_seconds(admin_client):
    client, root = admin_client
    _build_usage(root)
    _scan, _preview_body, registered = _register(client, "USAGE")
    ids = _ids(registered)
    url = f"/api/projects/{ids['project_id']}/requests/{ids['request_id']}/folder-progress"
    first = client.get(url).json()
    (root / USAGE / "Working" / USAGE_CASE / "Wobble" / "w.fem").write_text("synthetic", encoding="utf-8")
    assert client.get(url).json() == first
    folder_request_progress.clear_memo()
    assert _steps(client.get(url).json())["MODELING"][0] == "DONE"


def test_request_without_live_folder_registration_is_not_applicable(admin_client):
    client, root = admin_client
    _build_usage(root)
    _scan, _preview_body, registered = _register(client, "USAGE")
    ids = _ids(registered)
    with connect() as conn:
        conn.execute("UPDATE folder_environment_registrations SET status='DELETED' WHERE request_id=?", [ids["request_id"]])
    body = _progress(client, ids)
    assert body["applicable"] is False and body["steps"] == [] and body["current_key"] is None
    _progress(client, {**ids, "request_id": f"missing-{uuid4().hex}"}, status=404)


def test_storage_root_unavailable_reports_reason(admin_client, monkeypatch):
    client, root = admin_client
    _build_usage(root)
    _scan, _preview_body, registered = _register(client, "USAGE")
    from app.services import spdm_storage

    def unavailable(conn):
        raise spdm_storage.SpdmStorageError("SPDM_ROOT_UNAVAILABLE", "synthetic")
    monkeypatch.setattr(spdm_storage, "storage_root", unavailable)
    body = _progress(client, _ids(registered))
    steps = _steps(body)
    assert steps["MODELING"] == ("WAITING", "SPDM 저장소를 확인할 수 없습니다.")
    assert steps["FINAL"] == ("WAITING", "SPDM 저장소를 확인할 수 없습니다.")


def _user(project_id=None):
    from app.security import hash_password
    user_id, now = f"progress-{uuid4().hex[:10]}", datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute("INSERT INTO users (id,username,password_hash,display_name,legacy_role,is_active,created_at,updated_at,account_status,is_global_admin) "
                     "VALUES (?,?,?,?,'viewer',true,?,?,'ACTIVE',false)",
                     [user_id, user_id, hash_password("synthetic-progress-password"), user_id, now, now])
        if project_id:
            conn.execute("INSERT INTO project_memberships (id,project_id,user_id,role,created_by,created_at,updated_by,updated_at) "
                         "VALUES (?,?,?,'general',?,?,?,?)", [f"m-{uuid4().hex}", project_id, user_id, user_id, now, user_id, now])
    return user_id


def test_permission_matches_case_results_catalog(admin_client):
    client, root = admin_client
    _build_usage(root)
    _scan, _preview_body, registered = _register(client, "USAGE")
    ids = _ids(registered)
    url = f"/api/projects/{ids['project_id']}/requests/{ids['request_id']}/folder-progress"
    catalog = ("/api/dashboard/catalog", {"request_id": ids["request_id"], "environment": "USAGE"})
    statuses = {}
    for name, user in (("member", _user(ids["project_id"])), ("outsider", _user())):
        token = client.post("/api/auth/login", json={"username": user, "password": "synthetic-progress-password"}).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        expected = client.get(catalog[0], params=catalog[1], headers=headers).status_code
        statuses[name] = client.get(url, headers=headers).status_code
        assert statuses[name] == expected, name
    assert statuses["member"] == 200
    assert client.get(url, headers={"Authorization": "Bearer invalid"}).status_code == 401


def test_scenes_come_from_stored_data_without_live_scan_or_recursion(admin_client, monkeypatch):
    client, root = admin_client
    _build_distribution(root)
    _scan, _preview_body, registered = _register(client, "DISTRIBUTION")
    ids = _ids(registered)
    scene_b = root / CASE_B / TAIL / "2_Face"
    (scene_b / "sub" / "deeper").mkdir(parents=True)
    (scene_b / "sub" / "deeper" / "x.rad").write_text("synthetic", encoding="utf-8")
    (root / CASE_A / TAIL / "3_Face" / "m.fem").write_text("synthetic", encoding="utf-8")
    from app.services import folder_discovery_scan, folder_schema_resolver

    def no_scan(*_args, **_kwargs):
        raise AssertionError("live folder scan")
    listed: list[str] = []
    original = os.scandir

    def spy(path="."):
        listed.append(os.fspath(path))
        return original(path)
    with monkeypatch.context() as patch:
        patch.setattr(folder_discovery_scan, "scan", no_scan)
        patch.setattr(folder_schema_resolver, "resolve_request_schema", no_scan)
        patch.setattr(os, "scandir", spy)
        body = _progress(client, ids)
    assert _steps(body)["MODELING"] == ("IN_PROGRESS", "Case 1/2 입력 있음")
    scenes = {str(root / CASE_A / TAIL / name) for name in ("2_Face", "3_Face")} | {str(scene_b)}
    working = [path for path in listed if "/Working" in path]
    assert working and set(working) <= scenes, working

    # An active folder-refresh snapshot is the preferred stored source.
    from app.services import folder_discovery_environment as fde
    with connect() as conn:
        fde.refresh_scope(conn, root, ids["project_id"], ids["request_id"], "DISTRIBUTION", "synthetic",
                          capture_cases=False)
        assert folder_schema_resolver.active_refresh_snapshot(
            conn, fde.root_identity(root), ids["project_id"], ids["request_id"], "DISTRIBUTION")
    monkeypatch.setattr(folder_discovery_scan, "scan", no_scan)
    monkeypatch.setattr(folder_schema_resolver, "resolve_request_schema", no_scan)
    assert _steps(_progress(client, ids))["MODELING"] == ("IN_PROGRESS", "Case 1/2 입력 있음")

    # Nothing stored about Scenes: MODELING explains and waits.
    with connect() as conn:
        conn.execute("DELETE FROM folder_environment_scans WHERE id LIKE 'folder-refresh-%' AND request_id=?",
                     [ids["request_id"]])
    with connect() as conn:
        conn.execute("UPDATE folder_environment_previews SET rows_json=? WHERE id IN (SELECT preview_id FROM "
                     "folder_environment_registrations WHERE request_id=?)", ['{"rows": null}', ids["request_id"]])
    body = _progress(client, ids)
    assert _steps(body)["MODELING"] == ("WAITING", "저장된 Scene 폴더 구조가 없습니다. 폴더 동기화 후 다시 확인됩니다.")
