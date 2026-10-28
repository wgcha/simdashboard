"""Issue #55: size guard and file inventory of a Case capture.

A big DISTRIBUTION Case produced a ~224 MB ``payload_json`` and PostgreSQL refused the INSERT
(``ProgramLimitExceeded``).  The capture now measures first, refuses with ``DASHBOARD_CAPTURE_TOO_LARGE``,
the job stays FAILED until a manual retry, and every attempt reports what it took and skipped.
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

import pytest

from app.adapters.persistence.dashboard_schema import ensure_dashboard_schema
from app.database_connection import connect
from app.services import capture_inventory, dashboard_capture
from app.services import folder_discovery_environment as environment_service
from app.services.dashboard_capture import DashboardCaptureError, _root_id, create_capture
from tests.test_capture_jobs_drive import CASES, _capture_jobs, _jobs, _slow_drive, _slow_request
from tests.test_depth_schema import _restore_default_depth_schema  # noqa: F401  (autouse)
from tests.test_drive_discovery_tree import _discovery, _project  # noqa: F401  (autouse)
from tests.test_drive_reads import ENV, scx  # noqa: F401  (fixture)
from tests.test_folder_auto_discovery import _container_schema

pytestmark = pytest.mark.duckdb_integration

CSV_NAME = "MAX_RESULT_Max_Stress_P1 (major)_Mid_C23_scene.h3d.csv"


def _distribution_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shared"
    scene = root / "distribution-case" / "Drop" / "run" / "INDIVIDUAL" / "1_Face_Drop_Scene01_Face1_1st"
    scene.mkdir(parents=True)
    (scene / CSV_NAME).write_text("Position,Layer_1,Layer_2,Layer_3,Layer_4\nTOP,1,2,3,4\n", encoding="utf-8")
    (scene / "movie.avi").write_bytes(b"\x00" * 2048)       # not an allowed suffix: skipped, never silently
    (scene / "notes.txt").write_bytes(b"x" * 10)
    (scene / "clip.mp4").write_bytes(b"\x00" * 100)
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root))
    return root


def _payload(root: Path, connection) -> dict:
    row = connection.execute("SELECT project_id,id FROM analysis_requests ORDER BY id LIMIT 1").fetchone()
    return {"project_id": str(row[0]), "request_id": str(row[1]), "simulation_case_id": "case-id",
            "load_case_id": None, "execution_run_id": None, "run_display_name": None, "mode": None,
            "component_id": None, "storage_root_id": _root_id(root), "root_relative_path": "distribution-case",
            "environment": "DISTRIBUTION"}


def test_inventory_counts_avi_as_skipped_and_logs_one_warning(tmp_path, monkeypatch, caplog):
    root = _distribution_case(tmp_path, monkeypatch)
    with connect() as connection:
        ensure_dashboard_schema(connection)
        with caplog.at_level(logging.WARNING, logger="app.services.dashboard_capture"):
            result = create_capture(connection, _payload(root, connection), actor="test-actor")
    inventory = result["inventory"]
    skipped = {item["reason"]: item for item in inventory["skipped"]}
    assert skipped["UNSUPPORTED_EXTENSION"]["count"] == 2
    assert skipped["UNSUPPORTED_EXTENSION"]["extensions"] == {".avi": 1, ".txt": 1}
    assert skipped["UNSUPPORTED_EXTENSION"]["bytes"] == 2058
    taken = {item["extension"]: item for item in inventory["taken"]}
    assert taken[".csv"]["count"] == 1 and taken[".mp4"] == {"extension": ".mp4", "count": 1, "bytes": 100}
    assert inventory["observation_count"] and inventory["payload_bytes"] > 0 and inventory["outcome"] == "COMPLETED"
    assert inventory["top_sources"][0]["path"].endswith(CSV_NAME)
    lines = [record.getMessage() for record in caplog.records if record.getMessage().startswith("Capture inventory")]
    assert len(lines) == 1 and record_level(caplog) == logging.WARNING
    assert "UNSUPPORTED_EXTENSION=2/2058B[.avi:1,.txt:1]" in lines[0] and "case=distribution-case" in lines[0]
    assert len(lines[0]) <= capture_inventory.MAX_LOG_CHARS


def record_level(caplog) -> int:
    return next(r.levelno for r in caplog.records if r.getMessage().startswith("Capture inventory"))


def test_too_large_payload_is_refused_before_any_insert(tmp_path, monkeypatch):
    root = _distribution_case(tmp_path, monkeypatch)
    monkeypatch.setenv(capture_inventory.ENV_MAX_PAYLOAD, "500")
    with connect() as connection:
        ensure_dashboard_schema(connection)
        before = connection.execute("SELECT count(*) FROM dashboard_captures").fetchone()[0]
        with pytest.raises(DashboardCaptureError) as caught:
            create_capture(connection, _payload(root, connection), actor="test-actor")
        assert caught.value.code == "DASHBOARD_CAPTURE_TOO_LARGE"
        assert "결과 데이터가 너무 커서" in str(caught.value) and "한도" in str(caught.value)
        assert caught.value.inventory["outcome"] == "DASHBOARD_CAPTURE_TOO_LARGE"
        assert caught.value.inventory["top_sources"], "top contributors are reported"
        assert connection.execute("SELECT count(*) FROM dashboard_captures").fetchone()[0] == before
        assert connection.execute("SELECT count(*) FROM dashboard_assets").fetchone()[0] == 0


def test_raw_asset_total_limit_and_database_size_error_map_to_the_same_code(tmp_path, monkeypatch):
    root = _distribution_case(tmp_path, monkeypatch)
    monkeypatch.setenv(capture_inventory.ENV_MAX_ASSETS, "100")
    with connect() as connection:
        ensure_dashboard_schema(connection)
        with pytest.raises(DashboardCaptureError) as caught:
            create_capture(connection, _payload(root, connection), actor="test-actor")
        assert caught.value.code == "DASHBOARD_CAPTURE_TOO_LARGE" and "원본 파일 합계" in str(caught.value)

    class ProgramLimitExceeded(Exception):
        sqlstate = "54000"

    assert dashboard_capture._is_db_size_limit(ProgramLimitExceeded("jsonb ... (268435455 바이트)"))
    assert not dashboard_capture._is_db_size_limit(ValueError("other"))


def test_too_large_job_stays_failed_until_manual_retry_and_shows_inventory(scx, monkeypatch):
    _container_schema()
    _slow_drive(scx, monkeypatch)
    project = _project(uuid.uuid4().hex[:6])
    _slow_request(scx, f"{project}/WR-00000955_유통 환경 시뮬레이션")
    request_id = scx.client.post(ENV + "/discover", json={"force": True}).json()["created_requests"][0]["id"]
    inventory = {"case_path": "x/y", "outcome": "DASHBOARD_CAPTURE_TOO_LARGE", "taken": [], "taken_total": {"count": 3, "bytes": 30},
                 "skipped": [{"reason": "UNSUPPORTED_EXTENSION", "label": "지원하지 않는 파일 형식", "count": 1, "bytes": 5,
                              "extensions": {".avi": 1}, "examples": ["a.avi"]}], "skipped_total": {"count": 1, "bytes": 5},
                 "observation_count": 9, "payload_bytes": 999, "manifest_bytes": 10, "top_sources": [], "top_media": [],
                 "limits": {"payload_bytes": 500, "asset_total_bytes": 1000}}

    def too_large(conn, payload, **kwargs):
        raise DashboardCaptureError("DASHBOARD_CAPTURE_TOO_LARGE", "결과 데이터가 너무 커서 저장하지 않았습니다.", inventory)

    monkeypatch.setattr(dashboard_capture, "create_capture", too_large)
    jobs = _capture_jobs()
    for _ in range(30):   # slow fake drive: a busy machine needs more passes (timing, not behaviour)
        jobs.run_pending(budget_seconds=3.5, session_seconds=3.0)
        if _jobs(request_id) and all(code == "DASHBOARD_CAPTURE_TOO_LARGE" for _status, code in _jobs(request_id)):
            break
    assert {(status, code) for status, code in _jobs(request_id)} == {("FAILED", "DASHBOARD_CAPTURE_TOO_LARGE")}

    # Not auto-retried: another pass, a refresh (enqueue_capture) and the start-up retry leave it FAILED.
    jobs.run_pending(budget_seconds=3.5, session_seconds=3.0)
    with connect() as conn:
        row = conn.execute("SELECT j.registration_id,j.case_id FROM folder_environment_capture_jobs j JOIN "
                           "folder_environment_registrations r ON r.id=j.registration_id WHERE r.request_id=? LIMIT 1", [request_id]).fetchone()
        environment_service.enqueue_capture(conn, str(row[0]), str(row[1]))
        jobs.retry_fixed_failures(conn)
    assert {(status, code) for status, code in _jobs(request_id)} == {("FAILED", "DASHBOARD_CAPTURE_TOO_LARGE")}

    body = scx.client.get(ENV + "/capture-jobs", params={"status": "FAILED"}).json()
    mine = [item for item in body["items"] if item["request_id"] == request_id]
    assert mine and all(item["error_code"] == "DASHBOARD_CAPTURE_TOO_LARGE" for item in mine)
    assert all("너무 커서" in item["error_message"] for item in mine)
    assert mine[0]["inventory"]["skipped"][0]["extensions"] == {".avi": 1}

    retry = scx.client.post(f"{ENV}/registrations/{mine[0]['registration_id']}/capture/retry", json={"job_ids": [mine[0]["job_id"]]})
    assert retry.status_code == 200, retry.text
    assert ("PENDING", None) in set(_jobs(request_id))
