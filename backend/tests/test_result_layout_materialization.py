from uuid import uuid4

from fastapi.testclient import TestClient

from app.database import connect
from app.main import app


def _request_type_payload(suffix: str) -> dict:
    return {
        "display_name": "결과 편집 " + suffix,
        "description": "snapshot을 사용자 dashboard로 전환합니다.",
        "allowed_task_types": [{"id": "analysis-db-publish", "version": 1}],
        "default_workflow": {"nodes": [{"node_key": "publish", "task_type_id": "analysis-db-publish", "task_type_version": 1, "depends_on": []}]},
        "match_rules": {"labels": ["result-edit"]},
        "result_definition": {
            "page_name": "요청 결과",
            "widgets": [{"id": "summary-" + suffix, "type": "summary", "title": "결과 요약", "variable_key": "run_summary", "data_contracts": ["RESULT_RUN"], "required": True}],
        },
        "is_active": True,
    }


def _materialize_context(client: TestClient, suffix: str, *, configured: bool = True) -> tuple[str, str]:
    request_type_payload = _request_type_payload(suffix)
    if not configured:
        request_type_payload.pop("result_definition")
    request_type = client.post("/api/admin/workbench/request-types", json=request_type_payload)
    assert request_type.status_code == 201, request_type.text
    request = client.post(
        "/api/projects/project-tv-001/requests",
        json={
            "title": "결과 편집 의뢰 " + suffix,
            "owner_user_id": "local-admin",
            "due_in_days": 14,
            "overall_note": "사용자 편집 dashboard를 생성합니다.",
            "source_type": "EXTERNAL_SYSTEM",
            "source_reference": "result-layout-materialize-test",
            "requested_by": "test",
            "request_type_id": request_type.json()["id"],
            "request_type_version": 1,
        },
    )
    assert request.status_code == 201, request.text
    load_case = client.post(
        f"/api/requests/{request.json()['id']}/load-cases",
        json={"name": "결과 하중 경우 " + suffix, "analysis_type": "DROP", "parameters": {}},
    )
    assert load_case.status_code == 201, load_case.text
    return request.json()["id"], load_case.json()["id"]


def test_result_snapshot_materializes_an_idempotent_editable_dashboard() -> None:
    suffix = uuid4().hex[:8]
    with TestClient(app) as client:
        request_id, load_case_id = _materialize_context(client, suffix)
        original_snapshot = client.get(f"/api/workbench/requests/{request_id}/result-layout").json()["snapshot"]
        first = client.post(
            f"/api/workbench/requests/{request_id}/result-layout/materialize",
            json={"load_case_id": load_case_id},
        )
        assert first.status_code == 201, first.text
        definition = first.json()
        assert definition["widgets"][0]["id"] == "summary-" + suffix
        assert definition["widgets"][0]["settings"]["variable_key"] == "run_summary"
        assert definition["widgets"][0]["settings"]["variableId"] == "run_summary"
        assert definition["page"] == {"kind": "analysis_page", "analysis_key": "custom", "status": "published", "display_order": 100, "is_system": False}

        repeated = client.post(
            f"/api/workbench/requests/{request_id}/result-layout/materialize",
            json={"load_case_id": load_case_id},
        )
        assert repeated.status_code == 201, repeated.text
        assert repeated.json()["id"] == definition["id"]
        public_pages = client.get("/api/dashboard-pages", params={"load_case_id": load_case_id})
        assert public_pages.status_code == 200, public_pages.text
        assert definition["id"] in {page["id"] for page in public_pages.json()}
        assert client.get(f"/api/workbench/requests/{request_id}/result-layout").json()["snapshot"] == original_snapshot
        saved = client.put(f"/api/dashboards/{definition['id']}", json=definition)
        assert saved.status_code == 200, saved.text

    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM dashboards WHERE id=?", [definition["id"]]).fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM dashboard_versions WHERE dashboard_id=?", [definition["id"]]).fetchone()[0] == 2



def test_unconfigured_request_materializes_an_empty_published_dashboard() -> None:
    suffix = uuid4().hex[:8]
    with TestClient(app) as client:
        request_id, load_case_id = _materialize_context(client, suffix, configured=False)
        assert client.get(f"/api/workbench/requests/{request_id}/result-layout").json()["status"] == "UNCONFIGURED"
        first = client.post(
            f"/api/workbench/requests/{request_id}/result-layout/materialize",
            json={"load_case_id": load_case_id},
        )
        assert first.status_code == 201, first.text
        definition = first.json()
        assert definition["name"] == "요청 결과 사용자 편집"
        assert definition["widgets"] == []
        assert definition["page"]["status"] == "published"
        repeated = client.post(
            f"/api/workbench/requests/{request_id}/result-layout/materialize",
            json={"load_case_id": load_case_id},
        )
        assert repeated.status_code == 201, repeated.text
        assert repeated.json()["id"] == definition["id"]
        public_pages = client.get("/api/dashboard-pages", params={"load_case_id": load_case_id})
        assert definition["id"] in {page["id"] for page in public_pages.json()}
        selected_page = client.post(
            f"/api/workbench/requests/{request_id}/result-layout/materialize",
            json={"load_case_id": load_case_id, "page_id": "missing"},
        )
        assert selected_page.status_code == 404
        assert selected_page.json()["detail"]["code"] == "RESULT_LAYOUT_SNAPSHOT_NOT_FOUND"


def test_result_snapshot_materialize_rejects_a_load_case_from_another_request() -> None:
    suffix = uuid4().hex[:8]
    with TestClient(app) as client:
        request_id, _ = _materialize_context(client, suffix)
        rejected = client.post(
            f"/api/workbench/requests/{request_id}/result-layout/materialize",
            json={"load_case_id": "loadcase-clamp-left-001"},
        )
        assert rejected.status_code == 404
        assert rejected.json()["detail"]["code"] == "LOAD_CASE_NOT_FOUND"
