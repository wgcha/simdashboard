from __future__ import annotations

import ast
from pathlib import Path

from app.main import app
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


def test_result_ingestion_extraction_preserves_the_checked_in_openapi_contract():
    snapshot = Path(__file__).parents[2] / "frontend" / "openapi.json"
    assert check_contract(snapshot, app.openapi()) == []
