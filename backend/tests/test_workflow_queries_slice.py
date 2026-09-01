from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
import app.main as main_module
from app.adapters.http.routers import workflow_queries as workflow_router
from app.adapters.persistence import workflow_queries as workflow_persistence
from app.adapters.persistence.workflow_queries import SQLWorkflowQueriesRepository
from app.database_connection import connect
from app.main import app


class _Cursor:
    def __init__(self, columns: list[str], values: list[tuple[Any, ...]]) -> None:
        self._columns = columns
        self._values = values

    def keys(self) -> list[str]:
        return self._columns

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._values


class _Connection:
    backend = "fake"

    def __init__(self, request_rows: list[dict[str, Any]], list_rows: list[dict[str, Any]]) -> None:
        self.request_rows = request_rows
        self.list_rows = list_rows
        self.calls: list[tuple[str, Any]] = []

    def __enter__(self) -> "_Connection":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def execute(self, statement: str, parameters: Any | None = None) -> _Cursor:
        self.calls.append((statement, parameters))
        if "FROM analysis_requests WHERE id" in statement:
            rows = self.request_rows
        elif "FROM analysis_requests ar" in statement:
            rows = self.list_rows
        else:  # request_monitoring_summary is replaced in these seam tests.
            raise AssertionError(f"unexpected SQL: {statement}")
        columns = list(rows[0]) if rows else ["id"]
        return _Cursor(columns, [tuple(row[column] for column in columns) for row in rows])


class _Provider:
    def __init__(self, repository: Any) -> None:
        self.repository = repository

    def __call__(self):
        class _Context:
            def __enter__(_self):
                return self.repository

            def __exit__(_self, *_args: Any) -> None:
                return None

        return _Context()


class _Repository:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        self.request_rows = connection.request_rows
        self.list_rows = connection.list_rows
        self.seen: list[tuple[Any, str]] = []

    def analysis_request(self, request_id: str) -> dict[str, Any] | None:
        return next((row for row in self.request_rows if row["id"] == request_id), None)

    def workflow_requests(self) -> list[dict[str, Any]]:
        return self.list_rows

    def monitoring_summary(self, request_id: str) -> dict[str, Any]:
        self.seen.append((self.connection, request_id))
        return _summary()


REQUEST = {
    "id": "request-1",
    "project_id": "project-1",
    "title": "Request",
    "status": "READY",
    "owner": "Owner",
    "owner_user_id": "user-1",
    "requested_at": "2026-01-01T00:00:00",
    "due_at": None,
    "overall_note": "note",
}


def _summary(*, status: str = "IN_PROGRESS") -> dict[str, Any]:
    return {
        "steps": [{"id": "step-1", "name": "Step", "status": status}],
        "progress": 50,
        "current_step": "Step",
        "current_step_id": "step-1",
        "completed_count": 1,
        "total_count": 2,
        "work_plan": None,
        "latest_demo_run": None,
        "request_type_assignment": None,
        "status": status,
    }


def _routes() -> list[APIRoute]:
    return [
        route
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.path in {"/api/requests/{request_id}/workflow", "/api/workflows"}
    ]


@pytest.mark.contract
def test_workflow_query_routes_have_stable_contract_and_order() -> None:
    routes = _routes()
    assert [(route.path, tuple(sorted(route.methods or ()))) for route in routes] == [
        ("/api/requests/{request_id}/workflow", ("GET",)),
        ("/api/workflows", ("GET",)),
    ]
    assert [route.endpoint.__name__ for route in routes] == ["get_workflow", "get_workflows"]
    assert [(route.operation_id or route.unique_id) for route in routes] == [
        "get_workflow_api_requests__request_id__workflow_get",
        "get_workflows_api_workflows_get",
    ]
    assert [route.response_model for route in routes] == [dict[str, Any], list[dict[str, Any]]]
    spec = app.openapi()["paths"]
    assert set(spec["/api/requests/{request_id}/workflow"]["get"]["responses"]) == {"200", "422"}
    assert set(spec["/api/workflows"]["get"]["responses"]) == {"200"}


@pytest.mark.unit
def test_single_workflow_uses_one_connection_and_overwrites_request_status(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _Connection([REQUEST], [])
    seen: list[Any] = []

    def summary(request_id: str) -> dict[str, Any]:
        seen.append((connection, request_id))
        return _summary(status="COMPLETED")

    repository = _Repository(connection)
    repository.monitoring_summary = summary  # type: ignore[method-assign]
    monkeypatch.setattr(workflow_router, "SQLWorkflowQueriesRepositoryProvider", lambda: _Provider(repository))
    body = workflow_router.get_workflow("request-1")
    assert body["request"]["status"] == "COMPLETED"
    assert list(body) == [
        "request", "steps", "progress", "current_step", "current_step_id",
        "completed_count", "total_count", "work_plan", "latest_demo_run", "request_type_assignment",
    ]
    assert seen == [(connection, "request-1")]
    SQLWorkflowQueriesRepository(connection).analysis_request("request-1")
    assert len(connection.calls) == 1
    assert connection.calls[0][1] == ["request-1"]


@pytest.mark.unit
def test_single_workflow_returns_404_without_monitoring(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _Connection([], [])
    called = False

    def summary(*_args: Any) -> dict[str, Any]:
        nonlocal called
        called = True
        return _summary()

    repository = _Repository(connection)
    repository.monitoring_summary = summary  # type: ignore[method-assign]
    monkeypatch.setattr(workflow_router, "SQLWorkflowQueriesRepositoryProvider", lambda: _Provider(repository))
    with pytest.raises(Exception) as exc_info:
        workflow_router.get_workflow("missing")
    assert getattr(exc_info.value, "status_code", None) == 404
    assert called is False


@pytest.mark.unit
def test_workflow_list_orders_sql_and_monitors_each_request_on_same_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    second = {**REQUEST, "id": "request-2", "requested_at": "2026-02-01T00:00:00"}
    connection = _Connection([], [REQUEST, second])
    seen: list[tuple[Any, str]] = []

    def summary(request_id: str) -> dict[str, Any]:
        seen.append((connection, request_id))
        return _summary()

    repository = _Repository(connection)
    repository.monitoring_summary = summary  # type: ignore[method-assign]
    monkeypatch.setattr(workflow_router, "SQLWorkflowQueriesRepositoryProvider", lambda: _Provider(repository))
    body = workflow_router.get_workflows()
    assert [item["request"]["id"] for item in body] == ["request-1", "request-2"]
    assert seen == [(connection, "request-1"), (connection, "request-2")]
    sql_connection = _Connection([], [REQUEST, second])
    SQLWorkflowQueriesRepository(sql_connection).workflow_requests()
    assert len(sql_connection.calls) == 1
    sql = sql_connection.calls[0][0]
    assert "LEFT JOIN load_cases lc ON lc.request_id = ar.id" in sql
    assert "GROUP BY ar.id" in sql
    assert "ORDER BY ar.requested_at DESC" in sql


@pytest.mark.contract
def test_workflow_query_handlers_relinquish_sql_and_mutation_hooks() -> None:
    source = (Path(main_module.__file__)).read_text(encoding="utf-8")
    assert "def get_workflow(" not in source
    assert "def get_workflows(" not in source
    assert "app.include_router(workflow_queries_router)" in source
    router_source = Path(workflow_router.__file__).read_text(encoding="utf-8")
    assert ".execute(" not in router_source
    assert "require_permission" not in router_source
    assert "write_audit_event" not in router_source


@pytest.mark.contract
def test_quality_workflow_and_workflow_step_routes_are_contiguous_in_app_order() -> None:
    routes = [route for route in app.routes if isinstance(route, APIRoute)]
    selected = [
        (route.path, tuple(sorted(route.methods or ())), route.endpoint.__name__)
        for route in routes
        if route.path in {
            "/api/projects/{project_id}/quality-thresholds",
            "/api/projects/{project_id}/quality-thresholds/{criterion_key}",
            "/api/quality-thresholds/{criterion_key}",
            "/api/requests/{request_id}/workflow",
            "/api/workflows",
            "/api/requests/{request_id}/workflow-steps",
            "/api/workflow-steps/{step_id}",
        }
    ]
    assert selected == [
        ("/api/projects/{project_id}/quality-thresholds", ("GET",), "get_quality_thresholds"),
        ("/api/projects/{project_id}/quality-thresholds/{criterion_key}", ("PUT",), "update_project_quality_threshold"),
        ("/api/quality-thresholds/{criterion_key}", ("PUT",), "update_quality_threshold"),
        ("/api/requests/{request_id}/workflow", ("GET",), "get_workflow"),
        ("/api/workflows", ("GET",), "get_workflows"),
        ("/api/requests/{request_id}/workflow-steps", ("PUT",), "replace_workflow_steps"),
        ("/api/workflow-steps/{step_id}", ("PATCH",), "update_workflow_step"),
    ]


@pytest.mark.contract
def test_main_execute_ast_matches_architecture_ceiling_and_workflow_sql_is_relinquished() -> None:
    main_path = Path(main_module.__file__)
    tree = ast.parse(main_path.read_text(encoding="utf-8"))
    actual = sum(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
        for node in ast.walk(tree)
    )
    baseline = __import__("json").loads(
        (main_path.parents[1] / "scripts" / "architecture_baseline.json").read_text(encoding="utf-8")
    )
    assert actual == 69
    assert baseline["execute_call_ceilings"]["app/main.py"] == 69
    source = main_path.read_text(encoding="utf-8")
    assert "request_monitoring_summary" not in source
    assert "SELECT * FROM analysis_requests WHERE id = ?" not in source
    assert "FROM analysis_requests ar" not in source


@pytest.mark.unit
def test_sql_adapter_monitoring_summary_receives_its_own_connection_and_request_id(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = object()
    seen: list[tuple[Any, str]] = []
    monkeypatch.setattr(
        workflow_persistence,
        "request_monitoring_summary",
        lambda conn, request_id: seen.append((conn, request_id)) or _summary(),
    )
    SQLWorkflowQueriesRepository(connection).monitoring_summary("request-1")  # type: ignore[arg-type]
    assert seen == [(connection, "request-1")]


@pytest.mark.duckdb_integration
def test_seeded_detail_list_projection_404_order_and_unassigned_load_case() -> None:
    request_id = "workflow-query-no-load-case"
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO analysis_requests
                (id, project_id, title, status, owner, requested_at, due_at, overall_note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [request_id, "project-tv-001", "No load case", "READY", "Owner", "2020-01-01", None, "query slice"],
        )
    with TestClient(app) as client:
        detail_response = client.get("/api/requests/request-drop-001/workflow")
        assert detail_response.status_code == 200
        detail = detail_response.json()
        listed_response = client.get("/api/workflows")
        assert listed_response.status_code == 200
        listed = listed_response.json()
        assert [item["request"]["requested_at"] for item in listed] == sorted(
            (item["request"]["requested_at"] for item in listed), reverse=True
        )
        listed_detail = next(item for item in listed if item["request"]["id"] == "request-drop-001")
        assert {key: listed_detail[key] for key in detail if key != "request"} == {
            key: detail[key] for key in detail if key != "request"
        }
        request_keys = detail["request"].keys()
        assert {key: listed_detail["request"][key] for key in request_keys} == detail["request"]
        no_load = next(item for item in listed if item["request"]["id"] == request_id)
        assert no_load["request"]["category"] == "UNASSIGNED"
        assert no_load["request"]["load_case_name"] == "하중 경우 미지정"
        missing = client.get("/api/requests/does-not-exist/workflow")
        assert missing.status_code == 404
        assert missing.json()["detail"] == "해석 의뢰를 찾을 수 없습니다."
