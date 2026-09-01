from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from starlette.requests import Request

import app.main as main_module
from app.adapters.http.routers import analysis_runs
from app.main import app


RUNS_PATH = "/api/load-cases/{load_case_id}/runs"


@pytest.mark.contract
def test_analysis_runs_router_owns_exact_route_openapi_and_global_adjacency() -> None:
    routes = [route for route in app.routes if isinstance(route, APIRoute)]
    selected = next(route for route in routes if route.path == RUNS_PATH)

    assert selected.methods == {"GET"}
    assert selected.endpoint.__module__ == analysis_runs.__name__
    assert selected.endpoint.__name__ == "list_analysis_runs"
    assert (selected.operation_id or selected.unique_id) == (
        "list_analysis_runs_api_load_cases__load_case_id__runs_get"
    )
    assert selected.response_model == list[dict[str, Any]]
    assert selected.status_code is None

    operation = app.openapi()["paths"][RUNS_PATH]["get"]
    assert operation["operationId"] == "list_analysis_runs_api_load_cases__load_case_id__runs_get"
    assert set(operation["responses"]) == {"200", "422"}
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "type": "array",
        "items": {"type": "object", "additionalProperties": True},
        "title": "Response List Analysis Runs Api Load Cases  Load Case Id  Runs Get",
    }
    assert operation["parameters"] == [{
        "name": "load_case_id",
        "in": "path",
        "required": True,
        "schema": {"type": "string", "title": "Load Case Id"},
    }]

    index = routes.index(selected)
    cluster = routes[index - 1:index + 3]
    assert [
        (route.path, tuple(sorted(route.methods or ())), route.endpoint.__module__, route.endpoint.__name__)
        for route in cluster
    ] == [
        (
            "/api/load-cases/{load_case_id}/overview",
            ("GET",),
            "app.adapters.http.routers.load_case_overview",
            "get_load_case_overview",
        ),
        (RUNS_PATH, ("GET",), analysis_runs.__name__, "list_analysis_runs"),
        (
            "/api/load-cases/{load_case_id}/run-comparison",
            ("GET",),
            "app.adapters.http.routers.analysis_insights",
            "compare_analysis_runs",
        ),
        (
            "/api/analysis-runs/{run_id}/trust",
            ("GET",),
            "app.adapters.http.routers.analysis_insights",
            "get_analysis_run_trust",
        ),
    ]

    unaffected = {
        (route.path, next(iter(route.methods or ()))): (
            route.endpoint.__module__, route.endpoint.__name__, route.operation_id or route.unique_id,
        )
        for route in routes
        if route.path in {
            "/api/drop-videos/{video_id}/content",
            "/api/drop-videos/{video_id}/download",
            "/api/requests/{request_id}/load-cases",
        }
        and not (route.path == "/api/requests/{request_id}/load-cases" and route.methods != {"POST"})
    }
    assert unaffected == {
        ("/api/drop-videos/{video_id}/content", "HEAD"): (
            "app.main", "get_drop_video_content", "get_drop_video_content_api_drop_videos__video_id__content_head",
        ),
        ("/api/drop-videos/{video_id}/content", "GET"): (
            "app.main", "get_drop_video_content", "get_drop_video_content",
        ),
        ("/api/drop-videos/{video_id}/download", "HEAD"): (
            "app.main", "download_drop_video", "download_drop_video_api_drop_videos__video_id__download_head",
        ),
        ("/api/drop-videos/{video_id}/download", "GET"): (
            "app.main", "download_drop_video", "download_drop_video",
        ),
        ("/api/requests/{request_id}/load-cases", "POST"): (
            "app.main", "create_load_case", "create_load_case_api_requests__request_id__load_cases_post",
        ),
    }


@pytest.mark.contract
def test_main_relinquishes_runs_handler_imports_and_keeps_exact_execute_baseline() -> None:
    main_path = Path(main_module.__file__)
    source = main_path.read_text(encoding="utf-8")
    assert "def list_analysis_runs(" not in source
    assert "SQLAnalysisRunSummaryRepositoryProvider" not in source
    assert "list_analysis_runs_query" not in source
    assert "app.include_router(analysis_runs_router)" in source

    tree = ast.parse(source)
    actual = sum(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
        for node in ast.walk(tree)
    )
    baseline = json.loads(
        (main_path.parents[1] / "scripts" / "architecture_baseline.json").read_text(encoding="utf-8")
    )
    assert actual <= 58
    assert baseline["execute_call_ceilings"]["app/main.py"] == actual

    router_source = Path(analysis_runs.__file__).read_text(encoding="utf-8")
    assert ".execute(" not in router_source
    assert "write_audit_event" not in router_source
    assert all(token not in router_source for token in ("BEGIN", "COMMIT", "ROLLBACK"))


@pytest.mark.unit
def test_router_passes_company_permission_callback_and_provider_to_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[Any, ...]] = []
    provider = object()
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/api/load-cases/load-a/runs",
        "headers": [],
        "query_string": b"",
    })

    def require_permission(bound_request: Request, permission: str) -> object:
        events.append(("permission", bound_request, permission))
        return object()

    def query(load_case_id: str, authorize: Any, repository_provider: object) -> list[dict[str, Any]]:
        events.append(("query", load_case_id, repository_provider))
        authorize()
        return [{"id": "run-a"}]

    monkeypatch.setattr(analysis_runs, "require_permission", require_permission)
    monkeypatch.setattr(analysis_runs, "SQLAnalysisRunSummaryRepositoryProvider", lambda: provider)
    monkeypatch.setattr(analysis_runs, "list_analysis_runs_query", query)

    assert analysis_runs.list_analysis_runs("load-a", request) == [{"id": "run-a"}]
    assert events == [
        ("query", "load-a", provider),
        ("permission", request, analysis_runs.PROJECT_DATA_VIEW),
    ]
