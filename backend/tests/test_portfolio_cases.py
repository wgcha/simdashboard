"""결과 대시보드 counts from the folder-registered model (request x Case x environment)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.adapters.persistence.portfolio import SQLPortfolioRepository
from app.adapters.persistence.portfolio_cases import case_state
from app.database import connect

NOW = datetime.now(timezone.utc).replace(tzinfo=None)


def _project(conn, tag: str, suffix: str) -> str:
    project_id = f"pc-proj-{suffix}-{tag}"
    conn.execute("INSERT INTO projects(id,name,product_name,created_at) VALUES(?,?,?,?)", [project_id, f"PC {suffix} {tag}", "TV", NOW])
    return project_id


def _request(conn, project_id: str, title: str) -> str:
    request_id = f"pc-req-{uuid4().hex[:10]}"
    conn.execute("INSERT INTO analysis_requests(id,project_id,title,status,owner,requested_at) VALUES(?,?,?,?,?,?)", [request_id, project_id, title, "READY", "tester", NOW])
    return request_id


def _registration(conn, project_id: str, request_id: str, status: str = "REGISTERED", environment: str = "DISTRIBUTION") -> str:
    reg = f"pc-reg-{uuid4().hex[:10]}"
    conn.execute(
        "INSERT INTO folder_environment_registrations(id,preview_id,idempotency_key,environment,project_id,request_id,status,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
        [reg, "preview", reg, environment, project_id, request_id, status, "tester", NOW])
    return reg


def _case(conn, project_id: str, request_id: str, name: str, environment: str = "DISTRIBUTION") -> str:
    case_id = f"pc-case-{uuid4().hex[:10]}"
    conn.execute(
        "INSERT INTO dashboard_cases(id,project_id,request_id,storage_root_id,relative_path,environment,source_name,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
        [case_id, project_id, request_id, "root-" + case_id, f"{request_id}/{name}", environment, name, "{}", NOW])
    return case_id


def _capture(conn, case_id: str, at: datetime) -> None:
    conn.execute("INSERT INTO dashboard_captures(id,case_id,fingerprint,recipe_version,manifest_json,payload_json,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
                 [f"pc-cap-{uuid4().hex[:10]}", case_id, uuid4().hex, "v1", "{}", "{}", "tester", at])


def _job(conn, registration_id: str, case_id: str, status: str, updated: datetime | None = None, error: str | None = None) -> None:
    conn.execute(
        "INSERT INTO folder_environment_capture_jobs(id,registration_id,case_id,status,error_code,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        [f"pc-job-{uuid4().hex[:10]}", registration_id, case_id, status, error, updated or NOW, updated or NOW])


def _seed(conn, tag: str) -> dict[str, str]:
    project = _project(conn, tag, "A")
    request = _request(conn, project, f"PCREQ {tag}")
    live = _registration(conn, project, request)
    deleted = _registration(conn, project, request, status="DELETED")
    ids: dict[str, str] = {"project": project, "request": request}
    captured_at = NOW - timedelta(hours=1)

    ids["done"] = _case(conn, project, request, "done")
    _capture(conn, ids["done"], captured_at - timedelta(days=1))
    _capture(conn, ids["done"], captured_at)                       # two captures: still one Case
    _job(conn, live, ids["done"], "COMPLETED")
    ids["pending"] = _case(conn, project, request, "pending")
    _job(conn, live, ids["pending"], "PENDING")
    ids["running"] = _case(conn, project, request, "running")
    _job(conn, live, ids["running"], "RUNNING")
    ids["waiting"] = _case(conn, project, request, "waiting")      # registered, no job at all
    ids["failed"] = _case(conn, project, request, "failed")
    _job(conn, live, ids["failed"], "PENDING", NOW - timedelta(minutes=10))
    _job(conn, live, ids["failed"], "FAILED", NOW, "CAPTURE_UNEXPECTED_ERROR")   # latest job decides
    ids["retried"] = _case(conn, project, request, "retried")
    _job(conn, live, ids["retried"], "FAILED", NOW - timedelta(minutes=10), "CAPTURE_UNEXPECTED_ERROR")
    _job(conn, live, ids["retried"], "PENDING", NOW)                              # retried: receiving again
    ids["refresh_failed"] = _case(conn, project, request, "refresh_failed")
    _capture(conn, ids["refresh_failed"], captured_at - timedelta(hours=2))
    _job(conn, live, ids["refresh_failed"], "FAILED", NOW, "DRIVE_READ_NOT_PREPARED")
    ids["orphan"] = _case(conn, project, request, "orphan", environment="USAGE")  # no live USAGE registration
    ids["deleted_reg"] = _case(conn, project, request, "deleted_reg", environment="USAGE")
    _job(conn, deleted, ids["deleted_reg"], "PENDING")
    return ids


def _overview(tag: str, **filters):
    with connect() as conn:
        return SQLPortfolioRepository(conn).overview(search=tag, **filters)


def test_case_state_precedence() -> None:
    base = {"capture_count": 0, "live_registration": 0, "job_status": None}
    assert case_state({**base, "capture_count": 2, "job_status": "PENDING"}) == ("COMPLETED", "RECEIVING")
    assert case_state({**base, "capture_count": 1, "job_status": "FAILED"}) == ("COMPLETED", "FAILED")
    assert case_state({**base, "job_status": "RUNNING"}) == ("RECEIVING", None)
    assert case_state({**base, "job_status": "FAILED", "live_registration": 1}) == ("FAILED", None)
    assert case_state({**base, "job_status": "COMPLETED", "live_registration": 1}) == ("WAITING", None)
    assert case_state({**base, "live_registration": 1}) == ("WAITING", None)
    assert case_state(base) == (None, None)


def test_overview_counts_folder_registered_cases() -> None:
    tag = uuid4().hex[:8]
    with connect() as conn:
        ids = _seed(conn, tag)
    payload = _overview(tag)
    counts = payload["case_counts"]
    assert counts == {"total": 7, "completed": 2, "receiving": 3, "waiting": 1, "failed": 1,
                      "judged": 0, "criteria_fail": 0, "refresh_failed": 1}
    by_id = {item["case_id"]: item for item in payload["case_records"]}
    assert {key: by_id[ids[key]]["case_state"] for key in ("done", "pending", "running", "waiting", "failed", "retried", "refresh_failed")} == {
        "done": "COMPLETED", "pending": "RECEIVING", "running": "RECEIVING", "waiting": "WAITING", "failed": "FAILED",
        "retried": "RECEIVING", "refresh_failed": "COMPLETED"}
    assert ids["orphan"] not in by_id and ids["deleted_reg"] not in by_id
    assert by_id[ids["refresh_failed"]]["refresh_state"] == "FAILED"
    assert by_id[ids["failed"]]["error_code"] == "CAPTURE_UNEXPECTED_ERROR"
    assert by_id[ids["done"]]["capture_count"] == 2 and by_id[ids["done"]]["captured_at"] is not None
    assert payload["case_freshness"] is not None and payload["case_grain"] == "REQUEST_CASE_ENVIRONMENT"
    # Folder registration creates no load_cases: the legacy part stays empty and is not mixed in.
    assert payload["legacy_counts"] == {"load_cases": 0, "with_run": 0, "pending": 0, "judged": 0, "fail": 0}
    assert all(item["load_case_id"] == "" for item in payload["records"] if item["request_id"] == ids["request"])


def test_environments_are_counted_per_case() -> None:
    tag = uuid4().hex[:8]
    with connect() as conn:
        project = _project(conn, tag, "E")
        request = _request(conn, project, f"PCREQ {tag}")
        for environment in ("USAGE", "DISTRIBUTION"):
            registration = _registration(conn, project, request, environment=environment)
            case = _case(conn, project, request, "same-name", environment=environment)
            _job(conn, registration, case, "PENDING")
    records = _overview(tag)["case_records"]
    assert sorted(item["environment"] for item in records) == ["DISTRIBUTION", "USAGE"]


def test_legacy_load_cases_are_counted_separately() -> None:
    tag = uuid4().hex[:8]
    with connect() as conn:
        ids = _seed(conn, tag)
        conn.execute("INSERT INTO load_cases(id,request_id,name,analysis_type,status,parameters_json,created_at) VALUES(?,?,?,?,?,?,?)",
                     [f"pc-lc-{tag}", ids["request"], "legacy LC", "DROP", "READY", "{}", NOW])
    payload = _overview(tag)
    assert payload["legacy_counts"]["load_cases"] == 1 and payload["legacy_counts"]["pending"] == 1
    assert payload["legacy_counts"]["with_run"] == 0 and payload["legacy_counts"]["fail"] == 0
    assert payload["case_counts"]["total"] == 7           # legacy records never enter the Case counts
    assert any(item["load_case_id"] for item in payload["records"])


def test_filters_apply_to_cases() -> None:
    tag = uuid4().hex[:8]
    with connect() as conn:
        ids = _seed(conn, tag)
        other_project = _project(conn, tag, "B")
        other_request = _request(conn, other_project, f"PCREQ {tag} other")
        other_reg = _registration(conn, other_project, other_request)
        _job(conn, other_reg, _case(conn, other_project, other_request, "other-case"), "PENDING")
    assert _overview(tag)["case_counts"]["total"] == 8
    scoped = _overview(tag, project_id=ids["project"])
    assert scoped["case_counts"]["total"] == 7 and {item["project_id"] for item in scoped["case_records"]} == {ids["project"]}
    assert _overview(tag, project_id="no-such-project")["case_records"] == []
    assert _overview(tag, status="DONE")["case_records"] == []
    assert _overview(tag, analysis_type="DROP")["case_records"] == []
    with connect() as conn:
        found = SQLPortfolioRepository(conn).overview(search="refresh_failed")["case_records"]
    assert [item["case_id"] for item in found] == [ids["refresh_failed"]]
