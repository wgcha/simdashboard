"""Issue #51 (real SCX drive field report): a USAGE request registered from the drive with an empty
``Working`` that the user filled by hand later, sync/refresh logging, worker stderr and the admin
queue list editing.

Everything runs against the in-memory fake adapter (``drive_fakes``), an isolated DuckDB test
database and synthetic folders; no real SDK, drive, user database or running service is touched.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import time
import types
import unicodedata
import uuid
from pathlib import Path

import pytest

from app.database_connection import connect
from app.services import folder_auto_discovery, folder_auto_sync
from app.services.drive import config as drive_config
from app.services.drive import gateway as drive_gateway
from app.services.drive import reads as drive_reads
from app.services.drive import upload_queue, worker_log

from drive_fakes import ErrorCode
from test_drive_reads import CSV, CSV_BYTES, ENV, OPTION, Scx, _register
from test_drive_writes import _confirm, _final_ctx, _items, _preview, _settle, _stage, scxw  # noqa: F401  (fixture)

pytestmark = pytest.mark.duckdb_integration

QUEUE = "/api/admin/drive/queue"
BIG = 33 * 1024 * 1024      # above the 32 MiB content-fingerprint and capture-asset bound


def _discover(scx: Scx, monkeypatch, request: str) -> str:
    folder_auto_discovery.reset_for_tests()
    monkeypatch.setattr(folder_auto_discovery, "MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(folder_auto_discovery, "FORCE_MIN_INTERVAL_SECONDS", 0.0)
    result = scx.post(ENV + "/discover", {"force": True})
    created = [item for item in result["created_requests"] if item["name"] == request.rsplit("/", 1)[-1]]
    assert created, result
    return created[0]["id"]


def _usage_request(scx: Scx, monkeypatch) -> tuple[str, str, str]:
    """A USAGE request whose folder SPDM created with an empty Working (registered without Cases)."""
    request = f"PROJ-{uuid.uuid4().hex[:6]}_R95J 개발 과제/WR-00000801_사용 환경 시뮬레이션"
    scx.drive.add_dir(scx.path(f"{request}/Working"))
    request_id = _discover(scx, monkeypatch, request)
    with connect() as conn:
        project_id = conn.execute("SELECT project_id FROM analysis_requests WHERE id=?", [request_id]).fetchone()[0]
    return str(project_id), request_id, request


def _usage_sync(scx: Scx, project_id: str, request_id: str, **extra) -> dict:
    return scx.post(ENV + "/sync", {"project_id": project_id, "request_id": request_id, "environment": "USAGE", **extra})


def _catalog(scx: Scx, request_id: str) -> dict:
    response = scx.client.get("/api/dashboard/catalog", params={"request_id": request_id, "environment": "USAGE"})
    assert response.status_code == 200, response.text
    return response.json()


# --- 1. the field case -------------------------------------------------------------------------------

def test_hand_made_case_with_a_large_video_appears_with_results_in_one_sync(scxw, monkeypatch):
    project_id, request_id, request = _usage_request(scxw, monkeypatch)
    first = _usage_sync(scxw, project_id, request_id)
    assert first["status"] in {"REFRESHED", "UNCHANGED"}, first
    case = f"{request}/Working/Assy_Motion_75R9J_Set0907_Stand0907_Inner"
    scxw.add(f"{case}/Slope_Angle/{CSV}", CSV_BYTES)
    scxw.add(f"{case}/Slope_Angle/angle.png", b"\x89PNG\r\n\x1a\n0000")
    # Before the fix the 32 MiB content-fingerprint bound failed every sync (FOLDER_SCHEMA_CONTENT_FILE_LIMIT),
    # although drive content fingerprints never read the file.
    scxw.add(f"{case}/Slope_Angle/motion.mp4", b"\0" * BIG)
    synced = _usage_sync(scxw, project_id, request_id)
    assert (synced["status"], synced["code"]) == ("REFRESHED", None), synced
    catalog = _catalog(scxw, request_id)
    [found] = [item for item in catalog["cases"] if item["label"] == "Assy_Motion_75R9J_Set0907_Stand0907_Inner"]
    # New Cases are captured by the refresh (before: UNCAPTURED, "결과 없음" forever).
    assert found["match_status"] == "CAPTURED" and found["capture_count"] == 1, found
    assert "Slope_Angle" in {scene["label"] for scene in catalog["scenes"]}
    with connect() as conn:
        assets = {str(row[0]).rsplit("/", 1)[-1] for row in conn.execute(
            "SELECT a.relative_path FROM dashboard_assets a JOIN dashboard_captures c ON c.id=a.capture_id "
            "JOIN dashboard_cases d ON d.id=c.case_id WHERE d.request_id=?", [request_id]).fetchall()}
    assert {CSV, "angle.png"} <= assets and "motion.mp4" not in assets   # the video is reported, not stored
    assert scxw.drive.downloads().count(scxw.path(f"{case}/Slope_Angle/motion.mp4")) == 0
    quiet = _usage_sync(scxw, project_id, request_id)
    assert (quiet["status"], quiet["check_mode"]) == ("UNCHANGED", "QUICK"), quiet
    environments = scxw.client.get("/api/dashboard/result-environments",
                                   params={"project_id": project_id, "request_id": request_id})
    if environments.status_code == 200:
        assert "USAGE" in environments.json()["environments"]


def test_case_folders_made_step_by_step_are_followed(scxw, monkeypatch):
    project_id, request_id, request = _usage_request(scxw, monkeypatch)
    case = f"{request}/Working/Assy_Motion_A"
    scxw.drive.add_dir(scxw.path(case))
    assert _usage_sync(scxw, project_id, request_id)["status"] == "REFRESHED"
    scxw.drive.add_dir(scxw.path(f"{case}/Slope_Angle"))
    assert _usage_sync(scxw, project_id, request_id)["status"] == "REFRESHED"
    scxw.add(f"{case}/Slope_Angle/{CSV}", CSV_BYTES)
    assert _usage_sync(scxw, project_id, request_id)["status"] == "REFRESHED"
    [found] = _catalog(scxw, request_id)["cases"]
    assert found["match_status"] == "CAPTURED", found


def test_partial_structures_keep_the_scenes_that_exist(scxw, monkeypatch):
    project_id, request_id, request = _usage_request(scxw, monkeypatch)
    working = f"{request}/Working"
    scxw.drive.add_dir(scxw.path(f"{working}/Assy_Empty"))                 # Case without anything yet
    scxw.drive.add_dir(scxw.path(f"{working}/Assy_A/Slope_Angle"))          # evaluation still empty
    scxw.add(f"{working}/Assy_A/Drop_Test/{CSV}", CSV_BYTES)               # evaluation with results
    scxw.add(f"{working}/Assy_B/{CSV}", CSV_BYTES)                         # files straight in the Case
    scxw.add(f"{working}/Assy_D/Slope_Angle/only.mp4", b"\0" * BIG)        # only an oversized video
    synced = _usage_sync(scxw, project_id, request_id)
    assert synced["status"] == "REFRESHED", synced
    catalog = _catalog(scxw, request_id)
    cases = {item["label"]: item["match_status"] for item in catalog["cases"]}
    assert set(cases) == {"Assy_Empty", "Assy_A", "Assy_B", "Assy_D"} and cases["Assy_A"] == "CAPTURED", cases
    assert {"Slope_Angle", "Drop_Test"} <= {scene["label"] for scene in catalog["scenes"]}


def test_local_mode_keeps_the_content_fingerprint_bound(tmp_path):
    """Local mode reads the bytes for the content fingerprint, so its read bound is unchanged."""
    from app.services import folder_schema_resolver as resolver

    (tmp_path / "big.mp4").write_bytes(b"\0" * BIG)
    scanned = {"nodes": [], "file_state": [{"relative_path": "big.mp4", "size": BIG, "modified_ns": 1}]}
    with pytest.raises(resolver.FolderSchemaError) as refused:
        resolver.scan_fingerprints(scanned, tmp_path)
    assert refused.value.code == "FOLDER_SCHEMA_CONTENT_FILE_LIMIT"


# --- 2. logging -----------------------------------------------------------------------------------------

def test_failed_sync_logs_one_warning_per_scope_and_code(scxw, monkeypatch, caplog):
    project_id, request_id, request = _usage_request(scxw, monkeypatch)
    _usage_sync(scxw, project_id, request_id)
    scxw.drive.fail_always["list_dir"] = ErrorCode.TIMEOUT
    caplog.set_level(logging.INFO)
    failed = _usage_sync(scxw, project_id, request_id)
    assert (failed["status"], failed["code"]) == ("FAILED", "DRIVE_TIMEOUT"), failed

    def sync_lines() -> list[str]:
        return [record.getMessage() for record in caplog.records
                if record.name == "app.services.folder_auto_sync" and record.levelno == logging.WARNING]

    [line] = sync_lines()
    assert f"project={project_id}" in line and f"request={request_id}" in line and "environment=USAGE" in line
    assert "code=DRIVE_TIMEOUT" in line and "SpdmStorageError" in line, line
    reads = [record.getMessage() for record in caplog.records
             if record.name == "app.services.drive.reads" and record.levelno == logging.WARNING]
    assert any("list_dir failed" in text and "code=DRIVE_TIMEOUT" in text and request.split("/")[0] in text
               for text in reads), reads
    for _ in range(3):                                   # the 60 s polling does not repeat the line
        _usage_sync(scxw, project_id, request_id)
    assert len(sync_lines()) == 1
    monkeypatch.setattr(folder_auto_sync, "LOG_INTERVAL_SECONDS", 0.0)
    _usage_sync(scxw, project_id, request_id)
    assert len(sync_lines()) == 2
    assert all("eyJ" not in record.getMessage() for record in caplog.records)
    scxw.drive.fail_always.clear()


def test_log_outcome_masks_secrets_and_ignores_success(caplog):
    folder_auto_sync.reset_for_tests()
    caplog.set_level(logging.WARNING, logger="app.services.folder_auto_sync")
    assert not folder_auto_sync.log_outcome("p", "r", "USAGE", {"status": "REFRESHED"})
    error = ValueError("Bearer eyJabc.def leaked")
    assert folder_auto_sync.log_outcome("p", "r", "usage", {"status": "CONFLICT", "code": "X", "message": "m"}, error)
    assert not folder_auto_sync.log_outcome("p", "r", "USAGE", {"status": "CONFLICT", "code": "X"})
    assert folder_auto_sync.log_outcome("p", "r", "USAGE", {"status": "FAILED", "code": "Y"})
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "eyJabc" not in text and "Bearer ***" in text and "code=X" in text and "code=Y" in text
    folder_auto_sync.reset_for_tests()


def test_queue_drive_failure_is_logged_with_op_rel_and_code(scxw, monkeypatch, caplog):
    project_id, request_id = _register(scxw)
    monkeypatch.setattr(upload_queue, "wait_batch", lambda batch_id, timeout, **_: (_settle(), None)[1])
    scxw.drive.fail_next["mkdirs"] = ErrorCode.NOT_FOUND
    caplog.set_level(logging.WARNING, logger="app.services.drive.upload_queue")
    response = scxw.client.post("/api/result-registration/drop-target/folders", json={
        "project_id": project_id, "request_id": request_id, "environment": "DISTRIBUTION",
        "parent_relative_path": OPTION, "name": "8_Face", "confirm": True})
    assert response.status_code in {201, 409}, response.text
    _settle()
    lines = [record.getMessage() for record in caplog.records if record.name == "app.services.drive.upload_queue"]
    assert any("mkdirs failed" in line and f"rel={OPTION}/8_Face" in line and "code=NOT_FOUND" in line
               for line in lines), lines


@pytest.mark.parametrize("raw", ["SPDM/한글 루트/", "/SPDM/한글 루트", "SPDM/한글 루트"])
def test_queue_and_reads_compose_the_same_drive_path(monkeypatch, raw):
    monkeypatch.setenv("SIMDASH_SCX_DRIVE_ROOT", raw)
    monkeypatch.setenv("SIMDASH_DRIVE_GATEWAY", "none")
    root = drive_config.drive_settings().spdm_root if drive_config.drive_mode() == "scx" else \
        drive_config.validate_drive_rel_path(raw.strip("/"), allow_empty=False)
    settings = types.SimpleNamespace(spdm_root=root)
    monkeypatch.setattr(drive_gateway, "current_settings", lambda: settings)
    name = unicodedata.normalize("NFD", "PROJ 개발/WR-1_사용 환경/Working/새 폴더")
    expected = drive_reads.drive_path(root, drive_reads.check_rel(name))
    assert upload_queue._drive_path(name) == expected == f"SPDM/한글 루트/{unicodedata.normalize('NFC', name)}"
    with pytest.raises(Exception):
        upload_queue._drive_path("A/../B")


def test_drive_root_with_home_prefix_is_refused():
    with pytest.raises(ValueError):
        drive_config.validate_drive_rel_path("~/SPDM", allow_empty=False)


# --- worker stderr --------------------------------------------------------------------------------------

def test_worker_stderr_is_kept_in_a_rotating_redacted_log(tmp_path):
    worker_log.reset_for_tests()
    module = types.ModuleType("fake_scx_adapter_for_log")
    module.subprocess = subprocess
    sys.modules[module.__name__] = module
    try:
        tmp_path = tmp_path / "scx-work"
        assert worker_log.install(module, tmp_path) == 1
        script = ("import sys; sys.stderr.write('start refresh_token=abc123 Bearer eyJhb.c\\n'); "
                  "sys.stderr.write('{\"access_token\": \"eyJx.y\", \"password\": \"pw\"}\\n'); sys.stderr.flush()")
        process = module.subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.DEVNULL,
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        process.wait(timeout=30)
        log = tmp_path / worker_log.LOG_NAME
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and (not log.exists() or log.read_text(encoding="utf-8").count("\n") < 2):
            time.sleep(0.05)
        text = log.read_text(encoding="utf-8")
        assert "start refresh_token=***" in text and f"pid={process.pid}" in text
        for secret in ("abc123", "eyJhb", "eyJx", '"pw"'):
            assert secret not in text, text
        # a stderr the adapter reads itself is left alone
        piped = module.subprocess.Popen([sys.executable, "-c", "import sys; sys.stderr.write('mine')"],
                                        stderr=subprocess.PIPE)
        assert piped.communicate(timeout=30)[1] == b"mine"
    finally:
        del sys.modules[module.__name__]
        worker_log.reset_for_tests()


def test_worker_log_rotates(tmp_path):
    folder = tmp_path / "scx-work"
    log = worker_log.WorkerLog(folder / "worker.log", max_bytes=2000, backup_count=3)
    for index in range(200):
        log.write_line(f"line {index} " + "x" * 50)
    log.close()
    names = sorted(path.name for path in folder.iterdir())
    assert names == ["worker.log", "worker.log.1", "worker.log.2", "worker.log.3"]
    assert all(path.stat().st_size <= 2100 for path in folder.iterdir())


# --- 3. admin queue list editing --------------------------------------------------------------------

def _enqueue(states: list[str], *, origin: str = "result_drop") -> tuple[str, list[str]]:
    with connect() as conn:
        conn.execute("BEGIN TRANSACTION")
        batch = upload_queue.enqueue(conn, origin=origin, requested_by="test", items=[
            {"kind": "MKDIR", "dst_rel_dir": f"bulk/{uuid.uuid4().hex[:6]}/{index}"} for index in range(len(states))])
        ids = [item.id for item in upload_queue.batch_items(conn, batch)]
        for item_id, state in zip(ids, states):
            conn.execute("UPDATE drive_upload_queue SET state=?, finished_at=CASE WHEN ? IN "
                         "('DONE','CANCELLED','FAILED','CONFLICT') THEN now() ELSE NULL END WHERE id=?",
                         [state, state, item_id])
        conn.execute("COMMIT")
    return batch, ids


def _states(ids: list[str]) -> list[str | None]:
    with connect() as conn:
        return [(item.state if (item := upload_queue.get_item(conn, item_id)) else None) for item_id in ids]


def test_bulk_cancel_selected_and_whole_filter_without_touching_the_drive(scxw):
    batch, ids = _enqueue(["PENDING", "FAILED", "DONE"])
    _other, other_ids = _enqueue(["BLOCKED", "CONFLICT"])
    calls = len(scxw.drive.calls)
    response = scxw.client.post(QUEUE + "/bulk-cancel", json={"ids": ids})
    assert response.status_code == 200, response.text
    body = response.json()
    assert sorted(body["affected"]) == sorted(ids[:2])
    assert [(entry["id"], entry["code"]) for entry in body["skipped"]] == [(ids[2], "DRIVE_QUEUE_ITEM_STATE")]
    assert _states(ids) == ["CANCELLED", "CANCELLED", "DONE"]
    everything = scxw.client.post(QUEUE + "/bulk-cancel", json={"all_filter": {"state": "BLOCKED"}})
    assert everything.status_code == 200 and everything.json()["affected"] == [other_ids[0]], everything.text
    assert _states(other_ids) == ["CANCELLED", "CONFLICT"]
    assert len(scxw.drive.calls) == calls                       # nothing was sent to the drive
    bad = scxw.client.post(QUEUE + "/bulk-cancel", json={"ids": ids, "all_filter": {}})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "DRIVE_QUEUE_SELECTION_INVALID"
    with connect() as conn:
        audit = conn.execute("SELECT detail_json FROM audit_events WHERE action='DRIVE_QUEUE_BULK_CANCELLED' "
                             "ORDER BY occurred_at DESC LIMIT 1").fetchone()
    assert audit and other_ids[0] in str(audit[0])


def test_purge_removes_finished_rows_and_their_staging_only(scxw):
    batch, ids = _enqueue(["DONE", "CANCELLED", "FAILED"])
    staging = upload_queue.staging_dir_for(batch)
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "file.bin").write_bytes(b"x")
    open_batch, open_ids = _enqueue(["DONE", "PENDING"])
    drive_before = dict(scxw.drive.nodes)
    response = scxw.client.post(QUEUE + "/purge", json={"ids": [*ids, *open_ids]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert sorted(body["affected"]) == sorted(ids) and body["staging_removed"] == 1
    assert {(entry["id"], entry["code"]) for entry in body["skipped"]} == {
        (open_ids[0], "DRIVE_QUEUE_BATCH_OPEN"), (open_ids[1], "DRIVE_QUEUE_BATCH_OPEN")}
    assert _states(ids) == [None, None, None] and _states(open_ids) == ["DONE", "PENDING"]
    assert not staging.exists()
    assert scxw.drive.nodes.keys() == drive_before.keys()
    refused = scxw.client.post(QUEUE + "/purge", json={"states": ["PENDING"]})
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "DRIVE_QUEUE_PURGE_STATE"
    _done_batch, done_ids = _enqueue(["DONE", "CANCELLED"])
    all_done = scxw.client.post(QUEUE + "/purge", json={"states": ["DONE", "CANCELLED"]})
    assert all_done.status_code == 200, all_done.text
    assert set(done_ids) <= set(all_done.json()["affected"]) and _states(done_ids) == [None, None]
    assert _states(open_ids) == ["DONE", "PENDING"]             # still refused: its batch has an open item
    with connect() as conn:
        audit = conn.execute("SELECT count(*) FROM audit_events WHERE action='DRIVE_QUEUE_PURGED'").fetchone()[0]
    assert audit >= 2


def test_final_batches_are_purged_only_after_the_final_is_complete(scxw):
    ctx = _final_ctx(scxw)
    plan = _preview(scxw, ctx)
    operation_id = plan["operation_id"]
    _stage(scxw, ctx, operation_id)
    assert _confirm(scxw, ctx, operation_id).status_code == 200
    ids = [row[0] for row in _ids(operation_id)]
    publishing = scxw.client.post(QUEUE + "/purge", json={"ids": ids})
    assert publishing.status_code == 200 and publishing.json()["affected"] == []
    assert {entry["code"] for entry in publishing.json()["skipped"]} == {"DRIVE_QUEUE_BATCH_OPEN"}
    cancelled = scxw.client.post(QUEUE + "/bulk-cancel", json={"ids": ids})
    assert cancelled.status_code == 200 and cancelled.json()["affected"], cancelled.text
    assert operation_id in upload_queue._pending_finish
    _settle()
    with connect() as conn:
        status = conn.execute("SELECT status FROM finalization_operations WHERE operation_id=?", [operation_id]).fetchone()[0]
    assert status == "FAILED"                                     # the finish hook released the Final (single-cancel rule)
    stopped = scxw.client.post(QUEUE + "/purge", json={"ids": ids})
    assert stopped.status_code == 200 and stopped.json()["affected"] == []
    assert {entry["code"] for entry in stopped.json()["skipped"]} == {"DRIVE_QUEUE_FINAL_ACTIVE"}
    assert len(_items(operation_id)) == len(ids)                  # kept: the Final can still be retried


def _ids(batch_id: str) -> list[tuple[str]]:
    with connect() as conn:
        return [(item.id,) for item in upload_queue.batch_items(conn, batch_id)]


def test_bulk_endpoints_are_global_admin_only(scxw, monkeypatch):
    from types import SimpleNamespace

    from app.routers import drive as drive_router

    request = SimpleNamespace(state=SimpleNamespace(principal=SimpleNamespace(is_global_admin=False)))
    with pytest.raises(Exception) as refused:
        drive_router.queue_purge(drive_router.DriveQueuePurgeInput(ids=[]), request)
    assert getattr(refused.value, "status_code", None) == 403
    with pytest.raises(Exception) as refused_cancel:
        drive_router.queue_bulk_cancel(drive_router.DriveQueueBulkCancelInput(ids=[]), request)
    assert getattr(refused_cancel.value, "status_code", None) == 403


def test_worker_log_path_is_documented():
    doc = Path(__file__).resolve().parents[2] / "docs" / "features" / "scx-drive.md"
    text = doc.read_text(encoding="utf-8")
    assert "문제 확인: 로그 위치" in text and "worker.log" in text
