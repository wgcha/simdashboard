from __future__ import annotations

import ast
import inspect
from pathlib import Path

from app.main import app
from app.routers import workbench
from scripts.check_openapi_contract import check_contract


def test_workbench_catalog_routes_keep_their_contract_order_and_operation_ids() -> None:
    routes = [
        route
        for route in app.routes
        if getattr(route, "path", "") in {"/api/workbench/task-types", "/api/workbench/request-types"}
    ]

    assert [(route.path, tuple(sorted(route.methods or ()))) for route in routes] == [
        ("/api/workbench/task-types", ("GET",)),
        ("/api/workbench/request-types", ("GET",)),
    ]
    assert all(route.endpoint.__module__ == workbench.__name__ for route in routes)
    paths = app.openapi()["paths"]
    assert paths["/api/workbench/task-types"]["get"]["operationId"] == "list_task_types_api_workbench_task_types_get"
    assert paths["/api/workbench/request-types"]["get"]["operationId"] == "list_request_types_api_workbench_request_types_get"


def test_workbench_catalog_handlers_delegate_to_the_query_use_case() -> None:
    for handler in (workbench.list_task_types, workbench.list_request_types):
        tree = ast.parse(inspect.getsource(handler))
        assert not any(
            isinstance(node, ast.Attribute) and node.attr == "execute"
            for node in ast.walk(tree)
        )
    assert "WorkbenchRepository" not in inspect.getsource(workbench.list_task_types)
    assert "WorkbenchRepository" not in inspect.getsource(workbench.list_request_types)


def test_workbench_catalog_extraction_preserves_the_checked_in_openapi_contract() -> None:
    snapshot = Path(__file__).parents[2] / "frontend" / "openapi.json"
    assert check_contract(snapshot, app.openapi()) == []
