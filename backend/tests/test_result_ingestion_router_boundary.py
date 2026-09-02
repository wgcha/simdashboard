from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.main import app
from app.application.results import ingestion, queries
from app.domains.results.models import ResultIngestionTargetRead
from app.routers import result_ingestion
from scripts.check_openapi_contract import check_contract


def test_result_ingestion_routes_are_owned_by_router_in_contract_order():
    routes = [route for route in app.routes if getattr(route, "path", "").startswith("/api/result-import/template") or getattr(route, "path", "").endswith("/results/import") or getattr(route, "path", "").endswith("/folder-import/example")]
    assert [(route.path, tuple(sorted(route.methods or ()))) for route in routes] == [
        ("/api/result-import/template/{file_format}", ("GET",)),
        ("/api/load-cases/{load_case_id}/results/import", ("POST",)),
        ("/api/load-cases/{load_case_id}/folder-import/example", ("POST",)),
    ]
    assert all(route.endpoint.__module__ == result_ingestion.__name__ for route in routes)


def test_main_has_no_result_ingestion_decorators_and_router_has_no_sql_execute():
    main_source = Path(__file__).parents[1].joinpath("app", "main.py").read_text()
    assert "def get_result_import_template" not in main_source
    assert "def import_analysis_results" not in main_source
    assert "def import_typed_result_example" not in main_source

    tree = ast.parse(Path(result_ingestion.__file__).read_text())
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "execute" for node in ast.walk(tree))


def test_result_ingestion_application_service_stays_framework_free_and_owns_orchestration():
    source = Path(ingestion.__file__).read_text()
    forbidden_imports = ("fastapi", "starlette", "Request", "HTTPException", "database_connection", "security")
    assert not any(token in source for token in forbidden_imports)

    router_tree = ast.parse(Path(result_ingestion.__file__).read_text())
    called_names = {
        node.func.id
        for node in ast.walk(router_tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "parse_result_file" not in called_names
    assert "ingest_result_bundle" not in called_names
    assert {"run_manual_import", "run_typed_example"} <= called_names
    query_source = Path(queries.__file__).read_text()
    assert not any(token in query_source for token in forbidden_imports + ("repositories.", "adapters.persistence"))


@pytest.mark.unit
def test_result_ingestion_router_uses_query_port_and_preserves_read_order():
    router_source = Path(result_ingestion.__file__).read_text()
    assert "ResultIngestionRepository" not in router_source
    assert "get_manual_result_import_context" in router_source
    assert "get_result_ingestion_target" in router_source

    class Query:
        def __init__(self):
            self.calls = []

        def get_result_ingestion_target(self, load_case_id):
            self.calls.append(("target", load_case_id))
            return ResultIngestionTargetRead("project", "request", load_case_id)

        def get_quality_threshold(self, project_id, criterion_key, default):
            self.calls.append(("threshold", criterion_key))
            return default

        def list_catalog(self, load_case_id):
            self.calls.append(("catalog", load_case_id))
            return {"x": {"id": "x"}}

    query = Query()
    assert queries.get_manual_result_import_context(query, "load-case") is not None
    assert query.calls == [
        ("target", "load-case"),
        ("threshold", "chassis_rear_permanent_deformation_mm"),
        ("threshold", "open_cell_stress_mpa"),
        ("catalog", "load-case"),
    ]
    target_query = Query()
    assert queries.get_result_ingestion_target(target_query, "load-case") is not None
    assert target_query.calls == [("target", "load-case")]


def test_result_ingestion_extraction_preserves_the_checked_in_openapi_contract():
    snapshot = Path(__file__).parents[2] / "frontend" / "openapi.json"
    assert check_contract(snapshot, app.openapi()) == []
