from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import app.main as main_module
from app.adapters.http.routers import request_load_cases as load_cases_router
from app.adapters.persistence.request_load_cases import SQLRequestLoadCasesRepository
from app.application.request_load_cases.queries import get_load_cases
from app.database_connection import connect
from app.domains.request_load_cases.policies import load_cases_with_parameters
from app.main import app


class _Cursor:
    def __init__(self, columns: list[str], values: list[tuple[Any, ...]]) -> None:
        self._columns, self._values = columns, values

    def keys(self) -> list[str]:
        return self._columns

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._values


class _Connection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows, self.calls = rows, []

    def __enter__(self) -> "_Connection":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def execute(self, statement: str, parameters: Any | None = None) -> _Cursor:
        self.calls.append((statement, parameters))
        columns = list(self.rows[0]) if self.rows else ["id"]
        return _Cursor(columns, [tuple(row[column] for column in columns) for row in self.rows])


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


ROW = {
    "id": "loadcase-1", "request_id": "request-1", "name": "Case",
    "analysis_type": "DROP", "status": "READY", "parameters_json": '{"mass": 10}',
    "created_at": "2026-01-01T00:00:00",
}


@pytest.mark.contract
def test_load_case_route_contract_and_global_order() -> None:
    routes = [route for route in app.routes if isinstance(route, APIRoute)]
    selected = [
        (route.path, tuple(sorted(route.methods or ())), route.endpoint.__name__)
        for route in routes
        if route.path in {
            "/api/projects/{project_id}/requests",
            "/api/projects/{project_id}/requests",
            "/api/requests/{request_id}/load-cases",
            "/api/load-cases/{load_case_id}/drop-videos",
        }
        and not (route.path == "/api/requests/{request_id}/load-cases" and "POST" in (route.methods or ()))
    ]
    assert selected == [
        ("/api/projects/{project_id}/requests", ("GET",), "get_requests"),
        ("/api/projects/{project_id}/requests", ("POST",), "create_request"),
        ("/api/requests/{request_id}/load-cases", ("GET",), "get_load_cases"),
        ("/api/load-cases/{load_case_id}/drop-videos", ("GET",), "get_drop_videos"),
    ]
    route = next(
        route
        for route in routes
        if route.path == "/api/requests/{request_id}/load-cases" and route.methods == {"GET"}
    )
    assert route.response_model == list[dict[str, Any]]
    assert (route.operation_id or route.unique_id) == "get_load_cases_api_requests__request_id__load_cases_get"
    operation = app.openapi()["paths"]["/api/requests/{request_id}/load-cases"]["get"]
    assert set(operation["responses"]) == {"200", "422"}


@pytest.mark.contract
def test_main_relinquishes_load_case_route_sql_and_json_transform_with_ceiling_68() -> None:
    path = Path(main_module.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    actual = sum(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "execute" for n in ast.walk(tree))
    baseline = json.loads((path.parents[1] / "scripts" / "architecture_baseline.json").read_text(encoding="utf-8"))
    assert actual <= 68
    assert baseline["execute_call_ceilings"]["app/main.py"] == actual
    source = path.read_text(encoding="utf-8")
    assert "def get_load_cases(" not in source
    assert "SELECT * FROM load_cases WHERE request_id = ? ORDER BY created_at" not in source
    assert "parameters_json" not in source
    router_source = Path(load_cases_router.__file__).read_text(encoding="utf-8")
    assert router_source.count(".execute(") == 0
    assert "require_permission" not in router_source
    assert "write_audit_event" not in router_source
    assert "BEGIN" not in router_source and "COMMIT" not in router_source and "ROLLBACK" not in router_source


@pytest.mark.unit
def test_application_query_calls_provider_once() -> None:
    class Repository:
        def list_load_cases(self, request_id: str) -> list[dict[str, Any]]:
            self.request_id = request_id
            return [{"id": "loadcase-1", "parameters_json": '{"mass": 10}'}]

    repository = Repository()
    assert get_load_cases("request-1", _Provider(repository)) == [{"id": "loadcase-1", "parameters": {"mass": 10}}]
    assert repository.request_id == "request-1"


@pytest.mark.unit
def test_adapter_uses_exact_sql_and_same_connection() -> None:
    connection = _Connection([ROW])
    result = SQLRequestLoadCasesRepository(connection).list_load_cases("request-1")  # type: ignore[arg-type]
    assert result[0]["id"] == "loadcase-1"
    assert connection.calls == [("SELECT * FROM load_cases WHERE request_id = ? ORDER BY created_at", ["request-1"])]


@pytest.mark.unit
def test_domain_projection_mutates_same_list_and_rows_and_decodes_parameters() -> None:
    row = dict(ROW)
    items = [row]
    projected = load_cases_with_parameters(items)
    assert projected is items
    assert projected[0] is row
    assert row["parameters"] == {"mass": 10}
    assert "parameters_json" not in row


@pytest.mark.duckdb_integration
def test_seeded_http_projection_order_missing_request_and_malformed_json() -> None:
    with connect() as connection:
        connection.execute(
            "INSERT INTO load_cases VALUES (?, ?, ?, ?, ?, ?, ?)",
            ["loadcase-query-late", "request-drop-001", "Late", "DROP", "READY", '{"z": 1}', "2099-01-01"],
        )
        connection.execute(
            "INSERT INTO load_cases VALUES (?, ?, ?, ?, ?, ?, ?)",
            ["loadcase-query-early", "request-drop-001", "Early", "DROP", "READY", '{"a": 1}', "2000-01-01"],
        )
    with TestClient(app) as client:
        response = client.get("/api/requests/request-drop-001/load-cases")
        assert response.status_code == 200
        body = response.json()
        assert [item["id"] for item in body[:2]] == ["loadcase-query-early", "loadcase-drop-bottom-001"]
        assert all("parameters_json" not in item for item in body)
        assert all("parameters" in item for item in body)
        assert client.get("/api/requests/does-not-exist/load-cases").json() == []

    malformed = dict(ROW, parameters_json="not-json")
    result = load_cases_with_parameters([malformed])
    assert result[0]["parameters"] == "not-json"
    assert "parameters_json" not in result[0]
