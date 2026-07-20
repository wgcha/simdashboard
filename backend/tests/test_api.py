from fastapi.testclient import TestClient

from app.database import connect, initialize_database
from app.main import app


def test_health_and_seeded_overview():
    initialize_database()
    with TestClient(app) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        response = client.get("/api/load-cases/loadcase-drop-bottom-001/overview")
        assert response.status_code == 200
        payload = response.json()
        assert payload["load_case"]["analysis_type"] == "DROP"
        assert payload["overall_verdict"] == "FAIL"
        open_cell_results = [item for item in payload["scalar_results"] if not item["variable_key"].startswith("chassis_rear_")]
        assert len(open_cell_results) == 4
        assert len(payload["time_series"]) == 404
        product_values = {item["category"]: item["value_text"] for item in payload["product_information"]}
        assert product_values["SPEC"] == "65 inch"
        assert product_values["MODEL"] == "ORION-65-OLED-C"
        assert payload["analysis_verdicts"]["open_cell"] == "FAIL"
        assert payload["analysis_verdicts"]["chassis_rear"] == "FAIL"
        chassis_results = [item for item in payload["scalar_results"] if item["variable_key"].startswith("chassis_rear_")]
        assert len(chassis_results) == 6


def test_hierarchy_and_workflow():
    initialize_database()
    with TestClient(app) as client:
        load_cases = client.get("/api/requests/request-drop-001/load-cases").json()
        assert len(load_cases) == 1
        assert load_cases[0]["id"] == "loadcase-drop-bottom-001"
        workflow = client.get("/api/requests/request-drop-001/workflow").json()
        assert len(workflow["steps"]) == 10
        assert workflow["steps"][0]["name"] == "의뢰 접수"
        assert workflow["steps"][3]["name"] == "해석 전처리 모델링"
        assert workflow["steps"][5]["name"] == "후처리 작업"
        assert workflow["steps"][7]["is_optional"] is True
        workflows = client.get("/api/workflows").json()
        assert len(workflows) == 2
        assert {item["request"]["category"] for item in workflows} == {"DROP", "SIDE_CLAMP"}
        clamp_overview = client.get("/api/load-cases/loadcase-clamp-left-001/overview").json()
        assert clamp_overview["analysis_verdicts"]["open_cell"] == "FAIL"
        assert clamp_overview["analysis_verdicts"]["chassis_rear"] == "FAIL"
        clamp_open_cell = [item for item in clamp_overview["scalar_results"] if not item["variable_key"].startswith("chassis_rear_")]
        assert len(clamp_open_cell) == 4
        assert len(clamp_overview["time_series"]) == 244


def test_chassis_threshold_is_admin_configurable():
    initialize_database()
    with TestClient(app) as client:
        thresholds = client.get("/api/projects/project-tv-001/quality-thresholds").json()
        assert thresholds[0]["criterion_key"] == "chassis_rear_permanent_deformation_mm"
        response = client.put(
            "/api/quality-thresholds/chassis_rear_permanent_deformation_mm",
            json={"threshold_double": 5.0, "updated_by": "관리자"},
        )
        assert response.status_code == 200
        assert response.json()["threshold_double"] == 5.0


def test_natural_language_preview_is_allowlisted():
    with TestClient(app) as client:
        response = client.post("/api/dashboard-commands/preview", json={"command": "응력-시간 그래프를 추가해"})
        assert response.json()["recognized"] is True
        unsafe = client.post("/api/dashboard-commands/preview", json={"command": "데이터베이스를 삭제해"})
        assert unsafe.json()["recognized"] is False


def test_default_dashboard_has_persisted_widgets():
    initialize_database()
    with TestClient(app) as client:
        response = client.get("/api/dashboards/dashboard-drop-default")
        assert response.status_code == 200
        widget_types = {widget["type"] for widget in response.json()["widgets"]}
        assert {"open_cell_map", "verdict", "edge_bar", "time_series", "result_table"} <= widget_types


def test_create_project_request_and_load_case():
    created_project = None
    created_request = None
    created_load_case = None
    with TestClient(app) as client:
        project_response = client.post(
            "/api/projects",
            json={"name": "등록 API 검증 프로젝트", "product_name": "Test TV", "description": "test"},
        )
        assert project_response.status_code == 201
        created_project = project_response.json()["id"]
        request_response = client.post(
            f"/api/projects/{created_project}/requests",
            json={"title": "Side Clamp 검증 의뢰", "owner": "테스트", "due_in_days": 5},
        )
        assert request_response.status_code == 201
        created_request = request_response.json()["id"]
        load_case_response = client.post(
            f"/api/requests/{created_request}/load-cases",
            json={"name": "0.40 MPa Side Clamp", "analysis_type": "SIDE_CLAMP", "parameters": {"pressure_mpa": 0.4}},
        )
        assert load_case_response.status_code == 201
        created_load_case = load_case_response.json()["id"]
        assert len(client.get(f"/api/requests/{created_request}/workflow").json()["steps"]) == 10
    with connect() as conn:
        if created_request:
            conn.execute("DELETE FROM request_steps WHERE request_id = ?", [created_request])
            conn.execute("DELETE FROM load_cases WHERE request_id = ?", [created_request])
            conn.execute("DELETE FROM analysis_requests WHERE id = ?", [created_request])
        if created_project:
            conn.execute("DELETE FROM projects WHERE id = ?", [created_project])
