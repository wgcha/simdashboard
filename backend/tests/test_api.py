import json
import base64
import io
import os
import shutil
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.database import connect, initialize_database
from app.main import app
from app.media_policy import validate_media_metadata


def test_workspace_layout_versions_are_persisted():
    initialize_database()
    with TestClient(app) as client:
        initial = client.get("/api/workspace-layouts/portfolio")
        assert initial.status_code == 200
        original = initial.json()
        changed_definition = {**original["definition"], "fontSize": 11 if original["definition"]["fontSize"] != 11 else 12}
        try:
            saved = client.put("/api/workspace-layouts/portfolio", json={"definition": changed_definition, "updated_by": "테스트 편집자"})
            assert saved.status_code == 200, saved.text
            assert saved.json()["version"] == original["version"] + 1
            assert client.get("/api/workspace-layouts/portfolio").json()["definition"] == changed_definition
            versions = client.get("/api/workspace-layouts/portfolio/versions").json()
            assert versions[0]["version"] == saved.json()["version"]
        finally:
            restored = client.put("/api/workspace-layouts/portfolio", json={"definition": original["definition"], "updated_by": "테스트 복원"})
            assert restored.status_code == 200


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


def test_workflow_step_full_edit_and_validation():
    initialize_database()
    with TestClient(app) as client:
        workflow = client.get("/api/workflows").json()[0]
        original = workflow["steps"][0]
        payload = {
            "name": f'{original["name"]} 편집',
            "status": "IN_PROGRESS",
            "owner": "워크플로 편집자",
            "progress": 55,
            "is_optional": True,
            "note": "편집 기능 회귀 검증",
        }
        try:
            updated = client.patch(f'/api/workflow-steps/{original["id"]}', json=payload)
            assert updated.status_code == 200, updated.text
            assert all(updated.json()[key] == value for key, value in payload.items())
            refreshed = client.get("/api/workflows").json()
            saved = next(step for item in refreshed for step in item["steps"] if step["id"] == original["id"])
            assert all(saved[key] == value for key, value in payload.items())
            invalid = client.patch(f'/api/workflow-steps/{original["id"]}', json={**payload, "progress": 101})
            assert invalid.status_code == 422
        finally:
            client.patch(f'/api/workflow-steps/{original["id"]}', json={
                "name": original["name"],
                "status": original["status"],
                "owner": original["owner"],
                "progress": original["progress"],
                "is_optional": original["is_optional"],
                "note": original.get("note") or "",
            })


def test_workflow_steps_replace_supports_add_delete_and_reorder():
    initialize_database()
    request_id = ""
    with TestClient(app) as client:
        project_id = client.get("/api/projects").json()[0]["id"]
        created = client.post(f"/api/projects/{project_id}/requests", json={"title": "단계 편집 API 검증", "owner": "워크플로 편집자", "due_in_days": 7, "overall_note": "테스트 후 삭제"})
        assert created.status_code == 201, created.text
        request_id = created.json()["id"]
        workflow = next(item for item in client.get("/api/workflows").json() if item["request"]["id"] == request_id)
        first, second = workflow["steps"][:2]
        payload = {"steps": [
            {"id": second["id"], "name": "순서가 바뀐 두 번째 단계", "status": "IN_PROGRESS", "owner": "담당 B", "progress": 45, "is_optional": False, "note": "앞으로 이동"},
            {"id": first["id"], "name": first["name"], "status": first["status"], "owner": first["owner"], "progress": first["progress"], "is_optional": first["is_optional"], "note": first.get("note") or ""},
            {"id": None, "name": "새 승인 단계", "status": "WAITING", "owner": "담당 C", "progress": 0, "is_optional": True, "note": "신규 추가"},
        ]}
        try:
            replaced = client.put(f"/api/requests/{request_id}/workflow-steps", json=payload)
            assert replaced.status_code == 200, replaced.text
            steps = replaced.json()
            assert len(steps) == 3
            assert [step["sequence_no"] for step in steps] == [1, 2, 3]
            assert steps[0]["id"] == second["id"]
            assert steps[2]["name"] == "새 승인 단계"
            assert steps[2]["id"] not in {first["id"], second["id"]}
            assert client.put(f"/api/requests/{request_id}/workflow-steps", json={"steps": []}).status_code == 422
        finally:
            if request_id:
                with connect() as conn:
                    conn.execute("DELETE FROM request_steps WHERE request_id = ?", [request_id])
                    conn.execute("DELETE FROM analysis_requests WHERE id = ?", [request_id])


def test_report_layout_crud_and_version_history():
    initialize_database()
    with TestClient(app) as client:
        layouts = client.get("/api/report-layouts")
        assert layouts.status_code == 200
        assert len(layouts.json()) >= 3
        assert {item["definition"]["coverVariant"] for item in layouts.json()} >= {"balanced", "executive", "evidence"}
        payload = {
            "name": "테스트 보고서",
            "description": "변수 배치 회귀 검증",
            "definition": {
                "id": "client-placeholder",
                "name": "테스트 보고서",
                "description": "변수 배치 회귀 검증",
                "version": 1,
                "coverVariant": "balanced",
                "accentColor": "1898D5",
                "sectionOrder": ["scalar", "series", "media"],
                "variablePlacements": [{"variableKey": "top_edge_max_stress", "presentation": "table", "order": 0}],
                "includeMedia": False,
                "slideMaster": {"backgroundColor": "F4F8FB", "design": "header-band", "accentColor": "1898D5"},
                "canvas": {"columns": 32, "rows": 18, "widthInches": 13.333, "heightInches": 7.5},
                "slides": [{"id": "cover", "name": "표지", "kind": "cover", "repeat": "none", "style": {"useMaster": False, "backgroundColor": "FFFFFF", "design": "split", "accentColor": "FF9948"}, "elements": [{"id": "title", "type": "title", "label": "제목", "text": "직접 입력한 제목", "x": 1, "y": 1, "w": 20, "h": 2, "z": 1, "binding": {"source": "static"}}]}],
                "templateSource": "native",
                "templateBindings": {},
            },
            "updated_by": "테스트 편집자",
        }
        created = client.post("/api/report-layouts", json=payload)
        assert created.status_code == 201, created.text
        layout_id = created.json()["id"]
        payload["definition"]["accentColor"] = "FF9948"
        saved = client.put(f"/api/report-layouts/{layout_id}", json=payload)
        assert saved.status_code == 200
        assert saved.json()["version"] == 2
        assert saved.json()["definition"]["slideMaster"]["design"] == "header-band"
        assert saved.json()["definition"]["slides"][0]["elements"][0]["text"] == "직접 입력한 제목"
        versions = client.get(f"/api/report-layouts/{layout_id}/versions").json()
        assert [item["version"] for item in versions] == [2, 1]
        historical = client.get(f"/api/report-layouts/{layout_id}/versions/1")
        assert historical.status_code == 200
        assert historical.json()["definition"]["accentColor"] == "1898D5"
        assert client.delete(f"/api/report-layouts/{layout_id}").status_code == 200
        assert client.delete("/api/report-layouts/report-layout-standard").status_code == 409


def _minimal_tagged_pptx() -> bytes:
    buffer = io.BytesIO()
    presentation = b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:sldSz cx="12192000" cy="6858000"/></p:presentation>'''
    slide = b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree><p:sp><p:nvSpPr><p:cNvPr id="2" name="VAR:top_edge_max_stress"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="914400" y="914400"/><a:ext cx="3657600" cy="914400"/></a:xfrm></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>{{variable:top_edge_max_stress}}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>'''
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr("ppt/slides/slide1.xml", slide)
    return buffer.getvalue()


def test_pptx_template_upload_placeholder_inspection_and_render():
    initialize_database()
    template_id = None
    with TestClient(app) as client:
        response = client.post("/api/report-templates", json={"name": "태그 템플릿", "filename": "tagged.pptx", "content_base64": base64.b64encode(_minimal_tagged_pptx()).decode("ascii"), "updated_by": "테스트 편집자"})
        assert response.status_code == 201, response.text
        template = response.json()
        template_id = template["id"]
        assert template["slide_count"] == 1
        assert template["definition"]["placeholders"][0]["token"] == "variable:top_edge_max_stress"
        rendered = client.post(f"/api/report-templates/{template_id}/render", json={"replacements": {"variable:top_edge_max_stress": "72.50 MPa (PASS)"}, "filename": "해석 결과.pptx"})
        assert rendered.status_code == 200
        with zipfile.ZipFile(io.BytesIO(rendered.content)) as archive:
            assert "72.50 MPa (PASS)" in archive.read("ppt/slides/slide1.xml").decode("utf-8")
        assert client.delete(f"/api/report-templates/{template_id}").status_code == 200
    if template_id:
        with connect() as conn:
            conn.execute("DELETE FROM report_template_assets WHERE id=?", [template_id])


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
        expected_backend = os.getenv("ANALYSIS_DB_BACKEND", "duckdb").strip().lower()
        assert client.get("/api/health").json() == {"status": "ok", "database_backend": expected_backend}
        asset = client.get("/api/assets/media-contour-001")
        assert asset.status_code == 200
        assert asset.headers["content-type"].startswith("image/svg+xml")
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


def test_run_comparison_trust_and_review_are_additive():
    initialize_database()
    annotation_id = None
    bookmark_id = None
    with TestClient(app) as client:
        runs = client.get("/api/load-cases/loadcase-drop-bottom-001/runs")
        assert runs.status_code == 200, runs.text
        run_ids = {item["id"] for item in runs.json()}
        assert {"run-drop-baseline-001", "run-drop-001"} <= run_ids

        comparison = client.get(
            "/api/load-cases/loadcase-drop-bottom-001/run-comparison",
            params={"baseline_run_id": "run-drop-baseline-001", "target_run_id": "run-drop-001", "variable_key": "bottom_edge_stress_time"},
        )
        assert comparison.status_code == 200, comparison.text
        payload = comparison.json()
        bottom = next(item for item in payload["scalar_comparison"] if item["variable_key"] == "bottom_edge_max_stress")
        assert bottom["change"] == "REGRESSION"
        assert bottom["baseline_verdict"] == "PASS"
        assert bottom["target_verdict"] == "FAIL"
        assert payload["summary"]["regression"] >= 1
        assert payload["time_series"]["variable_key"] == "bottom_edge_stress_time"
        assert payload["time_series"]["points"]

        trust = client.get("/api/analysis-runs/run-drop-001/trust")
        assert trust.status_code == 200, trust.text
        trust_payload = trust.json()
        assert trust_payload["metadata"]["source_checksum"]
        assert trust_payload["coverage"]["result_variables"] >= 10
        assert {item["code"] for item in trust_payload["checks"]} >= {"run_status", "source_trace", "catalog_mapping", "unit_consistency", "validation"}

        created = client.post(
            "/api/analysis-runs/run-drop-001/review-items",
            json={
                "title": "하단 엣지 회귀 확인",
                "body": "기준 Run 대비 허용 응력을 초과했습니다.",
                "variable_key": "bottom_edge_max_stress",
                "time_value": 14.6,
                "entity_type": None,
                "entity_id": None,
                "review_status": "OPEN",
                "created_by": "테스트 검토자",
            },
        )
        assert created.status_code == 201, created.text
        annotation_id = created.json()["id"]
        bookmark_id = created.json()["bookmark_id"]
        assert created.json()["review_status"] == "OPEN"
        assert any(item["id"] == annotation_id for item in client.get("/api/analysis-runs/run-drop-001/review-items").json())
        resolved = client.patch(f"/api/review-items/{annotation_id}", json={"review_status": "RESOLVED"})
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["review_status"] == "RESOLVED"
    if annotation_id and bookmark_id:
        with connect() as conn:
            conn.execute("DELETE FROM review_annotations WHERE id=?", [annotation_id])
            conn.execute("DELETE FROM result_bookmarks WHERE id=?", [bookmark_id])


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


def test_typed_folder_example_registers_scalars_curves_media_and_catalog():
    initialize_database()
    load_case_id = "loadcase-clamp-left-001"
    with TestClient(app) as client:
        response = client.post(f"/api/load-cases/{load_case_id}/folder-import/example")
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["summary"] == {"scalar_count": 13, "curve_count": 2, "media_count": 1}
        overview = client.get(f"/api/load-cases/{load_case_id}/overview").json()
        assert overview["run"] == result["run_id"]
        assert len(overview["curves"]) == 2
        assert any(item["value_integer"] == 428120 for item in overview["scalar_results"])
        assert any(item["value_text"] for item in overview["scalar_results"])
        numeric = [item for item in overview["scalar_results"] if item["value_double"] is not None]
        assert len([item for item in numeric if not item["variable_key"].startswith("chassis_rear_")]) == 4
        assert len([item for item in numeric if item["variable_key"].startswith("chassis_rear_")]) == 6
        assert overview["media"][0]["asset_url"].startswith("/api/assets/")
        assert client.get(overview["media"][0]["asset_url"]).status_code == 200
        catalog = client.get(f"/api/load-cases/{load_case_id}/variables").json()
        assert {"NUMBER", "INTEGER", "TEXT", "CURVE", "IMAGE"} <= {item["data_type"] for item in catalog}

    with connect() as conn:
        asset_paths = [row[0] for row in conn.execute("SELECT file_path FROM media_assets WHERE analysis_run_id=?", [result["run_id"]]).fetchall()]
        curve_ids = [row[0] for row in conn.execute("SELECT id FROM curve_results WHERE analysis_run_id=?", [result["run_id"]]).fetchall()]
        for curve_id in curve_ids:
            conn.execute("DELETE FROM curve_points WHERE curve_id=?", [curve_id])
        conn.execute("DELETE FROM curve_results WHERE analysis_run_id=?", [result["run_id"]])
        conn.execute("DELETE FROM media_assets WHERE analysis_run_id=?", [result["run_id"]])
        conn.execute("DELETE FROM qualitative_notes WHERE analysis_run_id=?", [result["run_id"]])
        conn.execute("DELETE FROM time_series_results WHERE analysis_run_id=?", [result["run_id"]])
        conn.execute("DELETE FROM scalar_results WHERE analysis_run_id=?", [result["run_id"]])
        conn.execute("DELETE FROM folder_import_jobs WHERE id=?", [result["job_id"]])
        conn.execute("DELETE FROM analysis_runs WHERE id=?", [result["run_id"]])
        for key in ("mesh_element_count", "analysis_judgement", "chassis_rear_verdict", "open_cell_top_edge_stress_curve", "chassis_rear_top_edge_deformation_curve", "open_cell_stress_contour"):
            conn.execute("DELETE FROM variable_definitions WHERE load_case_id=? AND variable_key=?", [load_case_id, key])
    for path in asset_paths:
        asset_folder = Path(__file__).resolve().parents[1] / "assets" / Path(path).parent
        if asset_folder.exists():
            shutil.rmtree(asset_folder)


def test_import_schema_crud_and_hierarchy_mapping():
    payload = {
        "name": "TV 폴더 규칙",
        "description": "제품/의뢰/하중경우 계층",
        "definition": {"context_mapping": {"mode": "folder_levels", "project_level": 0, "request_level": 1, "load_case_level": 2}, "mappings": []},
        "updated_by": "테스트 관리자",
    }
    with TestClient(app) as client:
        created_response = client.post("/api/import-schemas", json=payload)
        assert created_response.status_code == 201, created_response.text
        created = created_response.json()
        assert created["definition"]["context_mapping"]["load_case_level"] == 2
        updated_response = client.put(f"/api/import-schemas/{created['id']}", json={**payload, "name": "TV 폴더 규칙 수정"})
        assert updated_response.status_code == 200, updated_response.text
        assert updated_response.json()["definition"]["version"] == 2
        assert any(item["name"] == "TV 폴더 규칙 수정" for item in client.get("/api/import-schemas").json())
        assert client.delete(f"/api/import-schemas/{created['id']}").status_code == 200
        assert all(item["id"] != created["id"] for item in client.get("/api/import-schemas").json())
    with connect() as conn:
        conn.execute("DELETE FROM import_schema_versions WHERE schema_id=?", [created["id"]])
        conn.execute("DELETE FROM import_schemas WHERE id=?", [created["id"]])


def test_feature_example_gallery_covers_major_states():
    initialize_database()
    with TestClient(app) as client:
        response = client.get("/api/feature-examples")
        assert response.status_code == 200
        examples = {item["id"]: item for item in response.json()}
        assert len(examples) >= 12
        assert {"run-comparison", "trust-ready", "trust-warning", "review-flow", "multi-type", "data-waiting", "workflow-states", "ppt-layout"} <= set(examples)
        assert examples["run-comparison"]["data_profile"]["runs"] == 3
        assert examples["review-flow"]["data_profile"]["reviews"] == 3
        assert examples["multi-type"]["data_profile"]["curves"] == 1
        assert examples["multi-type"]["data_profile"]["media"] == 1

        comparison = client.get(
            "/api/load-cases/loadcase-showcase-compare/run-comparison",
            params={"baseline_run_id": "run-showcase-compare-2", "target_run_id": "run-showcase-compare-3"},
        )
        assert comparison.status_code == 200
        assert comparison.json()["summary"] == {"regression": 1, "improved": 1, "unchanged": 2, "comparable": 4}

        assert client.get("/api/analysis-runs/run-showcase-trust-2/trust").json()["trust_status"] == "TRUSTED"
        warning = client.get("/api/analysis-runs/run-showcase-warning-2/trust").json()
        assert warning["trust_status"] == "WARN"
        assert "unmapped_hotspot" in warning["coverage"]["unmapped"]

        reviews = client.get("/api/analysis-runs/run-showcase-review-2/review-items").json()
        assert {item["review_status"] for item in reviews} == {"OPEN", "IN_REVIEW", "RESOLVED"}

        waiting = client.get("/api/load-cases/loadcase-showcase-waiting/variables").json()
        assert len(waiting) == 5
        assert {item["data_type"] for item in waiting} == {"NUMBER", "TIME_SERIES", "IMAGE", "VIDEO", "MODEL_3D"}
        assert all(item["has_data"] is False for item in waiting)

        workflow = client.get("/api/requests/request-showcase-workflow/workflow").json()
        assert {step["status"] for step in workflow["steps"]} >= {"COMPLETED", "IN_PROGRESS", "BLOCKED", "WAITING"}


def test_feature_example_seed_is_idempotent():
    initialize_database()
    initialize_database()
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM projects WHERE id='project-feature-showcase'").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM analysis_runs WHERE id='run-showcase-compare-3'").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM result_locations WHERE analysis_run_id='run-showcase-warning-2' AND variable_key='unmapped_hotspot'").fetchone()[0] == 1
