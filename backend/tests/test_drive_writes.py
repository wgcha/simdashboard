"""Stage D3 SCX drive write path (docs/features/scx-drive.md §10, plan §4 D3).

Everything runs against the in-memory fake adapter (``drive_fakes``) with an isolated
DuckDB test database and synthetic folders; no real SDK, drive, user database or
running service is touched.  Every test asserts that no drive call happened while the
calling thread held a database connection, and that the drive saw no delete, move or
overwrite (the fake has no such operations and ``upload_new``/``copy_within`` refuse an
existing name).  The queue runs on the test thread (``run_until_idle``) unless a test
starts the background worker itself.
"""
from __future__ import annotations

import ast
import hashlib
import json
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.database_connection import connect, connection_held
from app.main import app
from app.services import dashboard_capture, folder_auto_sync, result_drop_upload
from app.services.drive import gateway as drive_gateway
from app.services.drive import reads as drive_reads
from app.services.drive import upload_queue
from app.services.storage.drive import DriveRoot

from drive_fakes import ErrorCode, MemoryDrive, make_fake_adapter_module
from test_drive_reads import CSV, CSV_BYTES, OPTION, REQUEST, SERVER, Scx, _bundle, _register, _scenes, _sync

pytestmark = pytest.mark.duckdb_integration

BACKEND = Path(__file__).resolve().parents[1]
REG = "/api/result-registration"
API = "/api/dashboard/finalizations"
CASE = OPTION.split("/Drop/")[0]
CASE_LABEL = CASE.rsplit("/", 1)[-1]
FINAL = f"{REQUEST}/Final"
HTML = b"<!DOCTYPE html>\n<html><head><meta charset=\"utf-8\"><title>t</title></head><body>\xed\x95\x9c</body></html>"
NEW_SCENE = "5_Face"


@pytest.fixture
def scxw(tmp_path, monkeypatch, password_auth_bootstrap_admin):
    """scx mode with "드라이브 쓰기 허용" on; the queue worker does not autostart (tests drive it)."""
    drive = MemoryDrive(page_size=50)
    monkeypatch.setitem(sys.modules, "scx_drive_adapter", make_fake_adapter_module(drive))
    root = f"SPDM/T{uuid.uuid4().hex[:10]}"
    for name, value in {
        "SIMDASH_DRIVE_GATEWAY": "scx", "SIMDASH_SCX_WORKER_PYTHON": sys.executable,
        "SIMDASH_SCX_SERVER_URL": SERVER, "SIMDASH_SCX_CLIENT_NAME": "test-client",
        "SIMDASH_SECRET_ENC_KEY": Fernet.generate_key().decode(), "SIMDASH_SCX_WORK_DIR": str(tmp_path / "scx-work"),
        "SIMDASH_SCX_DRIVE_ROOT": root, "AUTH_MODE": "password", "SIMDASH_DRIVE_WRITES_ENABLED": "true",
        "AUTH_SECRET_KEY": "drive-writes-isolated-test-secret-at-least-32",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("SIMDASH_SPDM_ROOT", raising=False)
    monkeypatch.setattr(folder_auto_sync, "MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(folder_auto_sync, "FORCE_MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(result_drop_upload, "DISK_MARGIN_MIN_BYTES", 1024)
    monkeypatch.setattr(upload_queue, "AUTOSTART", False)
    folder_auto_sync.reset_for_tests()
    result_drop_upload.reset_for_tests()
    upload_queue.reset_for_tests()
    drive_reads.reset_for_tests()
    drive_gateway.shutdown()
    with connect() as conn:
        conn.execute("DELETE FROM drive_credentials")
    drive.add_dir(root)
    violations: list[tuple[str, str]] = []
    drive.on_call = lambda op, rel: violations.append((op, rel)) if connection_held() else None
    _, username, password = password_auth_bootstrap_admin
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": username, "password": password})
        assert login.status_code == 200, login.text
        client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        saved = client.put("/api/admin/drive/credentials", content=_bundle())
        assert saved.status_code == 200 and saved.json()["test"]["ok"], saved.text
        yield Scx(client, drive, root, violations)
    upload_queue.reset_for_tests()
    drive_gateway.shutdown()
    folder_auto_sync.reset_for_tests()
    result_drop_upload.reset_for_tests()
    assert violations == [], f"drive calls while a DB connection was held: {violations}"


def _settle(*, now: datetime | None = None) -> int:
    assert not connection_held()
    return upload_queue.run_until_idle(now=now)


def _later(minutes: int = 120) -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=minutes)


def _batch(scx: Scx, batch_id: str) -> dict:
    response = scx.client.get(f"/api/drive/upload-batches/{batch_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _items(batch_id: str) -> list[tuple]:
    with connect() as conn:
        return [(item.kind, item.state, item.target, item.transfer_method, item.attempts)
                for item in upload_queue.batch_items(conn, batch_id)]


# --- result drop upload (W8 in scx mode) ----------------------------------------------------------

def _drop_body(project_id, request_id, target, files, folders=None):
    return {"project_id": project_id, "request_id": request_id, "environment": "DISTRIBUTION",
            "target_relative_path": target, "files": [{"relative_path": path, "size": len(data)} for path, data in files],
            "folders": folders or []}


def _drop(scx: Scx, project_id, request_id, files, *, target=OPTION) -> dict:
    started = scx.client.post(REG + "/drop-uploads", json=_drop_body(project_id, request_id, target, files))
    assert started.status_code == 201, started.text
    session = started.json()
    contents = dict(files)
    for item in session["files"]:
        put = scx.client.put(f"{REG}/drop-uploads/{session['session_id']}/files/{item['index']}", params={"offset": 0},
                             content=contents[item["relative_path"]], headers={"Content-Type": "application/octet-stream"})
        assert put.status_code == 200 and put.json()["complete"], put.text
    return session


def _complete(scx: Scx, session_id: str) -> dict:
    response = scx.client.post(f"{REG}/drop-uploads/{session_id}/complete")
    assert response.status_code == 200, response.text
    return response.json()


def test_drop_upload_stages_on_the_server_and_the_queue_creates_new_drive_files(scxw):
    project_id, request_id = _register(scxw)
    data = CSV_BYTES + b"NEW,1,1,1,1\n"
    session = _drop(scxw, project_id, request_id, [(f"{NEW_SCENE}/{CSV}", data)])
    staging = upload_queue.staging_dir_for(session["session_id"])
    assert (staging / "0.part").read_bytes() == data          # chunks are staged on the server ...
    assert not any(op in {"upload_new", "mkdirs"} for op, _ in scxw.drive.calls)   # ... not on the drive
    result = _complete(scxw, session["session_id"])
    assert result["state"] == "QUEUED" and result["drive"]["state"] == "QUEUED" and result["queued_files"] == 1
    assert scxw.drive.writes() == []
    steps = _settle()
    assert steps == 2, _items(session["session_id"])
    target = scxw.path(f"{OPTION}/{NEW_SCENE}/{CSV}")
    assert scxw.drive.content(target) == data
    batch = _batch(scxw, session["session_id"])
    assert (batch["state"], batch["files_done"], batch["files_total"]) == ("DONE", 1, 1)
    assert not staging.exists()                                # server staging removed after DONE
    assert [op for op, _ in scxw.drive.writes()] == ["mkdirs", "upload_new"]
    # The D2 read path (60 s auto-sync) picks the new Scene up.
    _sync(scxw, project_id, request_id)
    assert NEW_SCENE in _scenes(scxw, request_id)
    with connect() as conn:
        audit = conn.execute("SELECT action FROM audit_events WHERE action='RESULT_DROP_UPLOAD_QUEUED'").fetchall()
    assert audit


def test_drop_conflict_on_the_drive_is_never_overwritten_and_identical_content_counts_as_done(scxw):
    """C2 (덮어쓰기 금지 업로드): a different file under the same name stays; the same bytes are DONE."""
    project_id, request_id = _register(scxw)
    other = b"someone else's file\n"
    session = _drop(scxw, project_id, request_id, [(f"2_Face/new_a.csv", b"a,1\n"), (f"2_Face/new_b.csv", b"b,2\n")])
    result = _complete(scxw, session["session_id"])
    assert result["state"] == "QUEUED"
    # Between publish and the queue run, other users write the same names on the drive.
    scxw.drive.add_file(scxw.path(f"{OPTION}/2_Face/new_a.csv"), other)
    scxw.drive.add_file(scxw.path(f"{OPTION}/2_Face/new_b.csv"), b"b,2\n")
    _settle()
    assert scxw.drive.content(scxw.path(f"{OPTION}/2_Face/new_a.csv")) == other       # untouched
    states = {target.rsplit("/", 1)[-1]: state for _kind, state, target, _m, _a in _items(session["session_id"])}
    assert states == {"new_a.csv": "CONFLICT", "new_b.csv": "DONE"}
    batch = _batch(scxw, session["session_id"])
    assert batch["state"] == "PARTIAL" and batch["errors"][0]["code"] == "SPDM_CONFLICT"
    assert upload_queue.staging_dir_for(session["session_id"]).joinpath("0.part").exists()   # kept until DONE


def test_drop_conflict_present_at_publish_is_refused_before_queueing(scxw):
    project_id, request_id = _register(scxw)
    session = _drop(scxw, project_id, request_id, [("2_Face/late.csv", b"x\n")])
    scxw.drive.add_file(scxw.path(f"{OPTION}/2_Face/late.csv"), b"other\n")
    response = scxw.client.post(f"{REG}/drop-uploads/{session['session_id']}/complete")
    assert response.status_code == 409 and response.json()["detail"]["code"] == "RESULT_DROP_CONFLICT"
    with connect() as conn:
        assert upload_queue.batch_summary(conn, session["session_id"]) is None
    assert scxw.drive.writes() == []


def test_new_folder_is_created_through_the_queue(scxw, monkeypatch):
    project_id, request_id = _register(scxw)
    monkeypatch.setattr(upload_queue, "wait_batch", lambda batch_id, timeout, **_: (_settle(), _summary(batch_id))[1])
    response = scxw.client.post(REG + "/drop-target/folders", json={
        "project_id": project_id, "request_id": request_id, "environment": "DISTRIBUTION",
        "parent_relative_path": OPTION, "name": "9_Face", "confirm": True})
    assert response.status_code == 201, response.text
    assert response.json()["drive"]["state"] == "DONE"
    assert scxw.drive.nodes[scxw.path(f"{OPTION}/9_Face")].kind == "dir"


def test_folder_structure_is_queued_on_the_drive_idempotent_and_linked(scxw, monkeypatch):
    """"폴더 구조 만들기" in scx mode: MKDIR items of one batch, existing folders kept, link after the batch."""
    from app.services import folder_auto_discovery, result_folder_structure

    project_id, request_id = _register(scxw)
    kicked: list[bool] = []
    monkeypatch.setattr(result_folder_structure, "_kick_discovery", lambda: kicked.append(True))
    monkeypatch.setattr(upload_queue, "wait_batch", lambda batch_id, timeout, **_: (_settle(), _summary(batch_id))[1])
    monkeypatch.setattr(folder_auto_discovery, "FORCE_MIN_INTERVAL_SECONDS", 0.0)
    folder_auto_discovery.reset_for_tests()
    overview = scxw.client.get(REG + "/drop-target/structure", params={"project_id": project_id, "request_id": request_id})
    assert overview.status_code == 200, overview.text
    usage = next(item for item in overview.json()["environments"] if item["environment"] == "USAGE")
    proposal = usage["proposal"]
    assert usage["status"] == "MISSING" and proposal["name"] == "[WR-0001]_[사용_환경]"
    body = {"project_id": project_id, "request_id": request_id, "environment": "USAGE",
            "new_request_folder": {"parent_relative_path": proposal["parent_relative_path"], "name": proposal["name"]},
            "case_names": ["Assy_Case1"]}
    response = scxw.client.post(REG + "/drop-target/structure", json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["drive"]["state"] == "DONE" and [item["role"] for item in result["queued"]] == [
        "REQUEST", "WORKING", "SIMULATION_CASE"]
    usage_folder = proposal["relative_path"]
    assert scxw.drive.nodes[scxw.path(f"{usage_folder}/Working/Assy_Case1")].kind == "dir"
    assert kicked == [True] and result["link"]["status"] == "PENDING"
    found = folder_auto_discovery.discover(force=True)
    assert [(item["environment"], item["id"], item["linked"]) for item in found["created_requests"]] == [
        ("USAGE", request_id, True)], found
    # Re-run: everything exists, nothing is queued; the distribution Case folder is reported as existing.
    again = scxw.client.post(REG + "/drop-target/structure", json={
        "project_id": project_id, "request_id": request_id, "environment": "USAGE",
        "request_relative_path": usage_folder, "case_names": ["Assy_Case1"]})
    assert again.status_code == 200, again.text
    assert again.json()["drive"] is None and again.json()["created"] == [] and again.json()["link"]["status"] == "LINKED"
    assert {op for op, _ in scxw.drive.calls if op in {"upload_new", "copy_within"}} == set()
    # Drive writes off: the endpoint answers DRIVE_WRITE_DISABLED (read-only notice on the screen).
    monkeypatch.setenv("SIMDASH_DRIVE_WRITES_ENABLED", "false")
    drive_gateway.shutdown()
    refused = scxw.client.post(REG + "/drop-target/structure", json={**body, "case_names": ["Assy_Case2"]})
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "DRIVE_WRITE_DISABLED", refused.text


def _summary(batch_id: str) -> dict | None:
    with connect() as conn:
        return upload_queue.batch_summary(conn, batch_id)


# --- retries, pause, restart -------------------------------------------------------------------------

def test_retryable_errors_back_off_then_complete_without_duplicates(scxw):
    project_id, request_id = _register(scxw)
    session = _drop(scxw, project_id, request_id, [("2_Face/r1.csv", b"1\n"), ("2_Face/r2.csv", b"2\n")])
    second = scxw.path(f"{OPTION}/2_Face/r2.csv")
    scxw.drive.fail_path[("upload_new", second)] = [ErrorCode.TIMEOUT, ErrorCode.BUSY]
    _complete(scxw, session["session_id"])
    _settle()
    items = _items(session["session_id"])
    assert items[-1][1] == "PENDING" and items[-1][4] == 1          # backing off (30 s)
    assert _batch(scxw, session["session_id"])["state"] == "RUNNING"
    _settle(now=_later(1))
    assert _items(session["session_id"])[-1][4] == 2                # BUSY: next back-off (2 min)
    _settle(now=_later(5))
    assert _batch(scxw, session["session_id"])["state"] == "DONE"
    assert scxw.drive.content(second) == b"2\n"
    assert sum(1 for op, rel in scxw.drive.calls if op == "upload_new" and rel == second) == 3


def test_upload_that_landed_before_a_lost_response_is_recognised_by_stat(scxw):
    project_id, request_id = _register(scxw)
    session = _drop(scxw, project_id, request_id, [("2_Face/lost.csv", b"lost-response\n")])
    scxw.drive.lost_response["upload_new"] = ErrorCode.TIMEOUT
    _complete(scxw, session["session_id"])
    _settle()
    assert _items(session["session_id"])[-1][1] == "PENDING"
    _settle(now=_later())
    target = scxw.path(f"{OPTION}/2_Face/lost.csv")
    assert _items(session["session_id"])[-1][1] == "DONE"
    assert sum(1 for op, rel in scxw.drive.calls if op == "upload_new" and rel == target) == 1   # stat, no re-upload


def test_auth_required_pauses_the_queue_until_credentials_are_registered(scxw):
    project_id, request_id = _register(scxw)
    session = _drop(scxw, project_id, request_id, [("2_Face/auth.csv", b"auth\n")])
    scxw.drive.fail_next["upload_new"] = ErrorCode.AUTH_REQUIRED
    _complete(scxw, session["session_id"])
    _settle()
    assert _items(session["session_id"])[-1][1] == "BLOCKED"
    assert _batch(scxw, session["session_id"])["state"] == "PAUSED"
    assert scxw.client.get("/api/drive/status").json()["queue_paused"] is True
    assert upload_queue.process_next() is False                     # paused: nothing runs
    saved = scxw.client.put("/api/admin/drive/credentials", content=_bundle())
    assert saved.status_code == 200
    _settle()
    assert _batch(scxw, session["session_id"])["state"] == "DONE"
    assert scxw.client.get("/api/drive/status").json()["queue_paused"] is False


def test_restart_resumes_running_items_and_the_worker_thread_finishes_them(scxw, monkeypatch):
    project_id, request_id = _register(scxw)
    session = _drop(scxw, project_id, request_id, [("2_Face/restart.csv", b"restart\n")])
    _complete(scxw, session["session_id"])
    with connect() as conn:   # the process died while the first item was RUNNING
        conn.execute("UPDATE drive_upload_queue SET state='RUNNING', attempts=1 WHERE batch_id=? AND seq=0",
                     [session["session_id"]])
    monkeypatch.setattr(upload_queue, "AUTOSTART", True)
    assert upload_queue.start() is True                              # startup: RUNNING → PENDING, worker thread
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and _summary(session["session_id"])["state"] != "DONE":
        time.sleep(0.1)
    upload_queue.stop()
    assert _summary(session["session_id"])["state"] == "DONE"
    assert scxw.drive.content(scxw.path(f"{OPTION}/2_Face/restart.csv")) == b"restart\n"


def test_admin_queue_lists_retries_and_cancels_without_touching_the_drive(scxw):
    project_id, request_id = _register(scxw)
    session = _drop(scxw, project_id, request_id, [("2_Face/adm.csv", b"mine\n")])
    _complete(scxw, session["session_id"])
    target = scxw.path(f"{OPTION}/2_Face/adm.csv")
    scxw.drive.add_file(target, b"theirs\n")
    _settle()
    listed = scxw.client.get("/api/admin/drive/queue").json()
    conflict = next(item for item in listed["items"] if item["batch_id"] == session["session_id"] and item["kind"] == "FILE")
    assert conflict["state"] == "CONFLICT" and listed["counts"]["CONFLICT"] >= 1
    retried = scxw.client.post(f"/api/admin/drive/queue/{conflict['id']}/retry")
    assert retried.status_code == 200 and retried.json()["state"] == "PENDING"
    _settle()
    assert _items(session["session_id"])[-1][1] == "CONFLICT"        # re-checked, still someone else's file
    cancelled = scxw.client.post(f"/api/admin/drive/queue/{conflict['id']}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["state"] == "CANCELLED"
    _settle()
    assert scxw.drive.content(target) == b"theirs\n"
    done = scxw.client.post(f"/api/admin/drive/queue/{conflict['id']}/cancel")
    assert done.status_code == 409


# --- Final designation on the drive ------------------------------------------------------------------

def _final_ctx(scxw) -> dict:
    for scene in ("2_Face", "3_Face"):
        scxw.add(f"{OPTION}/{scene}/model.rad", b"/INCLUDE part.inc\n")
        scxw.add(f"{OPTION}/{scene}/part.inc", f"{scene} include\n".encode())
    project_id, request_id = _register(scxw)
    with connect() as conn:
        case_id = conn.execute("SELECT id FROM dashboard_cases WHERE request_id=? AND project_id=?",
                               [request_id, project_id]).fetchone()[0]
    return {"project_id": project_id, "request_id": request_id, "environment": "DISTRIBUTION",
            "case_id": case_id, "capture_id": dashboard_capture.latest_capture_id(case_id)}


def _preview(scxw, ctx) -> dict:
    response = scxw.client.post(f"{API}/preview", json=ctx)
    assert response.status_code == 200, response.text
    return response.json()


def _stage(scxw, ctx, operation_id, data=HTML):
    response = scxw.client.put(f"{API}/{operation_id}/reports/html", params=ctx, content=data,
                               headers={"Content-Type": "application/octet-stream"})
    assert response.status_code == 200, response.text
    return response.json()


def _confirm(scxw, ctx, operation_id):
    return scxw.client.post(f"{API}/confirm", json={**ctx, "operation_id": operation_id, "report_formats": ["html"]})


def _status(scxw, ctx) -> dict:
    params = {key: value for key, value in ctx.items() if key != "capture_id"}
    response = scxw.client.get(f"{API}/status", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _job(scxw, ctx, operation_id) -> dict:
    params = {key: value for key, value in ctx.items() if key != "capture_id"}
    response = scxw.client.get(f"{API}/{operation_id}/job", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _working_snapshot(scxw) -> dict:
    prefix = scxw.path(f"{REQUEST}/Working")
    return {path: hashlib.sha256(node.content).hexdigest() for path, node in scxw.drive.nodes.items()
            if path.startswith(prefix) and node.kind == "file"}


def _designate(scxw, ctx) -> dict:
    plan = _preview(scxw, ctx)
    _stage(scxw, ctx, plan["operation_id"])
    confirmed = _confirm(scxw, ctx, plan["operation_id"])
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["state"] in {"QUEUED", "RUNNING"} and confirmed.json()["drive"]["state"] == "QUEUED"
    _settle()
    return plan


def test_final_is_built_on_the_drive_with_copy_within_and_complete_json_last(scxw):
    ctx = _final_ctx(scxw)
    working = _working_snapshot(scxw)
    plan = _preview(scxw, ctx)
    operation_id = plan["operation_id"]
    assert scxw.drive.writes() == []                                # preview writes nothing to the drive
    assert all(item.get("version_token") for item in plan["files"])
    staged = _stage(scxw, ctx, operation_id)
    assert staged["status"] == "STAGED" and scxw.drive.writes() == []
    confirmed = _confirm(scxw, ctx, operation_id)
    assert confirmed.status_code == 200, confirmed.text
    _settle()
    job = _job(scxw, ctx, operation_id)
    assert job["state"] == "COMPLETE" and job["record"]["verification"] == "DRIVE_UPLOAD", job
    cae = scxw.path(f"{FINAL}/CAE/{CASE_LABEL}/{operation_id}")
    report = scxw.path(f"{FINAL}/Report/{CASE_LABEL}/{operation_id}/{CASE_LABEL}_report.html")
    metadata = scxw.path(f"{FINAL}/.finalizations/{operation_id}")
    mirrored = f"{cae}/Drop/85qn80h_ref_organized/INDIVIDUAL/2_Face/{CSV}"
    assert scxw.drive.content(mirrored) == CSV_BYTES
    assert scxw.drive.content(report) == HTML
    assert json.loads(scxw.drive.content(f"{metadata}/plan.json"))["operation_id"] == operation_id
    complete = json.loads(scxw.drive.content(f"{metadata}/complete.json"))
    assert complete["status"] == "COMPLETE" and complete["storage"] == "scx" and complete["complete_signature"]
    assert {item["transfer_method"] for item in complete["files"]} == {"COPY_WITHIN"}
    writes = [rel for op, rel in scxw.drive.calls if op in {"upload_new", "copy_within"}]
    final_writes = [rel for rel in writes if "/designations/" not in rel]
    assert final_writes[-1].endswith("/complete.json")              # completion marker written last
    designation = scxw.path(f"{FINAL}/.finalizations/designations/00000001-{operation_id}.json")
    summary = json.loads(scxw.drive.content(designation))
    assert summary["designation_seq"] == 1 and summary["environments"]["DISTRIBUTION"]["final_id"] == operation_id
    status = _status(scxw, ctx)
    assert status["current_final"]["operation_id"] == operation_id and status["summary"]["state"] == "OK"
    assert status["storage"] == "scx" and status["latest"]["operation_id"] == operation_id
    assert _working_snapshot(scxw) == working                       # sources untouched
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM drive_locks").fetchone()[0] == 0
        row = conn.execute("SELECT status, complete_sha256 FROM finalization_operations WHERE operation_id=?",
                           [operation_id]).fetchone()
    assert row[0] == "COMPLETE" and row[1] == hashlib.sha256(scxw.drive.content(f"{metadata}/complete.json")).hexdigest()
    again = _confirm(scxw, ctx, operation_id)
    assert again.status_code == 200 and again.json()["state"] == "COMPLETE"


def test_copy_within_unsupported_falls_back_to_download_and_upload(scxw):
    """C7 unknown: a drive without server-side copy still gets the same Final, recorded as DOWNLOAD_UPLOAD."""
    ctx = _final_ctx(scxw)
    scxw.drive.copy_unsupported = True
    plan = _designate(scxw, ctx)
    job = _job(scxw, ctx, plan["operation_id"])
    assert job["state"] == "COMPLETE", job
    complete = json.loads(scxw.drive.content(scxw.path(f"{FINAL}/.finalizations/{plan['operation_id']}/complete.json")))
    assert {item["transfer_method"] for item in complete["files"]} == {"DOWNLOAD_UPLOAD"}
    mirrored = scxw.path(f"{FINAL}/CAE/{CASE_LABEL}/{plan['operation_id']}/Drop/85qn80h_ref_organized/INDIVIDUAL/3_Face/part.inc")
    assert scxw.drive.content(mirrored) == b"3_Face include\n"
    # three INTERNALs in a row switch to the fallback (review #7), then fallback only
    assert sum(1 for op, _ in scxw.drive.calls if op == "copy_within") == upload_queue.COPY_INTERNAL_STREAK
    assert not list(drive_gateway.current_settings().staging_dir.glob("xfer-*"))


def test_source_change_after_confirm_stops_the_batch_without_complete_json(scxw):
    ctx = _final_ctx(scxw)
    plan = _preview(scxw, ctx)
    _stage(scxw, ctx, plan["operation_id"])
    assert _confirm(scxw, ctx, plan["operation_id"]).status_code == 200
    scxw.drive.modify_file(scxw.path(f"{OPTION}/3_Face/part.inc"), b"edited after confirm\n")
    _settle()
    job = _job(scxw, ctx, plan["operation_id"])
    assert job["state"] == "FAILED" and job["error"]["code"] == "FINALIZATION_SOURCE_STALE", job
    metadata = scxw.path(f"{FINAL}/.finalizations/{plan['operation_id']}")
    assert f"{metadata}/complete.json" not in scxw.drive.nodes      # incomplete by definition
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM drive_locks").fetchone()[0] == 0
    assert _status(scxw, ctx)["current_final"] is None


def test_source_change_after_preview_refuses_confirm(scxw):
    ctx = _final_ctx(scxw)
    plan = _preview(scxw, ctx)
    _stage(scxw, ctx, plan["operation_id"])
    scxw.drive.touch(scxw.path(f"{OPTION}/2_Face/model.rad"))
    scxw.drive.modify_file(scxw.path(f"{OPTION}/2_Face/model.rad"), b"/INCLUDE part.inc\n* edited\n")
    response = _confirm(scxw, ctx, plan["operation_id"])
    assert response.status_code == 409 and response.json()["detail"]["code"] == "FINALIZATION_SOURCE_STALE", response.text
    assert scxw.drive.writes() == []


def test_second_final_of_the_same_request_waits_for_the_lock(scxw):
    ctx = _final_ctx(scxw)
    first = _preview(scxw, ctx)
    second = _preview(scxw, ctx)
    _stage(scxw, ctx, first["operation_id"])
    _stage(scxw, ctx, second["operation_id"])
    assert _confirm(scxw, ctx, first["operation_id"]).status_code == 200
    locked = _confirm(scxw, ctx, second["operation_id"])
    assert locked.status_code == 409 and locked.json()["detail"]["code"] == "FINALIZATION_LOCKED"
    _settle()
    retried = _confirm(scxw, ctx, second["operation_id"])
    assert retried.status_code == 200, retried.text
    _settle()
    status = _status(scxw, ctx)
    assert status["current_final"]["operation_id"] == second["operation_id"]
    assert [row["role"] for row in status["final_history"]] == ["CURRENT", "PREVIOUS"]
    designations = sorted(rel.rsplit("/", 1)[-1] for rel in scxw.drive.nodes if "/designations/" in rel)
    assert designations == [f"00000001-{first['operation_id']}.json", f"00000002-{second['operation_id']}.json"]
    newest = json.loads(scxw.drive.content(scxw.path(f"{FINAL}/.finalizations/designations/{designations[1]}")))
    assert newest["environments"]["DISTRIBUTION"]["previous_final_id"] == first["operation_id"]


def test_failed_final_is_retried_with_the_same_final_id(scxw):
    ctx = _final_ctx(scxw)
    plan = _preview(scxw, ctx)
    _stage(scxw, ctx, plan["operation_id"])
    report_dir = scxw.path(f"{FINAL}/Report/{CASE_LABEL}/{plan['operation_id']}")
    scxw.drive.fail_path[("upload_new", f"{report_dir}/{CASE_LABEL}_report.html")] = [ErrorCode.FORBIDDEN]
    assert _confirm(scxw, ctx, plan["operation_id"]).status_code == 200
    _settle()
    job = _job(scxw, ctx, plan["operation_id"])
    assert job["state"] == "FAILED" and job["error"]["code"] == "SPDM_FORBIDDEN"
    copies_before = sum(1 for op, _ in scxw.drive.calls if op == "copy_within")
    _stage(scxw, ctx, plan["operation_id"])                         # the window re-uploads the same bytes
    retried = _confirm(scxw, ctx, plan["operation_id"])
    assert retried.status_code == 200 and retried.json()["state"] in {"QUEUED", "RUNNING"}
    _settle()
    assert _job(scxw, ctx, plan["operation_id"])["state"] == "COMPLETE"
    assert sum(1 for op, _ in scxw.drive.calls if op == "copy_within") == copies_before   # CAE not copied again
    different = scxw.client.put(f"{API}/{plan['operation_id']}/reports/html", params=ctx, content=HTML + b"<!-- x -->",
                                headers={"Content-Type": "application/octet-stream"})
    assert different.status_code == 409


def test_stop_before_the_finish_hook_is_completed_on_the_next_start(scxw, monkeypatch):
    """The process stops after complete.json landed but before the Final was marked COMPLETE."""
    from app.services import case_finalization_drive

    ctx = _final_ctx(scxw)
    plan = _preview(scxw, ctx)
    _stage(scxw, ctx, plan["operation_id"])
    assert _confirm(scxw, ctx, plan["operation_id"]).status_code == 200
    with monkeypatch.context() as patched:
        patched.setattr(case_finalization_drive, "on_drive_batch_finished", lambda *args: None)
        _settle()
    assert _job(scxw, ctx, plan["operation_id"])["state"] == "RUNNING"     # all items DONE, Final not finished
    monkeypatch.setattr(upload_queue, "AUTOSTART", True)
    assert upload_queue.start() is True
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and _job(scxw, ctx, plan["operation_id"])["state"] != "COMPLETE":
        time.sleep(0.1)
    upload_queue.stop()
    assert _job(scxw, ctx, plan["operation_id"])["state"] == "COMPLETE"
    assert _status(scxw, ctx)["current_final"]["operation_id"] == plan["operation_id"]


def test_summary_repair_appends_a_new_designation_file(scxw):
    ctx = _final_ctx(scxw)
    plan = _designate(scxw, ctx)
    response = scxw.client.post(f"{API}/summary/repair", json={key: ctx[key] for key in ("project_id", "request_id", "environment", "case_id")})
    assert response.status_code == 200, response.text
    _settle()
    names = sorted(rel.rsplit("/", 1)[-1] for rel in scxw.drive.nodes if "/designations/" in rel)
    assert names == [f"00000001-{plan['operation_id']}.json", f"00000002-{plan['operation_id']}.json"]
    assert _status(scxw, ctx)["summary"]["state"] == "OK"


def test_request_progress_reads_final_state_from_the_db(scxw):
    ctx = _final_ctx(scxw)
    _designate(scxw, ctx)
    from app.services import case_finalization_drive

    with connect() as conn:
        record, names = case_finalization_drive.latest_completed(conn, project_id=ctx["project_id"],
                                                                 request_id=ctx["request_id"], environment="DISTRIBUTION")
    assert record["status"] == "COMPLETE" and names == [f"{CASE_LABEL}_report.html"]


# --- D3 independent review fixes ---------------------------------------------------------------------

def test_same_size_file_without_sha1_after_an_own_attempt_is_compared_by_content(scxw):
    """Review #1: never DONE on size alone — the target is downloaded and its sha256 compared."""
    project_id, request_id = _register(scxw)
    session = _drop(scxw, project_id, request_id, [("2_Face/same.csv", b"AAAA\n"), ("2_Face/twin.csv", b"CCCC\n")])
    other, twin = scxw.path(f"{OPTION}/2_Face/same.csv"), scxw.path(f"{OPTION}/2_Face/twin.csv")
    scxw.drive.fail_path[("upload_new", other)] = [ErrorCode.TIMEOUT]
    scxw.drive.fail_path[("upload_new", twin)] = [ErrorCode.TIMEOUT]
    _complete(scxw, session["session_id"])
    _settle()
    assert [state for _k, state, *_ in _items(session["session_id"])][-2:] == ["PENDING", "PENDING"]
    scxw.drive.add_file(other, b"BBBB\n")            # someone else: different content, same size
    scxw.drive.add_file(twin, b"CCCC\n")             # someone else: the very same bytes
    scxw.drive.stat_has_sha1 = False                 # C1/C9: drive stat without sha1
    _settle(now=_later())
    states = {target.rsplit("/", 1)[-1]: state for _k, state, target, _m, _a in _items(session["session_id"])}
    assert states == {"same.csv": "CONFLICT", "twin.csv": "DONE"}, states
    assert scxw.drive.content(other) == b"BBBB\n"   # never overwritten
    assert set(scxw.drive.downloads()) >= {other, twin}
    with connect() as conn:
        sha1 = conn.execute("SELECT result_sha1 FROM drive_upload_queue WHERE batch_id=? AND dst_name='twin.csv'",
                            [session["session_id"]]).fetchone()[0]
    assert sha1 == hashlib.sha1(b"CCCC\n").hexdigest()
    assert not list(drive_gateway.current_settings().staging_dir.glob("xfer-*"))


def test_same_name_file_above_the_verify_limit_needs_an_admin_decision(scxw, monkeypatch):
    project_id, request_id = _register(scxw)
    session = _drop(scxw, project_id, request_id, [("2_Face/big.csv", b"0123456789\n")])
    target = scxw.path(f"{OPTION}/2_Face/big.csv")
    _complete(scxw, session["session_id"])
    scxw.drive.add_file(target, b"0123456789\n")
    scxw.drive.stat_has_sha1 = False
    settings = drive_gateway.current_settings()
    monkeypatch.setattr(drive_gateway, "current_settings", lambda: replace(settings, verify_max_bytes=4))
    _settle()
    batch = _batch(scxw, session["session_id"])
    assert batch["errors"][0]["code"] == "SPDM_CONFLICT_UNVERIFIED" and _items(session["session_id"])[-1][1] == "CONFLICT"
    assert target not in scxw.drive.downloads()


def test_final_retry_after_the_reports_were_uploaded_needs_no_new_report(scxw):
    """Review #2: plan.json fails after the report is DONE; the panel's retry (no re-staging) completes."""
    ctx = _final_ctx(scxw)
    plan = _preview(scxw, ctx)
    operation_id = plan["operation_id"]
    _stage(scxw, ctx, operation_id)
    plan_json = scxw.path(f"{FINAL}/.finalizations/{operation_id}/plan.json")
    scxw.drive.fail_path[("upload_new", plan_json)] = [ErrorCode.FORBIDDEN]
    assert _confirm(scxw, ctx, operation_id).status_code == 200
    _settle()
    assert _job(scxw, ctx, operation_id)["state"] == "FAILED"
    report = upload_queue.staging_dir_for(operation_id) / "reports" / f"{CASE_LABEL}_report.html"
    assert report.exists()                          # a Final keeps its staging until the batch is DONE
    report.unlink()                                 # even without it: the uploaded report is not re-checked
    retried = _confirm(scxw, ctx, operation_id)
    assert retried.status_code == 200, retried.text
    _settle()
    assert _job(scxw, ctx, operation_id)["state"] == "COMPLETE"
    assert not upload_queue.staging_dir_for(operation_id).exists()
    reports = [rel for op, rel in scxw.drive.calls if op == "upload_new" and rel.endswith("_report.html")]
    assert len(reports) == 1                        # uploaded once


def test_a_failed_result_recording_is_retried_and_a_lost_one_is_swept_back(scxw, monkeypatch):
    """Review #3: the DB write after a drive call fails — retried; still failing → RUNNING → sweep → stat → DONE."""
    monkeypatch.setattr(upload_queue, "RECORD_RETRY_DELAYS", (0.0, 0.0))
    project_id, request_id = _register(scxw)
    session = _drop(scxw, project_id, request_id, [("2_Face/hiccup.csv", b"hiccup\n"), ("2_Face/lost2.csv", b"lost\n")])
    _complete(scxw, session["session_id"])
    real = upload_queue._apply
    failures = {"left": 1}

    def flaky(conn, item, outcome):
        if item.kind == "FILE" and failures["left"]:
            failures["left"] -= 1
            raise RuntimeError("db hiccup after the drive call")
        return real(conn, item, outcome)

    monkeypatch.setattr(upload_queue, "_apply", flaky)
    assert upload_queue.process_next() and upload_queue.process_next()     # MKDIR?, first FILE (one retry)
    _settle()
    assert all(state == "DONE" for _k, state, *_ in _items(session["session_id"]))
    session = _drop(scxw, project_id, request_id, [("2_Face/stuck.csv", b"stuck\n")])
    _complete(scxw, session["session_id"])
    failures["left"] = 99
    with pytest.raises(RuntimeError):
        _settle()
    assert _items(session["session_id"])[-1][1] == "RUNNING"
    failures["left"] = 0
    assert upload_queue.sweep()["resumed"] == 1
    _settle(now=_later())
    target = scxw.path(f"{OPTION}/2_Face/stuck.csv")
    assert _items(session["session_id"])[-1][1] == "DONE"
    assert sum(1 for op, rel in scxw.drive.calls if op == "upload_new" and rel == target) == 1   # stat, no re-upload


def test_a_failed_finish_hook_is_completed_by_the_sweep(scxw, monkeypatch):
    from app.services import case_finalization_drive

    ctx = _final_ctx(scxw)
    plan = _preview(scxw, ctx)
    _stage(scxw, ctx, plan["operation_id"])
    assert _confirm(scxw, ctx, plan["operation_id"]).status_code == 200
    real = case_finalization_drive._finalization_finished

    def broken(*args):
        raise RuntimeError("DB down while finishing")

    monkeypatch.setattr(case_finalization_drive, "_finalization_finished", broken)
    _settle()
    assert _job(scxw, ctx, plan["operation_id"])["state"] == "RUNNING"
    monkeypatch.setattr(case_finalization_drive, "_finalization_finished", real)
    assert upload_queue.sweep()["publishing"] == 1
    assert _job(scxw, ctx, plan["operation_id"])["state"] == "COMPLETE"
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM drive_locks").fetchone()[0] == 0


def test_admin_cancel_runs_the_finish_hook_only_after_commit(scxw):
    """Review #4: cancel_item registers nothing; the router asks for the finish hook after its commit."""
    ctx = _final_ctx(scxw)
    plan = _preview(scxw, ctx)
    operation_id = plan["operation_id"]
    _stage(scxw, ctx, operation_id)
    scxw.drive.fail_next["mkdirs"] = ErrorCode.AUTH_REQUIRED
    assert _confirm(scxw, ctx, operation_id).status_code == 200
    _settle()
    with connect() as conn:
        blocked = conn.execute("SELECT id FROM drive_upload_queue WHERE batch_id=? AND state='BLOCKED'",
                               [operation_id]).fetchone()[0]
        upload_queue.cancel_item(conn, blocked)
        conn.execute("UPDATE drive_upload_queue SET state='BLOCKED' WHERE id=?", [blocked])   # "rolled back"
    assert operation_id not in upload_queue._pending_finish
    cancelled = scxw.client.post(f"/api/admin/drive/queue/{blocked}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert operation_id in upload_queue._pending_finish
    _settle()
    job = _job(scxw, ctx, operation_id)
    assert job["state"] == "FAILED" and job["drive"]["state"] == "CANCELLED", job
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM drive_locks").fetchone()[0] == 0


def test_concurrent_summary_repairs_queue_one_designation_file(scxw):
    """Review #5: designation seq allocation is serialized per request."""
    import threading

    from app.services import case_finalization_drive

    ctx = _final_ctx(scxw)
    plan = _designate(scxw, ctx)
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def repair() -> None:
        try:
            barrier.wait(10)
            with connect() as conn:
                case_finalization_drive.repair_summary(conn, project_id=ctx["project_id"], request_id=ctx["request_id"],
                                                       environment="DISTRIBUTION", case_id=ctx["case_id"], actor="tester")
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    threads = [threading.Thread(target=repair) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    assert errors == []
    with connect() as conn:
        batches = conn.execute("SELECT count(DISTINCT batch_id) FROM drive_upload_queue WHERE origin='final_designation' "
                               "AND origin_ref=?", [plan["operation_id"]]).fetchone()[0]
    assert batches == 2                              # the first designation + one repair
    _settle()
    names = sorted(rel.rsplit("/", 1)[-1] for rel in scxw.drive.nodes if "/designations/" in rel)
    assert names == [f"00000001-{plan['operation_id']}.json", f"00000002-{plan['operation_id']}.json"]


def test_the_final_lock_is_held_while_its_final_is_publishing(scxw):
    """Review #6: an expired lock of a PUBLISHING (paused) Final is not taken over; admin retry needs the lock."""
    ctx = _final_ctx(scxw)
    first, second = _preview(scxw, ctx), _preview(scxw, ctx)
    _stage(scxw, ctx, first["operation_id"])
    _stage(scxw, ctx, second["operation_id"])
    scxw.drive.fail_next["mkdirs"] = ErrorCode.FORBIDDEN
    assert _confirm(scxw, ctx, first["operation_id"]).status_code == 200
    _settle()
    assert _job(scxw, ctx, first["operation_id"])["state"] == "FAILED"
    scxw.drive.fail_next["mkdirs"] = ErrorCode.AUTH_REQUIRED
    assert _confirm(scxw, ctx, second["operation_id"]).status_code == 200
    _settle()
    assert _job(scxw, ctx, second["operation_id"])["drive"]["state"] == "PAUSED"
    with connect() as conn:   # 31 minutes later, credentials not registered yet
        conn.execute("UPDATE drive_locks SET expires_at=?", [datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)])
        failed_item = conn.execute("SELECT id FROM drive_upload_queue WHERE batch_id=? AND state='FAILED'",
                                   [first["operation_id"]]).fetchone()[0]
    locked = _confirm(scxw, ctx, first["operation_id"])
    assert locked.status_code == 409 and locked.json()["detail"]["code"] == "FINALIZATION_LOCKED", locked.text
    admin = scxw.client.post(f"/api/admin/drive/queue/{failed_item}/retry")
    assert admin.status_code == 409 and admin.json()["detail"]["code"] == "FINALIZATION_LOCKED", admin.text
    assert scxw.client.put("/api/admin/drive/credentials", content=_bundle()).status_code == 200
    _settle()
    assert _job(scxw, ctx, second["operation_id"])["state"] == "COMPLETE"
    retried = scxw.client.post(f"/api/admin/drive/queue/{failed_item}/retry")
    assert retried.status_code == 200, retried.text
    _settle()
    assert _job(scxw, ctx, first["operation_id"])["state"] == "COMPLETE"
    status = _status(scxw, ctx)
    assert status["current_final"]["operation_id"] == first["operation_id"]
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM drive_locks").fetchone()[0] == 0


def test_a_single_copy_within_internal_falls_back_for_that_item_only(scxw):
    """Review #7: one INTERNAL is not "unsupported"; three in a row switch to download → upload."""
    ctx = _final_ctx(scxw)
    plan = _preview(scxw, ctx)
    first = plan["files"][0]["source_relative_path"]
    scxw.drive.fail_path[("copy_within", scxw.path(first))] = [ErrorCode.INTERNAL]
    _stage(scxw, ctx, plan["operation_id"])
    assert _confirm(scxw, ctx, plan["operation_id"]).status_code == 200
    _settle()
    job = _job(scxw, ctx, plan["operation_id"])
    assert job["state"] == "COMPLETE" and job["drive"]["transfer_methods"] == {"DOWNLOAD_UPLOAD": 1, "COPY_WITHIN": len(plan["files"]) - 1}, job
    assert upload_queue._worker.copy_fallback_until == 0.0 and not upload_queue._worker.copy_unsupported


def test_fallback_download_waits_when_server_staging_is_full(scxw, monkeypatch):
    from app.services.storage import server_local

    ctx = _final_ctx(scxw)
    scxw.drive.copy_unsupported = True
    plan = _preview(scxw, ctx)
    _stage(scxw, ctx, plan["operation_id"])
    assert _confirm(scxw, ctx, plan["operation_id"]).status_code == 200
    downloads = len(scxw.drive.downloads())
    with monkeypatch.context() as patched:
        patched.setattr(server_local, "disk_free", lambda path: 1024)
        _settle()
    waiting = [row for row in _items(plan["operation_id"]) if row[0] == "COPY" and row[1] == "PENDING"]
    assert waiting and len(scxw.drive.downloads()) == downloads     # nothing downloaded without room
    with connect() as conn:
        codes = {row[0] for row in conn.execute("SELECT last_error_code FROM drive_upload_queue WHERE batch_id=? AND kind='COPY' "
                                                "AND last_error_code IS NOT NULL", [plan["operation_id"]]).fetchall()}
    assert codes == {"DRIVE_STAGING_FULL"}
    _settle(now=_later())
    assert _job(scxw, ctx, plan["operation_id"])["state"] == "COMPLETE"


def test_staging_of_stopped_batches_and_unfinished_finals_is_removed_after_the_age_limit(scxw):
    """Review #9: hourly cleanup; never the drive."""
    import os

    ctx = _final_ctx(scxw)
    old_final = _preview(scxw, ctx)["operation_id"]
    new_final = _preview(scxw, ctx)["operation_id"]
    _stage(scxw, ctx, old_final)
    _stage(scxw, ctx, new_final)
    project_id, request_id = ctx["project_id"], ctx["request_id"]
    session = _drop(scxw, project_id, request_id, [("2_Face/keep.csv", b"mine\n")])
    _complete(scxw, session["session_id"])
    scxw.drive.add_file(scxw.path(f"{OPTION}/2_Face/keep.csv"), b"theirs\n")
    _settle()
    assert _items(session["session_id"])[-1][1] == "CONFLICT"
    staging = drive_gateway.current_settings().staging_dir
    folders = {name: upload_queue.staging_dir_for(name) for name in (old_final, new_final, session["session_id"])}
    two_days = time.time() - 2 * 24 * 3600
    for folder in folders.values():
        assert folder.exists()
        os.utime(folder, (two_days, two_days))
    stale = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=8)
    with connect() as conn:
        conn.execute("UPDATE finalization_operations SET updated_at=? WHERE operation_id=?", [stale, old_final])
    writes = list(scxw.drive.writes())
    assert upload_queue.sweep(cleanup=True)["staging_removed"] == 1
    assert not folders[old_final].exists() and folders[new_final].exists() and folders[session["session_id"]].exists()
    with connect() as conn:
        conn.execute("UPDATE drive_upload_queue SET updated_at=? WHERE batch_id=?", [stale, session["session_id"]])
    assert upload_queue.sweep(cleanup=True)["staging_removed"] == 1
    assert not folders[session["session_id"]].exists() and folders[new_final].exists()
    assert scxw.drive.writes() == writes and staging.exists()


def test_staging_of_halted_batches_ages_out_from_their_last_activity(scxw):
    """Re-review #1: a halt_on_error stop leaves later items PENDING; such a batch waits for an admin, it is not live."""
    import os

    ctx = _final_ctx(scxw)
    project_id, request_id = ctx["project_id"], ctx["request_id"]
    drops = []
    for scene in (NEW_SCENE, "6_Face"):
        session = _drop(scxw, project_id, request_id, [(f"{scene}/{CSV}", CSV_BYTES)])
        scxw.drive.fail_next["mkdirs"] = ErrorCode.FORBIDDEN
        _complete(scxw, session["session_id"])
        _settle()
        assert [row[:2] for row in _items(session["session_id"])] == [("MKDIR", "FAILED"), ("FILE", "PENDING")]
        drops.append(session["session_id"])
    finals = []
    for _ in range(2):
        operation_id = _preview(scxw, ctx)["operation_id"]
        _stage(scxw, ctx, operation_id)
        scxw.drive.fail_next["mkdirs"] = ErrorCode.FORBIDDEN
        assert _confirm(scxw, ctx, operation_id).status_code == 200
        _settle()
        rows = _items(operation_id)
        assert rows[0][:2] == ("MKDIR", "FAILED") and all(row[1] == "PENDING" for row in rows[1:])
        assert _job(scxw, ctx, operation_id)["state"] == "FAILED"
        finals.append(operation_id)
    open_batch = _drop(scxw, project_id, request_id, [("2_Face/open.csv", b"open\n")])["session_id"]
    _complete(scxw, open_batch)                                       # queued, not run yet (open, not halted)
    folders = {name: upload_queue.staging_dir_for(name) for name in (*drops, *finals, open_batch)}
    two_days = time.time() - 2 * 24 * 3600
    for folder in folders.values():
        assert folder.exists()
        os.utime(folder, (two_days, two_days))
    stale = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=8)
    with connect() as conn:
        for name in (drops[0], finals[0], open_batch):
            conn.execute("UPDATE drive_upload_queue SET updated_at=? WHERE batch_id=?", [stale, name])
        conn.execute("UPDATE finalization_operations SET updated_at=? WHERE operation_id=?", [stale, finals[0]])
    writes = list(scxw.drive.writes())
    assert upload_queue.sweep(cleanup=True)["staging_removed"] == 2
    assert not folders[drops[0]].exists() and not folders[finals[0]].exists()
    assert folders[drops[1]].exists() and folders[finals[1]].exists() and folders[open_batch].exists()
    assert scxw.drive.writes() == writes


def test_final_retry_rewrites_a_removed_staged_plan_from_the_database(scxw):
    """Re-review #2: staging cleanup removed the stopped Final's folder; retry rebuilds plan.json (same sha256)."""
    from app.services.storage import server_local

    ctx = _final_ctx(scxw)
    operation_id = _preview(scxw, ctx)["operation_id"]
    _stage(scxw, ctx, operation_id)
    scxw.drive.fail_next["mkdirs"] = ErrorCode.FORBIDDEN
    assert _confirm(scxw, ctx, operation_id).status_code == 200
    _settle()
    assert _job(scxw, ctx, operation_id)["state"] == "FAILED"
    server_local.remove_tree(upload_queue.staging_dir_for(operation_id))
    _stage(scxw, ctx, operation_id)
    with connect() as conn:                                           # a tampered record cannot be satisfied
        recorded = conn.execute("SELECT sha256 FROM drive_upload_queue WHERE batch_id=? AND dst_name='plan.json'",
                                [operation_id]).fetchone()[0]
        conn.execute("UPDATE drive_upload_queue SET sha256=? WHERE batch_id=? AND dst_name='plan.json'",
                     ["0" * 64, operation_id])
    refused = _confirm(scxw, ctx, operation_id)
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "FINALIZATION_PLAN_STAGE_INVALID", refused.text
    assert not (upload_queue.staging_dir_for(operation_id) / "plan.json").exists()
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM drive_locks").fetchone()[0] == 0
        conn.execute("UPDATE drive_upload_queue SET sha256=? WHERE batch_id=? AND dst_name='plan.json'",
                     [recorded, operation_id])
    assert _job(scxw, ctx, operation_id)["state"] == "FAILED"
    retried = _confirm(scxw, ctx, operation_id)
    assert retried.status_code == 200, retried.text
    _settle()
    assert _job(scxw, ctx, operation_id)["state"] == "COMPLETE"
    plan_file = scxw.path(f"{FINAL}/.finalizations/{operation_id}/plan.json")
    assert hashlib.sha256(scxw.drive.content(plan_file)).hexdigest() == recorded


def test_admin_retry_of_a_failed_final_publishes_with_its_lock_in_one_transaction(scxw):
    """Re-review #3: the retried Final is PUBLISHING with its lock; a refused retry changes neither."""
    ctx = _final_ctx(scxw)
    operation_id = _preview(scxw, ctx)["operation_id"]
    _stage(scxw, ctx, operation_id)
    report = scxw.path(f"{FINAL}/Report/{CASE_LABEL}/{operation_id}/{CASE_LABEL}_report.html")
    scxw.drive.fail_path[("upload_new", report)] = [ErrorCode.FORBIDDEN]
    assert _confirm(scxw, ctx, operation_id).status_code == 200
    _settle()
    assert _job(scxw, ctx, operation_id)["state"] == "FAILED"

    def state() -> tuple:
        with connect() as conn:
            status = conn.execute("SELECT status FROM finalization_operations WHERE operation_id=?",
                                  [operation_id]).fetchone()[0]
            return status, [str(row[0]) for row in conn.execute("SELECT owner FROM drive_locks").fetchall()]

    with connect() as conn:
        done_item = conn.execute("SELECT id FROM drive_upload_queue WHERE batch_id=? AND state='DONE' ORDER BY seq LIMIT 1",
                                 [operation_id]).fetchone()[0]
        failed_item = conn.execute("SELECT id FROM drive_upload_queue WHERE batch_id=? AND state='FAILED'",
                                   [operation_id]).fetchone()[0]
    refused = scxw.client.post(f"/api/admin/drive/queue/{done_item}/retry")
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "DRIVE_QUEUE_ITEM_STATE", refused.text
    assert state() == ("FAILED", [])                                 # lock and status rolled back together
    scxw.drive.fail_path[("upload_new", report)] = [ErrorCode.FORBIDDEN]
    assert scxw.client.post(f"/api/admin/drive/queue/{failed_item}/retry").status_code == 200
    assert state() == ("PUBLISHING", [operation_id])
    _settle()
    assert state() == ("FAILED", [])                                 # the finish hook settled it and released the lock
    assert scxw.client.post(f"/api/admin/drive/queue/{failed_item}/retry").status_code == 200
    _settle()
    assert _job(scxw, ctx, operation_id)["state"] == "COMPLETE" and state() == ("COMPLETE", [])


def test_a_drop_completion_that_did_not_commit_can_be_completed_again(scxw, monkeypatch):
    """Review #10: the session leaves the registry only after the router committed."""
    from app.routers import result_registration as router

    project_id, request_id = _register(scxw)
    session = _drop(scxw, project_id, request_id, [("2_Face/commit.csv", b"commit\n")])
    real = router._drop_audit

    def failing(request, action, detail, **kwargs):
        if action == "RESULT_DROP_UPLOAD_QUEUED":
            kwargs["conn"].execute("DELETE FROM drive_upload_queue WHERE batch_id=?", [session["session_id"]])  # rollback
            raise RuntimeError("commit failed")
        return real(request, action, detail, **kwargs)

    monkeypatch.setattr(router, "_drop_audit", failing)
    with pytest.raises(RuntimeError):
        scxw.client.post(f"{REG}/drop-uploads/{session['session_id']}/complete")
    monkeypatch.setattr(router, "_drop_audit", real)
    assert result_drop_upload.read_session(session["session_id"], _user_id(scxw))["state"] == "UPLOADING"
    result = _complete(scxw, session["session_id"])
    assert result["state"] == "QUEUED"
    _settle()
    assert scxw.drive.content(scxw.path(f"{OPTION}/2_Face/commit.csv")) == b"commit\n"
    gone = scxw.client.post(f"{REG}/drop-uploads/{session['session_id']}/complete")
    assert gone.status_code == 404


def _user_id(scxw) -> str:
    return scxw.client.get("/api/auth/me").json()["id"]


# --- gates and static rules --------------------------------------------------------------------------

def test_features_without_a_drive_design_stay_disabled_with_writes_on(scxw):
    for method, route in (("POST", "/api/result-registration/folders/prepare"),
                          ("POST", "/api/result-registration/drafts/x/publish"),
                          ("POST", "/api/load-cases/x/storage/upload"),
                          ("POST", "/api/storage/refresh")):
        response = scxw.client.request(method, route, json={})
        assert response.status_code == 409 and response.json()["detail"]["code"] == "DRIVE_WRITE_DISABLED", (route, response.text)
    status = scxw.client.get("/api/drive/status").json()
    assert (status["writes_enabled"], status["writes_available"]) == (True, True)


DRIVE_WRITE_METHODS = {"upload_new", "mkdirs", "copy_within"}


def _gateway_calls(path: Path) -> set[str]:
    called = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) \
                and node.func.value.id == "gateway":
            called.add(node.func.attr)
    return called


def test_only_the_queue_writes_to_the_drive():
    """The queue is the only drive writer; it uses mkdirs/copy_within/upload_new (+ stat/download_to)."""
    app_dir = BACKEND / "app"
    assert _gateway_calls(app_dir / "services" / "drive" / "upload_queue.py") == {
        "stat", "mkdirs", "copy_within", "upload_new", "download_to"}
    for path in app_dir.rglob("*.py"):
        if path.name == "upload_queue.py":
            continue
        assert not (_gateway_calls(path) & DRIVE_WRITE_METHODS), path


def test_duckdb_bootstrap_adds_the_write_path_tables_to_an_existing_database():
    from app.config import database_settings
    from app.database import initialize_database

    if database_settings().backend != "duckdb":
        pytest.skip("DuckDB bootstrap only (drops the tables; PostgreSQL uses migration 0038)")

    with connect() as conn:
        for table in ("drive_upload_queue", "drive_locks", "finalization_operations"):
            conn.execute(f"DROP TABLE {table}")
        conn.execute("INSERT INTO projects(id,name,product_name,description,created_at) VALUES "
                     "('drive-d3-upgrade','keep','p','d',CURRENT_TIMESTAMP)")
    initialize_database()
    with connect() as conn:
        tables = {row[0] for row in conn.execute("SELECT table_name FROM information_schema.tables").fetchall()}
        kept = conn.execute("SELECT name FROM projects WHERE id='drive-d3-upgrade'").fetchone()
        # NULL designation_seq values never collide (unique per request only when assigned).
        for index in range(2):
            conn.execute("INSERT INTO finalization_operations (operation_id,root_key,project_id,request_id,environment,"
                         "case_id,capture_id,status,plan_json,created_by,created_at,updated_at) VALUES "
                         "(?,'k','p','r','DISTRIBUTION','c','x','PLANNED','{}','u',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                         [f"{index:032x}"])
    assert {"drive_upload_queue", "drive_locks", "finalization_operations"} <= tables and kept == ("keep",)


def test_migration_0038_is_additive_and_grants_the_app_role():
    text = (BACKEND / "migrations" / "versions" / "0038_drive_write_path.py").read_text(encoding="utf-8")
    upgrade = text.split("def downgrade")[0]
    for table in ("drive_upload_queue", "drive_locks", "finalization_operations"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in upgrade
    for forbidden in ("DROP TABLE", "DELETE FROM", "TRUNCATE", "ALTER TABLE"):
        assert forbidden not in upgrade
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON {table}" in upgrade
