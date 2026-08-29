from __future__ import annotations

import ast
import inspect
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.database_connection import connect
from app.main import app
from app.routers import workbench
from scripts.check_openapi_contract import check_contract


def test_workbench_catalog_routes_keep_their_contract_order_and_operation_ids() -> None:
    routes = [
        route
        for route in app.routes
        if getattr(route, "path", "")
        in {
            "/api/workbench/task-types",
            "/api/workbench/request-types",
            "/api/workbench/requests/{request_id}/request-type",
            "/api/workbench/requests/{request_id}/work-plan",
        }
    ]

    assert [(route.path, tuple(sorted(route.methods or ()))) for route in routes] == [
        ("/api/workbench/task-types", ("GET",)),
        ("/api/workbench/request-types", ("GET",)),
        ("/api/workbench/requests/{request_id}/request-type", ("GET",)),
        ("/api/workbench/requests/{request_id}/request-type", ("PUT",)),
        ("/api/workbench/requests/{request_id}/work-plan", ("GET",)),
    ]
    assert all(route.endpoint.__module__ == workbench.__name__ for route in routes)
    paths = app.openapi()["paths"]
    assert paths["/api/workbench/task-types"]["get"]["operationId"] == "list_task_types_api_workbench_task_types_get"
    assert paths["/api/workbench/request-types"]["get"]["operationId"] == "list_request_types_api_workbench_request_types_get"
    assert (
        paths["/api/workbench/requests/{request_id}/request-type"]["get"]["operationId"]
        == "resolve_request_type_api_workbench_requests__request_id__request_type_get"
    )
    assert (
        paths["/api/workbench/requests/{request_id}/work-plan"]["get"]["operationId"]
        == "get_request_work_plan_api_workbench_requests__request_id__work_plan_get"
    )


def test_workbench_read_handlers_delegate_to_query_use_cases() -> None:
    for handler in (
        workbench.list_task_types,
        workbench.list_request_types,
        workbench.resolve_request_type,
        workbench.get_request_work_plan,
    ):
        tree = ast.parse(inspect.getsource(handler))
        assert not any(
            isinstance(node, ast.Attribute) and node.attr == "execute"
            for node in ast.walk(tree)
        )
    assert "WorkbenchRepository" not in inspect.getsource(workbench.list_task_types)
    assert "WorkbenchRepository" not in inspect.getsource(workbench.list_request_types)
    assert "WorkbenchRepository" not in inspect.getsource(workbench.resolve_request_type)
    assert "WorkbenchRepository" not in inspect.getsource(workbench.get_request_work_plan)


def test_request_type_resolution_keeps_the_existing_missing_request_response_without_authentication() -> None:
    with TestClient(app) as client:
        response = client.get("/api/workbench/requests/request-type-missing-test/request-type")

    assert response.status_code == 404
    assert response.json() == {"detail": "해석 의뢰를 찾을 수 없습니다."}


def test_request_work_plan_keeps_existing_success_and_not_found_responses_without_authentication() -> None:
    request_id = f"request-work-plan-unconfigured-{uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with TestClient(app) as client:
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO analysis_requests
                    (id, project_id, title, status, owner, requested_at, due_at, overall_note)
                VALUES (?, 'project-tv-001', '계획 없는 의뢰', 'READY', '테스트 담당자', ?, ?, '')
                """,
                [request_id, now, now],
            )
        try:
            missing_request = client.get("/api/workbench/requests/request-work-plan-missing-test/work-plan")
            assert missing_request.status_code == 404
            assert missing_request.json() == {
                "detail": {"code": "REQUEST_NOT_FOUND", "request_id": "request-work-plan-missing-test"}
            }

            missing_plan = client.get(f"/api/workbench/requests/{request_id}/work-plan")
            assert missing_plan.status_code == 404
            assert missing_plan.json() == {
                "detail": {"code": "WORK_PLAN_NOT_FOUND", "request_id": request_id}
            }

            success = client.get("/api/workbench/requests/request-drop-001/work-plan")
            assert success.status_code == 200
            assert success.json()["request_id"] == "request-drop-001"
            assert success.json()["work_plan"] is not None
        finally:
            with connect() as conn:
                conn.execute("DELETE FROM analysis_requests WHERE id=?", [request_id])


def test_workbench_catalog_extraction_preserves_the_checked_in_openapi_contract() -> None:
    snapshot = Path(__file__).parents[2] / "frontend" / "openapi.json"
    assert check_contract(snapshot, app.openapi()) == []
