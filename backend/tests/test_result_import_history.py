from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database import connect, initialize_database
from app.main import app
from app.security import hash_password


LOAD_CASE_ID = "loadcase-drop-bottom-001"


def _insert_job(
    job_id: str,
    *,
    status: str,
    source_type: str = "MASTER_FOLDER_REFRESH",
    source_folder: str = "history/manifest.json",
    outcome_reason: str | None = None,
) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO folder_import_jobs
                (id, load_case_id, analysis_run_id, schema_id, schema_version,
                 source_folder, status, summary_json, created_at, source_type,
                 outcome_reason, completed_at)
            VALUES (?, ?, NULL, 'history-test', 1, ?, ?, NULL, ?, ?, ?, ?)
            """,
            [job_id, LOAD_CASE_ID, source_folder, status, now, source_type, outcome_reason, now],
        )


def _write_retry_manifest(
    root: Path,
    relative: str,
    *,
    load_case_id: str,
    source_run_id: str | None = None,
) -> None:
    bundle = root / Path(relative).parent
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "scalar-results.json").write_text(
        json.dumps(
            [{
                "variable_key": f"history_retry_{uuid4().hex[:8]}",
                "display_name": "재시도 결과",
                "data_type": "FLOAT",
                "value": 12.5,
                "unit": "MPa",
                "threshold": 75.0,
                "result_group": "OPEN_CELL",
            }]
        ),
        encoding="utf-8",
    )
    manifest: dict[str, object] = {
        "schema_id": "history-retry",
        "version": 1,
        "solver": "pytest",
        "context": {
            "project_id": "project-tv-001",
            "request_id": "request-clamp-001" if load_case_id == "loadcase-clamp-left-001" else "request-drop-001",
            "load_case_id": load_case_id,
        },
        "mappings": [{"kind": "typed_scalars", "path": "scalar-results.json"}],
    }
    if source_run_id is not None:
        manifest["source_run_id"] = source_run_id
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_result_import_history_paginates_and_enforces_query_contract() -> None:
    initialize_database()
    prefix = f"history-{uuid4().hex[:10]}"
    with connect() as conn:
        before_total = int(
            conn.execute("SELECT count(*) FROM folder_import_jobs WHERE load_case_id=?", [LOAD_CASE_ID]).fetchone()[0]
        )
        before_counts = dict(
            conn.execute(
                "SELECT status, count(*) FROM folder_import_jobs WHERE load_case_id=? GROUP BY status",
                [LOAD_CASE_ID],
            ).fetchall()
        )
    for index, status in enumerate(("COMPLETED", "SKIPPED", "FAILED")):
        _insert_job(f"{prefix}-{index}", status=status)
    try:
        with TestClient(app) as client:
            page = client.get(
                f"/api/load-cases/{LOAD_CASE_ID}/result-imports",
                params={"limit": 1, "offset": 1},
            )
            assert page.status_code == 200, page.text
            body = page.json()
            assert body["total"] == before_total + 3
            assert len(body["items"]) == 1
            assert body["counts"]["COMPLETED"] == int(before_counts.get("COMPLETED", 0)) + 1
            assert body["counts"]["FAILED"] == int(before_counts.get("FAILED", 0)) + 1
            assert body["counts"]["SKIPPED"] == int(before_counts.get("SKIPPED", 0)) + 1
            assert body["counts"]["REJECTED"] == int(before_counts.get("REJECTED", 0))
            assert body["counts"]["RUNNING"] == int(before_counts.get("RUNNING", 0))
            assert set(body["items"][0]) == {
                "id", "load_case_id", "status", "source_type", "source_folder",
                "source_checksum", "source_run_id", "conflict_policy", "outcome_reason",
                "operation", "analysis_run_id", "replaced_analysis_run_id", "source_revision",
                "created_at", "completed_at", "retryable",
            }
            assert client.get(
                f"/api/load-cases/{LOAD_CASE_ID}/result-imports",
                params={"status": "NOT_A_STATUS"},
            ).status_code == 422
            assert client.get(
                f"/api/load-cases/{LOAD_CASE_ID}/result-imports",
                params={"limit": 0},
            ).status_code == 422
            filtered = client.get(
                f"/api/load-cases/{LOAD_CASE_ID}/result-imports",
                params={"status": "FAILED"},
            )
            assert filtered.status_code == 200
            assert filtered.json()["total"] == int(before_counts.get("FAILED", 0)) + 1
            assert filtered.json()["items"][0]["operation"] is None
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM folder_import_jobs WHERE id LIKE ?", [f"{prefix}-%"])


def test_result_import_history_prevents_cross_project_load_case_read(monkeypatch: pytest.MonkeyPatch) -> None:
    initialize_database()
    suffix = uuid4().hex[:10]
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    user_id = f"history-user-{suffix}"
    project_id = f"history-project-{suffix}"
    request_id = f"history-request-{suffix}"
    load_case_id = f"history-loadcase-{suffix}"
    password = "history-scope-password"
    retry_job_id = f"history-retry-{suffix}"
    with connect() as conn:
        conn.execute(
            "INSERT INTO projects VALUES (?, ?, ?, ?, ?)",
            [project_id, "이력 범위 검증", "테스트", None, now],
        )
        conn.execute(
            "INSERT INTO analysis_requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [request_id, project_id, "다른 프로젝트", "READY", None, None, now, None, None],
        )
        conn.execute(
            "INSERT INTO load_cases VALUES (?, ?, ?, ?, ?, ?, ?)",
            [load_case_id, request_id, "다른 하중", "STATIC", "READY", "{}", now],
        )
        conn.execute(
            """INSERT INTO users
                (id, username, password_hash, display_name, legacy_role, is_active,
                 created_at, updated_at, account_status, is_global_admin)
                VALUES (?, ?, ?, ?, 'editor', true, ?, ?, 'ACTIVE', false)""",
            [user_id, f"history-user-{suffix}", hash_password(password), "이력 사용자", now, now],
        )
        conn.execute(
            """INSERT INTO project_memberships
                (id, project_id, user_id, role, created_by, created_at, updated_by, updated_at)
                VALUES (?, 'project-tv-001', ?, 'power', 'test', ?, 'test', ?)""",
            [f"history-membership-{suffix}", user_id, now, now],
        )
    _insert_job(retry_job_id, status="FAILED", source_folder="other/manifest.json")
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-secret-key-that-is-at-least-32-characters")
    try:
        with TestClient(app) as client:
            login = client.post(
                "/api/auth/login",
                json={"username": f"history-user-{suffix}", "password": password},
            )
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            assert client.get(
                f"/api/load-cases/{load_case_id}/result-imports", headers=headers
            ).status_code == 403
            # A project power user has RESULT_IMPORT for the local project but
            # cannot invoke the installation-wide retry operation.
            assert client.post(f"/api/result-imports/{retry_job_id}/retry", headers=headers).status_code == 403
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM folder_import_jobs WHERE id=?", [retry_job_id])
            conn.execute("DELETE FROM project_memberships WHERE user_id=?", [user_id])
            conn.execute("DELETE FROM users WHERE id=?", [user_id])
            conn.execute("DELETE FROM load_cases WHERE id=?", [load_case_id])
            conn.execute("DELETE FROM analysis_requests WHERE id=?", [request_id])
            conn.execute("DELETE FROM projects WHERE id=?", [project_id])


def test_result_import_retry_contract_uses_only_stored_relative_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    initialize_database()
    suffix = uuid4().hex[:10]
    retryable_job = f"retry-{suffix}"
    non_retryable_job = f"retry-non-{suffix}"
    unsafe_job = f"retry-unsafe-{suffix}"
    retry_source_folder = f"contract-missing-{suffix}/manifest.json"
    _insert_job(
        retryable_job,
        status="FAILED",
        source_folder=retry_source_folder,
        outcome_reason="BUNDLE_IMPORT_FAILED",
    )
    _insert_job(non_retryable_job, status="COMPLETED", source_folder="completed/manifest.json")
    _insert_job(unsafe_job, status="FAILED", source_folder="../manifest.json", outcome_reason="BUNDLE_IMPORT_FAILED")
    monkeypatch.setenv("SIMDASH_IMPORT_ROOT", str(tmp_path))
    try:
        with TestClient(app) as client:
            retry = client.post(f"/api/result-imports/{retryable_job}/retry")
            assert retry.status_code == 200, retry.text
            assert retry.json()["manifest_path"] == retry_source_folder
            assert retry.json()["status"] == "FAILED"
            not_retryable = client.post(f"/api/result-imports/{non_retryable_job}/retry")
            assert not_retryable.status_code == 409
            assert not_retryable.json()["detail"]["code"] == "RESULT_IMPORT_NOT_RETRYABLE"
            unsafe = client.post(f"/api/result-imports/{unsafe_job}/retry")
            assert unsafe.status_code == 409
            assert unsafe.json()["detail"]["code"] == "RESULT_IMPORT_NOT_RETRYABLE"
        route = next(route for route in app.routes if getattr(route, "path", None) == "/api/result-imports/{job_id}/retry")
        assert not route.dependant.query_params
        assert route.dependant.body_params == []
    finally:
        with connect() as conn:
            conn.execute(
                "DELETE FROM folder_import_jobs WHERE source_folder=? OR id IN (?, ?)",
                [retry_source_folder, non_retryable_job, unsafe_job],
            )


def test_result_import_retry_missing_manifest_appends_failed_job_to_original_load_case(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    initialize_database()
    suffix = uuid4().hex[:10]
    source_folder = f"missing-{suffix}/manifest.json"
    original_job = f"retry-missing-{suffix}"
    _insert_job(original_job, status="FAILED", source_folder=source_folder, outcome_reason="BUNDLE_IMPORT_FAILED")
    monkeypatch.setenv("SIMDASH_IMPORT_ROOT", str(tmp_path))
    try:
        with connect() as conn:
            before = int(
                conn.execute(
                    "SELECT count(*) FROM folder_import_jobs WHERE load_case_id=? AND source_folder=?",
                    [LOAD_CASE_ID, source_folder],
                ).fetchone()[0]
            )
        with TestClient(app) as client:
            response = client.post(f"/api/result-imports/{original_job}/retry")
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "FAILED"
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT load_case_id, status, outcome_reason
                FROM folder_import_jobs
                WHERE source_folder=?
                ORDER BY created_at, id
                """,
                [source_folder],
            ).fetchall()
        assert len(rows) == before + 1
        assert rows[-1][0] == LOAD_CASE_ID
        assert rows[-1][1] == "FAILED"
        assert rows[-1][2] == "BUNDLE_SOURCE_PATH_UNSAFE"
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM folder_import_jobs WHERE source_folder=?", [source_folder])


def test_result_import_retry_changed_manifest_target_isolated_to_original_load_case(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    initialize_database()
    suffix = uuid4().hex[:10]
    source_folder = f"changed-target-{suffix}/manifest.json"
    original_job = f"retry-target-{suffix}"
    _write_retry_manifest(tmp_path, source_folder, load_case_id="loadcase-clamp-left-001")
    _insert_job(original_job, status="FAILED", source_folder=source_folder, outcome_reason="BUNDLE_IMPORT_FAILED")
    monkeypatch.setenv("SIMDASH_IMPORT_ROOT", str(tmp_path))
    try:
        with connect() as conn:
            before_other = int(
                conn.execute(
                    "SELECT count(*) FROM analysis_runs WHERE load_case_id=?",
                    ["loadcase-clamp-left-001"],
                ).fetchone()[0]
            )
            before_original_history = int(
                conn.execute(
                    "SELECT count(*) FROM folder_import_jobs WHERE load_case_id=? AND source_folder=?",
                    [LOAD_CASE_ID, source_folder],
                ).fetchone()[0]
            )
        with TestClient(app) as client:
            response = client.post(f"/api/result-imports/{original_job}/retry")
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "FAILED"
            assert response.json()["reason_code"] == "RESULT_IMPORT_RETRY_TARGET_MISMATCH"
        with connect() as conn:
            after_other = int(
                conn.execute(
                    "SELECT count(*) FROM analysis_runs WHERE load_case_id=?",
                    ["loadcase-clamp-left-001"],
                ).fetchone()[0]
            )
            retry_rows = conn.execute(
                """
                SELECT load_case_id, status, outcome_reason
                FROM folder_import_jobs
                WHERE source_folder=?
                ORDER BY created_at, id
                """,
                [source_folder],
            ).fetchall()
        assert after_other == before_other
        assert len(retry_rows) == before_original_history + 1
        assert retry_rows[-1] == (LOAD_CASE_ID, "FAILED", "RESULT_IMPORT_RETRY_TARGET_MISMATCH")
    finally:
        with connect() as conn:
            run_ids = [
                row[0]
                for row in conn.execute(
                    "SELECT analysis_run_id FROM folder_import_jobs WHERE source_folder=? AND analysis_run_id IS NOT NULL",
                    [source_folder],
                ).fetchall()
            ]
            conn.execute("DELETE FROM folder_import_jobs WHERE source_folder=?", [source_folder])
            for run_id in run_ids:
                conn.execute("DELETE FROM scalar_results WHERE analysis_run_id=?", [run_id])
                conn.execute("DELETE FROM canonical_result_ingestion_source_versions WHERE analysis_run_id=?", [run_id])
                conn.execute("DELETE FROM analysis_run_metadata WHERE analysis_run_id=?", [run_id])
                conn.execute("DELETE FROM analysis_runs WHERE id=?", [run_id])


def test_result_import_retry_succeeds_against_one_stored_master_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    initialize_database()
    suffix = uuid4().hex[:10]
    job_id = f"retry-success-{suffix}"
    bundle = tmp_path / "retry-success"
    bundle.mkdir()
    (bundle / "scalar-results.json").write_text(
        json.dumps(
            [{
                "variable_key": "history_retry_stress",
                "display_name": "재시도 응력",
                "data_type": "FLOAT",
                "value": 12.5,
                "unit": "MPa",
                "threshold": 75.0,
                "result_group": "OPEN_CELL",
            }]
        ),
        encoding="utf-8",
    )
    (bundle / "manifest.json").write_text(
        json.dumps(
            {
                "schema_id": "history-retry",
                "version": 1,
                "solver": "pytest",
                "context": {
                    "project_id": "project-tv-001",
                    "request_id": "request-drop-001",
                    "load_case_id": LOAD_CASE_ID,
                },
                "mappings": [{"kind": "typed_scalars", "path": "scalar-results.json"}],
            }
        ),
        encoding="utf-8",
    )
    _insert_job(job_id, status="FAILED", source_folder="retry-success/manifest.json")
    monkeypatch.setenv("SIMDASH_IMPORT_ROOT", str(tmp_path))
    try:
        with TestClient(app) as client:
            response = client.post(f"/api/result-imports/{job_id}/retry")
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "IMPORTED"
            history = client.get(f"/api/load-cases/{LOAD_CASE_ID}/result-imports")
            assert history.status_code == 200
            assert any(
                item["source_folder"] == "retry-success/manifest.json" and item["status"] == "COMPLETED"
                for item in history.json()["items"]
            )
    finally:
        with connect() as conn:
            run_ids = [
                row[0]
                for row in conn.execute(
                    "SELECT analysis_run_id FROM folder_import_jobs WHERE source_folder='retry-success/manifest.json' AND analysis_run_id IS NOT NULL"
                ).fetchall()
            ]
            conn.execute("DELETE FROM folder_import_jobs WHERE source_folder='retry-success/manifest.json'")
            for run_id in run_ids:
                conn.execute("DELETE FROM scalar_results WHERE analysis_run_id=?", [run_id])
                conn.execute("DELETE FROM canonical_result_ingestion_source_versions WHERE analysis_run_id=?", [run_id])
                conn.execute("DELETE FROM analysis_run_metadata WHERE analysis_run_id=?", [run_id])
                conn.execute("DELETE FROM analysis_runs WHERE id=?", [run_id])
            conn.execute("DELETE FROM variable_definitions WHERE load_case_id=? AND variable_key='history_retry_stress'", [LOAD_CASE_ID])


def test_result_import_history_projects_v2_operation_and_revision() -> None:
    initialize_database()
    suffix = uuid4().hex[:10]
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    run_ids = [f"history-v2-run-{suffix}-{index}" for index in range(3)]
    job_ids = [f"history-v2-job-{suffix}-{index}" for index in range(3)]
    try:
        with connect() as conn:
            next_run_no = int(
                conn.execute("SELECT coalesce(max(run_no), 0) + 1 FROM analysis_runs WHERE load_case_id=?", [LOAD_CASE_ID]).fetchone()[0]
            )
            for index, run_id in enumerate(run_ids, start=1):
                conn.execute(
                    "INSERT INTO analysis_runs VALUES (?, ?, NULL, ?, 'pytest', 'COMPLETED', ?, ?)",
                    [run_id, LOAD_CASE_ID, next_run_no + index, now, now],
                )
                conn.execute(
                    """
                    INSERT INTO canonical_result_ingestion_source_versions
                        (load_case_id, source_type, source_key, source_run_id, source_name,
                         source_checksum, source_revision, analysis_run_id, conflict_policy,
                         supersedes_analysis_run_id, claimed_at)
                    VALUES (?, 'MASTER_FOLDER_REFRESH', ?, ?, ?, ?, ?, ?, 'SKIP', NULL, ?)
                    """,
                    [LOAD_CASE_ID, f"run:v2-{suffix}-{index}", f"v2-{suffix}-{index}", f"v2/{index}/manifest.json", f"{index:064x}", index, run_id, now],
                )
                status, reason = (
                    ("COMPLETED", "SOURCE_RUN_REPLACED") if index == 1 else
                    ("SKIPPED", "SOURCE_RUN_CHANGED_SKIPPED") if index == 2 else
                    ("REJECTED", "SOURCE_RUN_CHANGED_REJECTED")
                )
                conn.execute(
                    """
                    INSERT INTO folder_import_jobs
                        (id, load_case_id, analysis_run_id, schema_id, schema_version, source_folder,
                         status, summary_json, created_at, source_type, outcome_reason, completed_at)
                    VALUES (?, ?, ?, 'history-v2', 1, ?, ?, NULL, ?, 'MASTER_FOLDER_REFRESH', ?, ?)
                    """,
                    [job_ids[index - 1], LOAD_CASE_ID, run_id, f"v2/{index}/manifest.json", status, now, reason, now],
                )
        with TestClient(app) as client:
            response = client.get(f"/api/load-cases/{LOAD_CASE_ID}/result-imports")
            assert response.status_code == 200, response.text
            observed = {item["id"]: (item["operation"], item["source_revision"]) for item in response.json()["items"]}
            assert observed[job_ids[0]] == ("REPLACED", 1)
            assert observed[job_ids[1]] == ("NOOP", 2)
            assert observed[job_ids[2]] == ("REJECTED", 3)
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM folder_import_jobs WHERE id IN (?, ?, ?)", job_ids)
            for run_id in run_ids:
                conn.execute("DELETE FROM canonical_result_ingestion_source_versions WHERE analysis_run_id=?", [run_id])
                conn.execute("DELETE FROM analysis_runs WHERE id=?", [run_id])
