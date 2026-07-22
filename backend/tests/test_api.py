import json

from fastapi.testclient import TestClient

from app.database import connect, initialize_database
from app.main import app
from app.media_policy import validate_media_metadata


def test_portfolio_metrics_filters_and_csv_reconcile():
    initialize_database()
    with TestClient(app) as client:
        payload = client.get("/api/portfolio/overview").json()
        assert payload["grain"] == "LOAD_CASE_LATEST_RUN"
        assert payload["kpis"]["load_cases"] == len(payload["records"])
        assert sum(item["value"] for item in payload["type_distribution"]) == len(payload["records"])
        filtered = client.get("/api/portfolio/overview", params={"analysis_type": "DROP"}).json()
        assert filtered["records"]
        assert all(item["analysis_type"] == "DROP" for item in filtered["records"])
        searched = client.get("/api/portfolio/overview", params={"search": "존재하지않는검색어"}).json()
        assert searched["kpis"]["load_cases"] == 0
        csv_response = client.get("/api/portfolio/export.csv", params={"analysis_type": "DROP"})
        assert csv_response.status_code == 200
        assert "project_name" in csv_response.text
        assert "DROP" in csv_response.text


def test_widget_catalog_and_dashboard_version_flows():
    initialize_database()
    with TestClient(app) as client:
        catalog = client.get("/api/widget-catalog").json()
        assert {item["type"] for item in catalog} >= {"kpi", "gauge", "time_series", "video", "model3d", "workflow"}
        source = client.get("/api/dashboards/dashboard-drop-default").json()
        clone = client.post("/api/dashboards/dashboard-drop-default/clone", json={"name": "테스트 복제본", "description": "회귀 검증"})
        assert clone.status_code == 201
        clone_id = clone.json()["id"]
        assert client.get(f"/api/dashboards/{clone_id}").json()["name"] == "테스트 복제본"
        saved = client.put(f"/api/dashboards/{clone_id}", json={**client.get(f"/api/dashboards/{clone_id}").json(), "description": "변경됨"})
        assert saved.status_code == 200
        restored = client.post(f"/api/dashboards/{clone_id}/restore/1")
        assert restored.status_code == 200
        assert client.get(f"/api/dashboards/{clone_id}").json()["description"] == "회귀 검증"
    with connect() as conn:
        conn.execute("DELETE FROM dashboard_versions WHERE dashboard_id = ?", [clone_id])
        conn.execute("DELETE FROM dashboards WHERE id = ?", [clone_id])


def test_media_metadata_policy_rejects_unsafe_paths_and_formats():
    validate_media_metadata("MODEL_3D", "results/tv/lightweight.glb", 1024)
    for args in [
        ("MODEL_3D", "../original.fem", 100),
        ("MODEL_3D", "results/heavy.obj", 100),
        ("VIDEO", "https://example.com/result.mp4", 100),
        ("CONTOUR_IMAGE", "results/plot.png", 30 * 1024 * 1024),
    ]:
        try:
            validate_media_metadata(*args)
            assert False, f"unsafe metadata accepted: {args}"
        except ValueError:
            pass


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
        assert len(workflows) >= 2
        assert {"DROP", "SIDE_CLAMP"} <= {item["request"]["category"] for item in workflows}
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
        improvement = client.post("/api/dashboard-commands/preview", json={"command": "패스/실패 카드를 맨 위 오른쪽으로 이동해"}).json()
        assert improvement["recognized"] is True
        assert improvement["proposal"]["action"] == "update_widgets"
        unsafe = client.post("/api/dashboard-commands/preview", json={"command": "데이터베이스를 삭제해"})
        assert unsafe.json()["recognized"] is False


def test_default_dashboard_has_persisted_widgets():
    initialize_database()
    with TestClient(app) as client:
        response = client.get("/api/dashboards/dashboard-drop-default")
        assert response.status_code == 200
        assert any(item["id"] == "dashboard-drop-default" for item in client.get("/api/dashboards").json())
        widget_types = {widget["type"] for widget in response.json()["widgets"]}
        assert {"open_cell_map", "verdict", "edge_bar", "time_series", "result_table"} <= widget_types
        chassis = client.get("/api/dashboards/dashboard-chassis-default")
        assert chassis.status_code == 200
        assert {"chassis_summary", "chassis_diagram", "chassis_bar", "chassis_table"} <= {widget["type"] for widget in chassis.json()["widgets"]}
        variables = client.get("/api/load-cases/loadcase-drop-bottom-001/variables").json()
        assert variables and all("allowed_aggregations" in item and "description" in item for item in variables)
        templates = client.get("/api/automation-templates").json()
        assert {item["analysis_type"] for item in templates} >= {"DROP", "SIDE_CLAMP"}


def test_variable_catalog_crud_persists_and_protects_dashboard_bindings():
    initialize_database()
    load_case_id = "loadcase-drop-bottom-001"
    variable_key = "custom_frame_energy"
    dashboard_id = "dashboard-variable-reference-test"
    create_payload = {
        "variable_key": variable_key,
        "display_name": "프레임 흡수 에너지",
        "data_type": "NUMBER",
        "unit": "J",
        "description": "사용자 정의 에너지 결과",
        "threshold": 120.0,
        "allowed_widgets": ["kpi", "edge_bar", "result_table"],
        "allowed_aggregations": ["MAX", "AVG", "LATEST"],
        "result_group": "CUSTOM",
        "updated_by": "테스트 관리자",
    }
    with TestClient(app) as client:
        created = client.post(f"/api/load-cases/{load_case_id}/variables", json=create_payload)
        assert created.status_code == 201, created.text
        assert created.json()["id"] == variable_key
        assert created.json()["has_data"] is False

        preview = client.post(
            f"/api/load-cases/{load_case_id}/results/import",
            json={
                "filename": "custom-variable.csv",
                "content": "record_type,variable_key,display_name,value,unit,threshold,time,time_unit,value_unit\nscalar,custom_frame_energy,프레임 흡수 에너지,96.4,J,120,,,,\n",
                "author": "테스트 관리자",
                "validate_only": True,
            },
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["results"][0]["variable_key"] == variable_key

        updated = client.put(
            f"/api/load-cases/{load_case_id}/variables/{variable_key}",
            json={**{key: value for key, value in create_payload.items() if key not in {"variable_key", "data_type"}}, "display_name": "프레임 에너지", "threshold": 130.0},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["display_name"] == "프레임 에너지"
        assert updated.json()["threshold"] == 130.0
        assert any(item["id"] == variable_key for item in client.get(f"/api/load-cases/{load_case_id}/variables").json())

        with connect() as conn:
            stored = conn.execute(
                "SELECT display_name, threshold_double, is_active FROM variable_definitions WHERE load_case_id=? AND variable_key=?",
                [load_case_id, variable_key],
            ).fetchone()
            assert stored == ("프레임 에너지", 130.0, True)
            definition = json.dumps({"id": dashboard_id, "name": "test", "description": "", "widgets": [{"id": "test-widget", "type": "kpi", "title": "test", "x": 0, "y": 0, "w": 3, "h": 2, "settings": {"variableId": variable_key}}]})
            conn.execute("INSERT INTO dashboards VALUES (?, 'project-tv-001', 'request-drop-001', ?, 'test', '', 1, ?, now())", [dashboard_id, load_case_id, definition])

        blocked = client.delete(f"/api/load-cases/{load_case_id}/variables/{variable_key}")
        assert blocked.status_code == 409

        with connect() as conn:
            conn.execute("DELETE FROM dashboards WHERE id = ?", [dashboard_id])
        deleted = client.delete(f"/api/load-cases/{load_case_id}/variables/{variable_key}")
        assert deleted.status_code == 200
        assert all(item["id"] != variable_key for item in client.get(f"/api/load-cases/{load_case_id}/variables").json())

    with connect() as conn:
        conn.execute("DELETE FROM variable_definitions WHERE load_case_id=? AND variable_key=?", [load_case_id, variable_key])


def test_create_project_request_and_load_case():
    created_project = None
    created_request = None
    created_load_case = None
    created_run = None
    with TestClient(app) as client:
        project_response = client.post(
            "/api/projects",
            json={"name": "등록 API 검증 프로젝트", "product_name": "Test TV", "manufacturer": "Test Display", "display_size_inch": 55, "description": "test"},
        )
        assert project_response.status_code == 201
        created_project = project_response.json()["id"]
        with connect() as conn:
            metadata = {row[0]: row[1] for row in conn.execute("SELECT category, value_text FROM product_information WHERE project_id = ?", [created_project]).fetchall()}
        assert metadata == {"MODEL": "Test TV", "MANUFACTURER": "Test Display", "SPEC": "55 inch"}
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
        result_content = json.dumps({
            "solver": "Test Solver",
            "note": "업로드 검증 의견",
            "scalar_results": [
                {"variable_key": "top_edge_max_stress", "value": 70.0, "unit": "MPa", "threshold": 75.0},
                {"variable_key": "bottom_edge_max_stress", "value": 80.0, "unit": "MPa", "threshold": 75.0},
                {"variable_key": "chassis_rear_top_edge_gap_permanent_deformation", "value": 5.0, "unit": "mm"},
            ],
            "time_series": [
                {"variable_key": "top_edge_stress_time", "time": 0, "value": 0, "time_unit": "ms", "value_unit": "MPa"},
                {"variable_key": "top_edge_stress_time", "time": 10, "value": 70, "time_unit": "ms", "value_unit": "MPa"},
            ],
        }, ensure_ascii=False)
        invalid_response = client.post(
            f"/api/load-cases/{created_load_case}/results/import",
            json={"filename": "invalid.json", "content": json.dumps({"scalar_results": [{"variable_key": "unknown_result", "value": 1}]}), "author": "테스트", "validate_only": True},
        )
        assert invalid_response.status_code == 422
        radioss_sample = client.get("/api/result-import/template/radioss-csv")
        assert radioss_sample.status_code == 200
        radioss_preview = client.post(
            f"/api/load-cases/{created_load_case}/results/import",
            json={"filename": "radioss-example.csv", "content": radioss_sample.text, "author": "테스트", "validate_only": True},
        )
        assert radioss_preview.status_code == 200
        assert radioss_preview.json()["source_format"] == "RADIOSS_MESH_CSV"
        assert radioss_preview.json()["node_count"] == 111
        assert radioss_preview.json()["element_count"] == 64
        assert radioss_preview.json()["scalar_count"] == 10
        derived_values = {item["variable_key"]: item["value"] for item in radioss_preview.json()["results"]}
        assert derived_values["bottom_edge_max_stress"] == 84.0
        assert 6.39 < derived_values["chassis_rear_top_edge_gap_permanent_deformation"] < 6.41
        assert 4.29 < derived_values["chassis_rear_bottom_edge_gap_permanent_deformation"] < 4.31
        preview_response = client.post(
            f"/api/load-cases/{created_load_case}/results/import",
            json={"filename": "result.json", "content": result_content, "author": "테스트", "validate_only": True},
        )
        assert preview_response.status_code == 200
        assert preview_response.json()["status"] == "VALID"
        assert preview_response.json()["fail_count"] == 2
        import_response = client.post(
            f"/api/load-cases/{created_load_case}/results/import",
            json={"filename": "radioss-example.csv", "content": radioss_sample.text, "author": "테스트"},
        )
        assert import_response.status_code == 200
        assert import_response.json()["status"] == "IMPORTED"
        created_run = import_response.json()["run_id"]
        overview = client.get(f"/api/load-cases/{created_load_case}/overview").json()
        assert overview["analysis_verdicts"] == {"open_cell": "FAIL", "chassis_rear": "FAIL"}
        assert len(overview["time_series"]) == 12
        assert len(overview["result_locations"]) == 10
        locations = {item["variable_key"]: item for item in overview["result_locations"]}
        assert locations["bottom_edge_max_stress"]["entity_id"] == "5001"
        assert locations["chassis_rear_top_edge_gap_permanent_deformation"]["entity_id"] == "2014"
        workflow = client.get(f"/api/requests/{created_request}/workflow").json()["steps"]
        assert next(step for step in workflow if step["name"] == "후처리 작업")["status"] == "COMPLETED"
        assert next(step for step in workflow if step["name"] == "결과 검토")["status"] == "IN_PROGRESS"
    with connect() as conn:
        if created_run:
            conn.execute("DELETE FROM qualitative_notes WHERE analysis_run_id = ?", [created_run])
            conn.execute("DELETE FROM result_locations WHERE analysis_run_id = ?", [created_run])
            conn.execute("DELETE FROM time_series_results WHERE analysis_run_id = ?", [created_run])
            conn.execute("DELETE FROM scalar_results WHERE analysis_run_id = ?", [created_run])
            conn.execute("DELETE FROM analysis_runs WHERE id = ?", [created_run])
        if created_request:
            conn.execute("DELETE FROM request_steps WHERE request_id = ?", [created_request])
            conn.execute("DELETE FROM load_cases WHERE request_id = ?", [created_request])
            conn.execute("DELETE FROM analysis_requests WHERE id = ?", [created_request])
        if created_project:
            conn.execute("DELETE FROM product_information WHERE project_id = ?", [created_project])
            conn.execute("DELETE FROM projects WHERE id = ?", [created_project])
