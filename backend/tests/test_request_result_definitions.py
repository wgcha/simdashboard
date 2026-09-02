from uuid import uuid4

from fastapi.testclient import TestClient

from app.database import connect
from app.main import app


def _payload(name: str, *, widgets: list[dict], task_id: str = "analysis-db-publish") -> dict:
    return {
        "display_name": name,
        "description": "작업 유형 안에서 결과 위젯을 정의합니다.",
        "allowed_task_types": [{"id": task_id, "version": 1}],
        "default_workflow": {"nodes": [{"node_key": "publish", "task_type_id": task_id, "task_type_version": 1, "depends_on": []}]},
        "match_rules": {"labels": ["result-definition"]},
        "result_definition": {
            "page_name": "요청 결과",
            "page_description": "의뢰 결과를 요약합니다.",
            "widgets": widgets,
        },
        "is_active": True,
    }


def _widgets(suffix: str) -> list[dict]:
    return [
        {
            "id": f"max-stress-{suffix}",
            "type": "kpi",
            "title": "최대 응력",
            "variable_key": "max_stress",
            "data_contracts": ["scalar_result"],
            "required": True,
        },
        {
            "id": f"run-summary-{suffix}",
            "type": "summary",
            "title": "해석 실행 요약",
            "data_contracts": ["result_run"],
            "required": False,
        },
    ]


def test_request_result_definition_compiles_to_published_template_and_profile() -> None:
    suffix = uuid4().hex[:8]
    with TestClient(app) as client:
        created = client.post("/api/admin/workbench/request-types", json=_payload("결과 정의 " + suffix, widgets=_widgets(suffix)))
        assert created.status_code == 201, created.text
        request_type = created.json()
        profile = client.get(f"/api/workbench/request-types/{request_type['id']}/1/result-profile")
        assert profile.status_code == 200, profile.text
        body = profile.json()
        assert body["included_widget_ids"] == [f"max-stress-{suffix}", f"run-summary-{suffix}"]
        assert body["required_data_contracts"] == ["SCALAR_RESULT", "RESULT_RUN"]
        assert body["template"]["lifecycle_status"] == "PUBLISHED"
        assert body["template"]["scope_kind"] == "SYSTEM"
        widgets = body["template"]["page_definitions"][0]["widgets"]
        assert widgets[0]["settings"] == {"data_contracts": ["SCALAR_RESULT"], "required": True, "variable_key": "max_stress"}
        assert widgets[0]["x"] == 0 and widgets[1]["x"] == 6


def test_request_result_definition_versions_template_family_with_request_type() -> None:
    suffix = uuid4().hex[:8]
    with TestClient(app) as client:
        created = client.post("/api/admin/workbench/request-types", json=_payload("결과 버전 " + suffix, widgets=_widgets(suffix)))
        assert created.status_code == 201, created.text
        request_type = created.json()
        updated_widgets = _widgets(suffix) + [{"id": f"trend-{suffix}", "type": "time_series", "title": "추이", "data_contracts": ["time_series"], "required": False}]
        updated = client.put(f"/api/admin/workbench/request-types/{request_type['id']}", json=_payload("결과 버전 변경 " + suffix, widgets=updated_widgets))
        assert updated.status_code == 201, updated.text
        assert updated.json()["version"] == 2
        first = client.get(f"/api/workbench/request-types/{request_type['id']}/1/result-profile").json()
        second = client.get(f"/api/workbench/request-types/{request_type['id']}/2/result-profile").json()
        assert first["template_id"] == second["template_id"]
        assert (first["template_version"], second["template_version"]) == (1, 2)
        assert second["included_widget_ids"][-1] == f"trend-{suffix}"


def test_result_definition_contracts_are_saved_for_later_uploads() -> None:
    suffix = uuid4().hex[:8]
    name = "업로드 대기 결과 " + suffix
    body = _payload(name, widgets=_widgets(suffix), task_id="cad-prepare")
    with TestClient(app) as client:
        created = client.post("/api/admin/workbench/request-types", json=body)
        assert created.status_code == 201, created.text
        request_type = created.json()
        request = client.post(
            "/api/projects/project-tv-001/requests",
            json={
                "title": "업로드 대기 의뢰 " + suffix,
                "owner_user_id": "local-admin",
                "due_in_days": 14,
                "overall_note": "폴더 refresh 결과를 기다립니다.",
                "source_type": "EXTERNAL_SYSTEM",
                "source_reference": "request-result-definition-test",
                "requested_by": "test",
                "request_type_id": request_type["id"],
                "request_type_version": request_type["version"],
            },
        )
        assert request.status_code == 201, request.text
        layout = client.get(f"/api/workbench/requests/{request.json()['id']}/result-layout")
        assert layout.status_code == 200, layout.text
        assert layout.json()["snapshot"]["pages"][0]["widgets"][0]["id"] == f"max-stress-{suffix}"
        assert "RESULT_RUN" not in layout.json()["bindings"]["available_data_contracts"]
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM request_type_versions WHERE display_name=?", [name]).fetchone()[0] == 1


def test_legacy_result_profile_remains_supported() -> None:
    suffix = uuid4().hex[:8]
    template_id = "legacy-result-template-" + suffix
    widget_id = "legacy-result-widget-" + suffix
    template = {
        "id": template_id,
        "display_name": "기존 프로필 " + suffix,
        "description": "기존 템플릿 선택 경로",
        "page_definitions": [{"id": "legacy-page-" + suffix, "name": "기존 결과", "description": "", "widgets": [{"id": widget_id, "type": "summary", "title": "요약", "x": 0, "y": 0, "w": 6, "h": 3, "settings": {"data_contracts": ["RESULT_RUN"]}}]}],
        "lifecycle_status": "PUBLISHED",
        "scope_kind": "SYSTEM",
    }
    with TestClient(app) as client:
        assert client.post("/api/admin/workbench/analysis-templates", json=template).status_code == 201
        payload = _payload("기존 프로필 유형 " + suffix, widgets=_widgets(suffix), task_id="cad-prepare")
        payload.pop("result_definition")
        payload["result_profile"] = {"template_id": template_id, "template_version": 1, "included_widget_ids": [widget_id], "overrides": {}, "required_data_contracts": ["RESULT_RUN"]}
        created = client.post("/api/admin/workbench/request-types", json=payload)
        assert created.status_code == 201, created.text
        profile = client.get(f"/api/workbench/request-types/{created.json()['id']}/1/result-profile").json()
        assert profile["template_id"] == template_id
