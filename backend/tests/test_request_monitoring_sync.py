from __future__ import annotations

from fastapi.testclient import TestClient

from app.database_connection import connect
from app.main import app


def _step_payload(step: dict, *, status: str, progress: int) -> dict:
    return {
        "name": step["name"],
        "status": status,
        "owner": step["owner"],
        "progress": progress,
        "is_optional": step["is_optional"],
        "note": step.get("note") or "",
    }


def test_workflow_and_portfolio_share_canonical_request_monitoring_values():
    request_id = "request-drop-001"
    with TestClient(app) as client:
        original = client.get(f"/api/requests/{request_id}/workflow").json()
        current = next(step for step in original["steps"] if step["status"] == "IN_PROGRESS")
        changed = client.post(
            f"/api/workbench/work-items/{current['id']}/complete",
            json={"completed_by": "동기화 테스트"},
        )
        assert changed.status_code == 200, changed.text

        workflow = client.get(f"/api/requests/{request_id}/workflow").json()
        workflow_list_item = next(item for item in client.get("/api/workflows").json() if item["request"]["id"] == request_id)
        portfolio_records = [item for item in client.get("/api/portfolio/overview").json()["records"] if item["request_id"] == request_id]
        request_row = next(item for item in client.get("/api/projects/project-tv-001/requests").json() if item["id"] == request_id)

        assert portfolio_records
        assert workflow["progress"] == workflow_list_item["progress"] == portfolio_records[0]["request_progress"] == 50
        assert workflow["current_step"] == workflow_list_item["current_step"] == portfolio_records[0]["current_step"] == "결과 후처리"
        assert workflow["request"]["status"] == workflow_list_item["request"]["status"] == portfolio_records[0]["request_status"] == request_row["status"]
        assert all(item["request_progress"] == portfolio_records[0]["request_progress"] for item in portfolio_records)


def test_latest_demo_run_projection_is_shared_by_request_and_portfolio():
    request_id = "request-drop-001"
    with TestClient(app) as client:
        created = client.post(
            "/api/workbench/demo-runs",
            json={
                "name": "모니터링 정본 검증 데모",
                "request_id": request_id,
                "execution_mode": "DEMO_ONLY",
                "created_by": "동기화 테스트",
                "nodes": [{"node_key": "hpc-submit", "task_type_id": "hpc-submit", "task_type_version": 1, "depends_on": []}],
            },
        )
        assert created.status_code == 201, created.text
        run = created.json()
        workflow = client.get(f"/api/requests/{request_id}/workflow").json()
        portfolio = next(item for item in client.get("/api/portfolio/overview").json()["records"] if item["request_id"] == request_id)

        assert workflow["latest_demo_run"]["id"] == portfolio["latest_demo_run"]["id"] == run["id"]
        assert workflow["latest_demo_run"]["status"] == portfolio["latest_demo_run"]["status"] == "SUCCEEDED"
        assert workflow["latest_demo_run"]["progress"] == portfolio["latest_demo_run"]["progress"] == 100


def test_request_without_load_case_remains_visible_in_operational_monitoring():
    with TestClient(app) as client:
        project = client.post(
            "/api/projects",
            json={"name": "CAD 단독 의뢰 프로젝트", "product_name": "Concept A", "manufacturer": "Demo", "display_size_inch": None, "description": "load case 없음"},
        )
        assert project.status_code == 201, project.text
        with connect() as conn:
            assignee = conn.execute(
                """
                SELECT users.id FROM users
                JOIN project_memberships memberships ON memberships.user_id=users.id
                WHERE memberships.project_id=? AND users.account_status='ACTIVE'
                ORDER BY users.id LIMIT 1
                """,
                [project.json()["id"]],
            ).fetchone()
        assert assignee
        request = client.post(
            f"/api/projects/{project.json()['id']}/requests",
            json={
                "title": "CAD 형상 준비만 수행",
                "owner_user_id": assignee[0],
                "due_in_days": 3,
                "overall_note": "CAD_ONLY",
                "source_type": "EXTERNAL_SYSTEM",
                "source_reference": "CAD PDM",
                "requested_by": "형상 자동화 시스템",
            },
        )
        assert request.status_code == 201, request.text

        records = [item for item in client.get("/api/portfolio/overview").json()["records"] if item["request_id"] == request.json()["id"]]
        assert len(records) == 1
        assert records[0]["load_case_id"] == ""
        assert records[0]["load_case_name"] == "하중 경우 미지정"
        assert records[0]["analysis_type"] == "UNASSIGNED"
        assert records[0]["request_progress"] == 0
        assert records[0]["current_step"] == "CAD 작업"
