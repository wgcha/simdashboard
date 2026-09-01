from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import app.main as main_module
from app.adapters.http.routers import feature_examples as feature_examples_router
from app.adapters.persistence.feature_examples import SQLFeatureExamplesRepository
from app.application.feature_examples.queries import feature_examples
from app.database import initialize_database
from app.main import app


LOAD_CASE_IDS = [
    "loadcase-showcase-compare", "loadcase-showcase-trust",
    "loadcase-showcase-warning", "loadcase-showcase-review",
    "loadcase-showcase-multitype", "loadcase-showcase-waiting",
    "loadcase-showcase-workflow", "loadcase-showcase-multitype",
]
CATALOG_IDS = [
    "run-comparison", "trust-ready", "trust-warning", "review-flow",
    "multi-type", "data-waiting", "workflow-states", "folder-schema",
    "ppt-layout", "automation", "data-registration", "help",
]
PROFILE_KEYS = {"runs", "scalars", "series", "curves", "media", "reviews"}
BASE_KEYS = {"id", "order", "category", "title", "summary", "badge", "workspace_page", "features", "checks", "data_profile"}


class _Cursor:
    def __init__(self, columns: list[str], values: list[tuple[Any, ...]]) -> None:
        self._columns, self._values = columns, values

    def keys(self) -> list[str]:
        return self._columns

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._values

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._values[0] if self._values else None


class _Connection:
    def __init__(self, values: tuple[Any, ...] = (1, 2, 3, 4, 5, 6)) -> None:
        self.values, self.calls = values, []

    def execute(self, statement: str, parameters: Any | None = None) -> _Cursor:
        self.calls.append((statement, parameters))
        return _Cursor(["runs", "scalars", "series", "curves", "media", "reviews"], [self.values])


@pytest.mark.contract
def test_feature_examples_route_openapi_and_global_order() -> None:
    routes = [route for route in app.routes if isinstance(route, APIRoute)]
    route = next(route for route in routes if route.path == "/api/feature-examples")
    assert route.endpoint.__module__ == feature_examples_router.__name__
    assert route.endpoint.__name__ == "feature_examples"
    assert route.response_model == list[dict[str, Any]]
    assert (route.operation_id or route.unique_id) == "feature_examples_api_feature_examples_get"
    operation = app.openapi()["paths"]["/api/feature-examples"]["get"]
    assert set(operation["responses"]) == {"200"}
    assert routes.index(route) < routes.index(next(item for item in routes if item.path == "/api/projects"))


@pytest.mark.contract
def test_main_relinquishes_feature_examples_and_ceiling_is_monotonic() -> None:
    path = Path(main_module.__file__)
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    actual = sum(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "execute" for node in ast.walk(tree))
    baseline = json.loads((path.parents[1] / "scripts" / "architecture_baseline.json").read_text(encoding="utf-8"))
    assert actual <= 67
    assert baseline["execute_call_ceilings"]["app/main.py"] == actual
    assert "def feature_examples(" not in source
    assert "SELECT count(DISTINCT r.id)" not in source
    assert "data_profile" not in source
    assert feature_examples_router.__file__
    assert Path(feature_examples_router.__file__).read_text(encoding="utf-8").count(".execute(") == 0


@pytest.mark.unit
def test_application_uses_one_provider_connection_and_eight_calls_in_catalog_order() -> None:
    class Repository:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def data_profile(self, load_case_id: str) -> dict[str, int]:
            self.calls.append(load_case_id)
            return {key: index for index, key in enumerate(sorted(PROFILE_KEYS))}

    repository = Repository()

    class Provider:
        def __call__(self):
            class Context:
                def __enter__(self):
                    return repository

                def __exit__(self, *_args: Any) -> None:
                    return None

            return Context()

    result = feature_examples(Provider())
    assert repository.calls == LOAD_CASE_IDS
    assert [item["id"] for item in result] == CATALOG_IDS
    assert all(item["data_profile"] for item in result[:7])
    assert result[4]["data_profile"] == result[8]["data_profile"]
    assert all(result[index]["data_profile"] == {key: 0 for key in PROFILE_KEYS} for index in (7, 9, 10, 11))


@pytest.mark.unit
def test_sql_adapter_uses_exact_legacy_aggregate_sql() -> None:
    connection = _Connection()
    SQLFeatureExamplesRepository(connection).data_profile("loadcase-showcase-compare")
    statement, parameters = connection.calls[0]
    assert " ".join(statement.split()) == (
        "SELECT count(DISTINCT r.id), count(DISTINCT s.id), count(DISTINCT ts.variable_key), "
        "count(DISTINCT c.id), count(DISTINCT m.id), count(DISTINCT a.id) "
        "FROM load_cases lc LEFT JOIN analysis_runs r ON r.load_case_id=lc.id "
        "LEFT JOIN scalar_results s ON s.analysis_run_id=r.id "
        "LEFT JOIN time_series_results ts ON ts.analysis_run_id=r.id "
        "LEFT JOIN curve_results c ON c.analysis_run_id=r.id "
        "LEFT JOIN media_assets m ON m.analysis_run_id=r.id "
        "LEFT JOIN review_annotations a ON a.analysis_run_id=r.id WHERE lc.id=?"
    )
    assert parameters == ["loadcase-showcase-compare"]


@pytest.mark.unit
def test_sql_missing_load_case_is_zero_profile() -> None:
    connection = _Connection((0, 0, 0, 0, 0, 0))
    assert SQLFeatureExamplesRepository(connection).data_profile("does-not-exist") == {key: 0 for key in PROFILE_KEYS}


@pytest.mark.unit
def test_application_propagates_repository_errors_without_hooks() -> None:
    class Provider:
        def __call__(self):
            class Context:
                def __enter__(self):
                    raise RuntimeError("database unavailable")

                def __exit__(self, *_args: Any) -> None:
                    return None

            return Context()

    with pytest.raises(RuntimeError, match="database unavailable"):
        feature_examples(Provider())


@pytest.mark.duckdb_integration
def test_seeded_catalog_has_exact_order_profiles_and_fresh_objects() -> None:
    initialize_database()
    with TestClient(app) as client:
        first = client.get("/api/feature-examples").json()
        second = client.get("/api/feature-examples").json()
    assert [item["id"] for item in first] == CATALOG_IDS
    assert [item["order"] for item in first] == list(range(1, 13))
    assert all(set(item) >= BASE_KEYS for item in first)
    assert all(set(item["data_profile"]) == PROFILE_KEYS for item in first)
    first[0]["title"] = "mutated"
    assert second[0]["title"] == "Run 비교: 회귀와 개선"
    assert first[0]["data_profile"] == {"runs": 3, "scalars": 12, "series": 1, "curves": 0, "media": 0, "reviews": 0}
    assert first[3]["data_profile"]["reviews"] == 3
    assert first[4]["data_profile"]["curves"] == 1
    assert first[4]["data_profile"]["media"] == 1
    assert all({"preferred_view", "project_id", "request_id", "load_case_id"} <= set(first[index]) for index in range(7))
    assert set(first[7]) == BASE_KEYS
    assert {"preferred_view", "project_id", "request_id", "load_case_id", "action_hint"} <= set(first[8])
    assert all(set(first[index]) == BASE_KEYS for index in range(9, 12))
    assert "action_hint" in first[8]
