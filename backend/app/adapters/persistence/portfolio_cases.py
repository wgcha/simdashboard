"""Folder-registered Case counts for the 결과 대시보드 (docs: result overview, 2026-10-28).

Grain: one record per ``dashboard_cases`` row, i.e. per request x Case x environment (USAGE / DISTRIBUTION).
Folder registration never creates ``load_cases``, so the legacy ``load_cases`` -> ``analysis_runs`` ->
``scalar_results`` chain cannot see these results; this module reads the folder model instead.

State of a Case (mutually exclusive, first match wins):

* ``COMPLETED`` (결과 보유): the Case has at least one ``dashboard_captures`` row.  A newer capture job
  (refresh) that is waiting / running / failed does not hide the stored result; it is reported in
  ``refresh_state`` (``RECEIVING`` / ``FAILED``) instead.
* ``RECEIVING`` (결과 받는 중): no capture, and the latest capture job of a live registration is PENDING or RUNNING.
* ``FAILED`` (받기 실패): no capture, and the latest capture job is FAILED.
* ``WAITING`` (결과 대기): a registered Case (dashboard_cases row) of a live registration (status in
  ``capture_jobs.LIVE_STATES`` for the same project / request / environment) with no capture and
  no pending / running / failed job.
* Anything else (no capture, no live registration, no live job) is an orphan row and is not counted.

"Latest job" is the newest ``updated_at`` job of the Case among jobs of live registrations.
Criteria: captured results have no judgement criteria yet, so ``judged`` / ``criteria_fail`` stay 0.
"""
from __future__ import annotations

from typing import Any

from ...database_connection import ConnectionLike, rows
from ...services.capture_jobs import LIVE_STATES

CASE_GRAIN = "REQUEST_CASE_ENVIRONMENT"
CASE_SOURCE = (
    "folder-registered Cases: dashboard_cases / dashboard_captures / folder_environment_capture_jobs "
    "(live registrations) per request x Case x environment"
)
_CASE_SQL = """
WITH live AS (
    SELECT DISTINCT project_id, request_id, environment
    FROM folder_environment_registrations WHERE status IN ({marks})
), job AS (
    SELECT j.case_id, j.status, j.error_code, j.updated_at,
           row_number() OVER (PARTITION BY j.case_id ORDER BY j.updated_at DESC, j.created_at DESC, j.id DESC) AS rn
    FROM folder_environment_capture_jobs j
    JOIN folder_environment_registrations r ON r.id = j.registration_id
    WHERE r.status IN ({marks})
), cap AS (
    SELECT case_id, count(*) AS capture_count, max(created_at) AS captured_at
    FROM dashboard_captures GROUP BY case_id
)
SELECT c.id AS case_id, c.project_id, c.request_id, c.environment, c.relative_path, c.source_name,
       COALESCE(cap.capture_count, 0) AS capture_count, cap.captured_at,
       job.status AS job_status, job.error_code, job.updated_at AS job_updated_at,
       CASE WHEN live.request_id IS NULL THEN 0 ELSE 1 END AS live_registration
FROM dashboard_cases c
LEFT JOIN cap ON cap.case_id = c.id
LEFT JOIN job ON job.case_id = c.id AND job.rn = 1
LEFT JOIN live ON live.project_id = c.project_id AND live.request_id = c.request_id AND live.environment = c.environment
"""


def case_state(item: dict[str, Any]) -> tuple[str | None, str | None]:
    """(state, refresh_state) of one Case row; ``state`` is None for an orphan row."""
    job = str(item["job_status"]).upper() if item.get("job_status") else None
    receiving = job in ("PENDING", "RUNNING")
    if int(item["capture_count"] or 0) > 0:
        return "COMPLETED", "RECEIVING" if receiving else "FAILED" if job == "FAILED" else None
    if receiving:
        return "RECEIVING", None
    if job == "FAILED":
        return "FAILED", None
    if int(item["live_registration"] or 0):
        return "WAITING", None
    return None, None


def case_rows(connection: ConnectionLike) -> list[dict[str, Any]]:
    marks = ",".join("?" for _ in LIVE_STATES)
    return rows(connection.execute(_CASE_SQL.format(marks=marks), [*LIVE_STATES, *LIVE_STATES]))


def build_case_records(connection: ConnectionLike, requests: dict[str, dict[str, Any]], selected: Any) -> list[dict[str, Any]]:
    """Case records of the requests in ``requests`` (request_id -> request fields) that pass ``selected``."""
    records: list[dict[str, Any]] = []
    for item in case_rows(connection):
        request = requests.get(item["request_id"])
        if request is None:
            continue
        state, refresh = case_state(item)
        if state is None:
            continue
        name = item["source_name"] or str(item["relative_path"]).rstrip("/").split("/")[-1]
        record = {
            "project_id": request["project_id"], "project_name": request["project_name"],
            "product_name": request["product_name"], "request_id": item["request_id"],
            "request_title": request["request_title"], "owner": request["owner"],
            "request_status": request["request_status"], "requested_at": request["requested_at"],
            "case_id": item["case_id"], "case_name": name, "case_path": item["relative_path"],
            "environment": item["environment"], "case_state": state, "refresh_state": refresh,
            "job_status": item["job_status"], "error_code": item["error_code"],
            "capture_count": int(item["capture_count"] or 0), "captured_at": item["captured_at"],
        }
        if selected(record):
            records.append(record)
    records.sort(key=lambda r: (str(r["captured_at"] or ""), str(r["requested_at"])), reverse=True)
    return records


def case_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    states = [r["case_state"] for r in records]
    return {
        "total": len(records),
        "completed": states.count("COMPLETED"),
        "receiving": states.count("RECEIVING"),
        "waiting": states.count("WAITING"),
        "failed": states.count("FAILED"),
        # Captured results have no judgement criteria yet.
        "judged": 0,
        "criteria_fail": 0,
        "refresh_failed": sum(1 for r in records if r["refresh_state"] == "FAILED"),
    }
