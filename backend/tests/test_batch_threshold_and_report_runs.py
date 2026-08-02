import json

from fastapi.testclient import TestClient

from app.database import connect
from app.main import app


def test_quality_thresholds_only_recalculate_matching_unit_and_result_semantics():
    with connect() as conn:
        rows = (
            ("test-chassis-mm", "custom_permanent_deformation", 9.0, "mm", 5.0, "FAIL"),
            ("test-chassis-mpa", "custom_permanent_deformation", 9.0, "MPa", 5.0, "PASS"),
            ("test-open-mpa", "custom_stress_peak", 90.0, "MPa", 75.0, "FAIL"),
            ("test-open-mm", "custom_stress_peak_mm", 90.0, "mm", 75.0, "PASS"),
        )
        for item_id, key, value, unit, threshold, verdict in rows:
            conn.execute(
                "INSERT INTO scalar_results VALUES (?, 'run-drop-001', ?, ?, ?, NULL, NULL, ?, ?, ?)",
                [item_id, key, key, value, unit, threshold, verdict],
            )

    with TestClient(app) as client:
        assert client.put("/api/quality-thresholds/open_cell_stress_mpa", json={"threshold_double": 85, "updated_by": "테스트 관리자"}).status_code == 200
        assert client.put("/api/quality-thresholds/chassis_rear_permanent_deformation_mm", json={"threshold_double": 8, "updated_by": "테스트 관리자"}).status_code == 200

    with connect() as conn:
        values = {row[0]: (row[1], row[2]) for row in conn.execute("SELECT id, threshold_double, verdict FROM scalar_results WHERE id LIKE 'test-%'").fetchall()}
    assert values["test-chassis-mm"] == (8.0, "FAIL")
    assert values["test-chassis-mpa"] == (5.0, "PASS")
    assert values["test-open-mpa"] == (85.0, "FAIL")
    assert values["test-open-mm"] == (75.0, "PASS")


def test_report_overview_can_select_a_run_but_rejects_another_load_case_run():
    with TestClient(app) as client:
        baseline = client.get("/api/load-cases/loadcase-drop-bottom-001/overview", params={"run_id": "run-drop-baseline-001"})
        assert baseline.status_code == 200
        assert baseline.json()["run"] == "run-drop-baseline-001"
        wrong = client.get("/api/load-cases/loadcase-drop-bottom-001/overview", params={"run_id": "run-clamp-001"})
        assert wrong.status_code == 404


def test_work_item_progress_and_safe_batch_dispatch_are_persistent():
    with TestClient(app) as client:
        workflow = client.get("/api/requests/request-drop-001/workflow").json()
        current = next(item for item in workflow["steps"] if item["status"] == "IN_PROGRESS")
        changed = client.patch(
            f"/api/workbench/work-items/{current['id']}/progress",
            json={"progress": 40, "updated_by": "실행 담당자"},
        )
        assert changed.status_code == 200, changed.text
        assert next(item for item in changed.json()["steps"] if item["id"] == current["id"])["progress"] == 40
        assert changed.json()["progress"] == 40
        rejected = client.patch(
            f"/api/workbench/work-items/{current['id']}/progress",
            json={"progress": 30, "updated_by": "실행 담당자"},
        )
        assert rejected.status_code == 409
        assert rejected.json()["detail"]["code"] == "WORK_ITEM_PROGRESS_NOT_MONOTONIC"

        profiles = client.get("/api/workbench/batch-profiles").json()
        assert profiles and profiles[0]["environment"]
        assert current["task_type_id"] in profiles[0]["task_type_ids"]
        incompatible_profile = {
            "id": "post-only-demo",
            "name": "후처리 전용 데모",
            "solver_path": "C:\\Demo\\post.exe",
            "working_directory": "C:\\Demo\\runs\\{request_id}",
            "arguments_template": "--input {input}",
            "environment": {},
            "task_type_ids": ["post-process"],
            "is_active": True,
            "updated_by": "테스트 관리자",
        }
        assert client.put("/api/admin/workbench/batch-profiles/post-only-demo", json=incompatible_profile).status_code == 200
        incompatible_dispatch = client.post(
            f"/api/workbench/work-items/{current['id']}/batch-dispatch",
            json={"batch_profile_id": "post-only-demo", "created_by": "실행 담당자"},
        )
        assert incompatible_dispatch.status_code == 409
        assert incompatible_dispatch.json()["detail"]["code"] == "BATCH_PROFILE_TASK_MISMATCH"
        dispatched = client.post(
            f"/api/workbench/work-items/{current['id']}/batch-dispatch",
            json={"batch_profile_id": profiles[0]["id"], "created_by": "실행 담당자"},
        )
        assert dispatched.status_code == 201, dispatched.text
        payload = dispatched.json()
        assert payload["execution_mode"] == "DEMO_ONLY"
        assert payload["batch_dispatch"]["status"] == "RECORDED_DEMO"
        assert profiles[0]["solver_path"] in payload["batch_dispatch"]["command_preview"]

    with connect() as conn:
        stored = conn.execute("SELECT status, profile_snapshot_json FROM batch_dispatches WHERE workflow_run_id=?", [payload["id"]]).fetchone()
        assert stored and stored[0] == "RECORDED_DEMO"
        snapshot = json.loads(stored[1]) if isinstance(stored[1], str) else stored[1]
        assert snapshot["solver_path"] == profiles[0]["solver_path"]
