from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.adapters.http.routers import load_case_overview
from app.adapters.persistence.load_case_overview import SQLLoadCaseOverviewRepository
from app.application.load_case_overview import queries
from app.database import initialize_database
from app.database_connection import connect
from app.domains.load_case_overview.errors import LoadCaseNotFoundError, SelectedRunNotFoundError
from app.domains.load_case_overview.policies import overview_payload
from app.main import app


LOAD_CASE_ID = "loadcase-drop-bottom-001"


def _provider(repository: Any):
    @contextmanager
    def provide() -> Iterator[Any]:
        yield repository

    return provide


class EmptyProductRepository:
    def list_for_load_case(self, _load_case_id: str) -> list[dict[str, Any]]:
        return []


class FakeOverviewRepository:
    def __init__(self, *, load_case: dict[str, Any] | None, selected_run: str | None, projection: dict[str, Any] | None) -> None:
        self.load_case_value = load_case
        self.selected_run_value = selected_run
        self.projection = projection
        self.events: list[str] = []

    def load_case(self, load_case_id: str) -> dict[str, Any] | None:
        self.events.append(f"load_case:{load_case_id}")
        return self.load_case_value

    def selected_run(self, load_case_id: str, run_id: str | None) -> str | None:
        self.events.append(f"selected_run:{load_case_id}:{run_id}")
        return self.selected_run_value

    def run_projection(self, load_case_id: str, run_id: str) -> dict[str, Any]:
        self.events.append(f"projection:{load_case_id}:{run_id}")
        assert self.projection is not None
        return self.projection


@pytest.mark.contract
def test_overview_router_owns_exact_legacy_route_openapi_and_global_adjacency() -> None:
    routes = [
        route for route in app.routes
        if isinstance(route, APIRoute) and route.endpoint.__module__ == load_case_overview.__name__
    ]
    assert [(route.path, tuple(sorted(route.methods or ()) )) for route in routes] == [
        ("/api/load-cases/{load_case_id}/overview", ("GET",)),
    ]
    route = routes[0]
    assert route.endpoint.__name__ == "get_load_case_overview"
    assert (route.operation_id or route.unique_id) == "get_load_case_overview_api_load_cases__load_case_id__overview_get"
    assert route.status_code is None
    assert route.response_model == dict[str, Any]

    operation = app.openapi()["paths"]["/api/load-cases/{load_case_id}/overview"]["get"]
    assert set(operation["responses"]) == {"200", "422"}
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "type": "object",
        "additionalProperties": True,
        "title": "Response Get Load Case Overview Api Load Cases  Load Case Id  Overview Get",
    }
    assert [(item["name"], item["in"], item["required"]) for item in operation["parameters"]] == [
        ("load_case_id", "path", True),
        ("run_id", "query", False),
    ]
    assert operation["parameters"][1]["schema"] == {
        "anyOf": [{"type": "string", "minLength": 3, "maxLength": 120}, {"type": "null"}],
        "title": "Run Id",
    }

    all_routes = [route for route in app.routes if isinstance(route, APIRoute)]
    index = all_routes.index(route)
    assert all_routes[index - 1].endpoint.__name__ == "download_result_asset"
    assert any(candidate.endpoint.__name__ == "get_result_asset" for candidate in all_routes[:index])
    assert all_routes[index + 1].endpoint.__name__ == "list_analysis_runs"


@pytest.mark.contract
def test_main_relinquishes_overview_route_sql_and_execute_ownership() -> None:
    root = Path(__file__).parents[1] / "app"
    main_source = (root / "main.py").read_text(encoding="utf-8")
    router_source = Path(load_case_overview.__file__).read_text(encoding="utf-8")
    forbidden = (
        "def get_load_case_overview",
        "SELECT lc.*, ar.id AS request_id",
        "FROM scalar_results sr",
        "FROM time_series_results ts",
        "FROM curve_results cr",
        "FROM result_locations WHERE analysis_run_id",
        "FROM qualitative_notes WHERE analysis_run_id",
        "FROM media_assets WHERE analysis_run_id",
        "FROM template_executions te",
    )
    assert all(token not in main_source for token in forbidden)
    assert "app.include_router(load_case_overview_router)" in main_source
    assert router_source.count(".execute(") == 0
    assert main_source.count(".execute(") <= 81


@pytest.mark.unit
def test_application_authorizes_then_uses_separate_product_and_overview_providers() -> None:
    events: list[str] = []

    class ProductRepository:
        def list_for_load_case(self, load_case_id: str) -> list[dict[str, Any]]:
            events.append(f"product-read:{load_case_id}")
            return [{"category": "MODEL", "name": "Model", "value_text": "X", "file_path": None, "metadata": None}]

    @contextmanager
    def product_provider() -> Iterator[ProductRepository]:
        events.append("product-open:connection-product")
        yield ProductRepository()
        events.append("product-close:connection-product")

    repository = FakeOverviewRepository(
        load_case={"id": "load", "parameters_json": "{}"}, selected_run=None, projection=None
    )

    @contextmanager
    def overview_provider() -> Iterator[FakeOverviewRepository]:
        events.append("overview-open:connection-overview")
        yield repository
        events.append("overview-close:connection-overview")

    def authorize() -> object:
        events.append("authorize:project.data.view")
        return object()

    response = queries.get_load_case_overview("load", None, authorize, product_provider, overview_provider)

    assert response["product_information"][0]["value_text"] == "X"
    assert events == [
        "authorize:project.data.view",
        "product-open:connection-product",
        "product-read:load",
        "product-close:connection-product",
        "overview-open:connection-overview",
        "overview-close:connection-overview",
    ]
    assert repository.events == ["load_case:load", "selected_run:load:None"]


@pytest.mark.unit
def test_application_denial_stops_before_either_provider_opens() -> None:
    events: list[str] = []

    def deny() -> object:
        events.append("authorize")
        raise PermissionError("denied")

    @contextmanager
    def unexpected_product_provider() -> Iterator[Any]:
        events.append("product-provider-open")
        raise AssertionError("denied request must not open a product provider")
        yield None  # pragma: no cover

    @contextmanager
    def unexpected_overview_provider() -> Iterator[Any]:
        events.append("overview-provider-open")
        raise AssertionError("denied request must not open an overview provider")
        yield None  # pragma: no cover

    with pytest.raises(PermissionError, match="denied"):
        queries.get_load_case_overview("load", None, deny, unexpected_product_provider, unexpected_overview_provider)

    assert events == ["authorize"]


@pytest.mark.unit
def test_product_read_failure_closes_product_provider_without_opening_overview_provider() -> None:
    events: list[str] = []

    class FailingProductRepository:
        def list_for_load_case(self, _load_case_id: str) -> list[dict[str, Any]]:
            events.append("product-read")
            raise RuntimeError("product read failed")

    @contextmanager
    def product_provider() -> Iterator[FailingProductRepository]:
        events.append("product-open")
        try:
            yield FailingProductRepository()
        finally:
            events.append("product-close")

    @contextmanager
    def overview_provider() -> Iterator[Any]:
        events.append("overview-open")
        raise AssertionError("overview provider must not open after product failure")
        yield None  # pragma: no cover

    with pytest.raises(RuntimeError, match="product read failed"):
        queries.get_load_case_overview("load", None, lambda: events.append("authorize"), product_provider, overview_provider)
    assert events == ["authorize", "product-open", "product-read", "product-close"]


@pytest.mark.unit
def test_query_missing_selected_and_no_run_branches_preserve_legacy_errors_and_order() -> None:
    missing = FakeOverviewRepository(load_case=None, selected_run=None, projection=None)
    with pytest.raises(LoadCaseNotFoundError, match="하중 경우를 찾을 수 없습니다."):
        queries.get_load_case_overview("missing", None, lambda: None, _provider(EmptyProductRepository()), _provider(missing))
    assert missing.events == ["load_case:missing"]

    selected_missing = FakeOverviewRepository(
        load_case={"id": "load", "parameters_json": "{}"}, selected_run=None, projection=None
    )
    with pytest.raises(SelectedRunNotFoundError, match="선택한 Run이 이 하중 경우에 존재하지 않습니다."):
        queries.get_load_case_overview("load", "another-run", lambda: None, _provider(EmptyProductRepository()), _provider(selected_missing))
    assert selected_missing.events == ["load_case:load", "selected_run:load:another-run"]

    no_run = FakeOverviewRepository(
        load_case={"id": "load", "parameters_json": '{"mass": 1}'}, selected_run=None, projection=None
    )
    class ProductRepository:
        def list_for_load_case(self, _load_case_id: str) -> list[dict[str, Any]]:
            return [{"category": "MODEL"}]

    payload = queries.get_load_case_overview("load", None, lambda: None, _provider(ProductRepository()), _provider(no_run))
    assert payload["run"] is None
    assert payload["overall_verdict"] == "NO_DATA"
    assert payload["load_case"]["parameters"] == {"mass": 1}
    assert no_run.events == ["load_case:load", "selected_run:load:None"]


@pytest.mark.unit
def test_router_is_thin_and_maps_domain_errors_without_owning_payload_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    source = Path(load_case_overview.__file__).read_text(encoding="utf-8")
    assert "list_product_information(" not in source
    assert ".execute(" not in source
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""})

    monkeypatch.setattr(load_case_overview, "get_load_case_overview_query", lambda *_args: (_ for _ in ()).throw(LoadCaseNotFoundError()))
    with pytest.raises(HTTPException) as error:
        load_case_overview.get_load_case_overview("missing", request, None)
    assert error.value.status_code == 404
    assert error.value.detail == "하중 경우를 찾을 수 없습니다."


@pytest.mark.contract
def test_domain_policy_does_not_own_http_asset_urls_and_transport_response_does() -> None:
    policy_source = (Path(__file__).parents[1] / "app" / "domains" / "load_case_overview" / "policies.py").read_text(encoding="utf-8")
    assert "/api/assets/" not in policy_source
    payload = load_case_overview._with_media_urls({"media": [{"id": "asset-1", "metadata": {}}]})
    assert payload["media"] == [{
        "id": "asset-1", "metadata": {},
        "asset_url": "/api/assets/asset-1", "download_url": "/api/assets/asset-1/download",
    }]


@pytest.mark.unit
def test_policy_transforms_json_media_urls_and_open_cell_chassis_verdict_precedence() -> None:
    payload = overview_payload(
        {"id": "load", "parameters_json": '{"drop_height": 1.2}'},
        "run",
        {
            "scalar_results": [
                {"result_group": "OPEN_CELL", "unit": "MPa", "variable_key": "bottom_stress", "threshold_double": 101.0, "verdict": "PASS"},
                {"result_group": "OPEN_CELL", "unit": "MPa", "variable_key": "top_stress", "threshold_double": 99.0, "verdict": "FAIL"},
                {"result_group": "CHASSIS_REAR", "unit": "mm", "variable_key": "permanent_deformation", "threshold_double": 5.0, "verdict": "FAIL"},
                {"result_group": "CUSTOM", "unit": "N", "variable_key": "other", "threshold_double": None, "verdict": "PASS"},
            ],
            "time_series": [{"variable_key": "stress_time"}],
            "curves": [{"series_key": "curve"}],
            "result_locations": [{"variable_key": "bottom_stress"}],
            "notes": [{"body": "note"}],
            "media": [{"id": "asset-1", "metadata_json": '{"variable_key":"bottom_stress"}'}],
            "template": [{"input_json": '{"mesh":"a"}', "generated_model_json": "not-json"}],
        },
        [{"category": "MODEL"}],
    )
    assert payload["load_case"]["parameters"] == {"drop_height": 1.2}
    assert payload["threshold"] == 101.0
    assert payload["overall_verdict"] == "FAIL"
    assert payload["analysis_verdicts"] == {"open_cell": "FAIL", "chassis_rear": "FAIL"}
    assert payload["media"] == [{"id": "asset-1", "metadata": {"variable_key": "bottom_stress"}}]
    assert payload["template_execution"] == {"input": {"mesh": "a"}, "generated_model": "not-json"}


@pytest.mark.unit
def test_policy_chassis_threshold_fallback_and_pass_no_data_json_fallbacks() -> None:
    base = {"time_series": [], "curves": [], "result_locations": [], "notes": [], "media": [], "template": []}
    fallback = overview_payload(
        {"id": "load", "parameters_json": "not-json"},
        "run",
        {
            **base,
            "scalar_results": [
                {"result_group": "OPEN_CELL", "unit": "MPa", "variable_key": "stress", "threshold_double": None, "verdict": "PASS"},
                {"result_group": "CHASSIS_REAR", "unit": "mm", "variable_key": "permanent_deformation", "threshold_double": 5.0, "verdict": "PASS"},
            ],
        },
        [],
    )
    assert fallback["load_case"]["parameters"] == "not-json"
    assert fallback["threshold"] == 5.0
    assert fallback["overall_verdict"] == "PASS"
    assert fallback["analysis_verdicts"] == {"open_cell": "PASS", "chassis_rear": "PASS"}

    no_scalars = overview_payload(
        {"id": "load", "parameters_json": None}, "run", {**base, "scalar_results": []}, []
    )
    assert no_scalars["load_case"]["parameters"] is None
    assert no_scalars["overall_verdict"] == "NO_DATA"
    assert no_scalars["analysis_verdicts"] == {"open_cell": "NO_DATA", "chassis_rear": "NO_DATA"}


@pytest.mark.unit
def test_sql_repository_keeps_legacy_query_order_and_projection_shape() -> None:
    class Cursor:
        def __init__(self, values: list[tuple[Any, ...]], columns: list[str] = []) -> None:
            self._values = values
            self.description = [(column,) for column in columns]

        def fetchall(self) -> list[tuple[Any, ...]]:
            return self._values

        def fetchone(self) -> tuple[Any, ...] | None:
            return self._values[0] if self._values else None

    class Connection:
        def __init__(self) -> None:
            self.calls: list[tuple[str, list[Any] | None]] = []

        def execute(self, statement: str, parameters: list[Any] | None = None) -> Cursor:
            self.calls.append((statement, parameters))
            normalized = " ".join(statement.upper().split())
            if "FROM LOAD_CASES LC" in normalized:
                return Cursor([("load", "{}")], ["id", "parameters_json"])
            if normalized.startswith("SELECT ID FROM ANALYSIS_RUNS"):
                return Cursor([("run",)])
            return Cursor([])

    connection = Connection()
    repository = SQLLoadCaseOverviewRepository(connection)  # type: ignore[arg-type]
    assert repository.load_case("load") == {"id": "load", "parameters_json": "{}"}
    assert repository.selected_run("load", None) == "run"
    assert repository.run_projection("load", "run") == {
        "scalar_results": [], "time_series": [], "curves": [], "result_locations": [],
        "notes": [], "media": [], "template": [],
    }
    statements = [" ".join(statement.upper().split()) for statement, _ in connection.calls]
    assert connection.calls[1][1] == ["load", None, None]
    assert "ORDER BY RUN_NO DESC LIMIT 1" in statements[1]
    assert connection.calls[2][1] == ["load", "run"]
    assert connection.calls[3][1] == ["load", "run"]
    assert connection.calls[4][1] == ["load", "run"]
    assert [parameters for _, parameters in connection.calls[5:]] == [["run"], ["run"], ["run"], ["run"]]
    assert "LEFT JOIN VARIABLE_DEFINITIONS VD ON VD.LOAD_CASE_ID = ? AND VD.VARIABLE_KEY = SR.VARIABLE_KEY" in statements[2]
    assert "ORDER BY SR.DISPLAY_NAME" in statements[2]
    assert "ORDER BY TS.TIME_VALUE, TS.VARIABLE_KEY" in statements[3]
    assert "ORDER BY CR.DISPLAY_NAME, CR.SERIES_KEY" in statements[4]
    assert "ORDER BY VARIABLE_KEY" in statements[5]
    assert "ORDER BY CREATED_AT DESC" in statements[6]
    assert "FROM MEDIA_ASSETS WHERE ANALYSIS_RUN_ID = ?" in statements[7]
    assert "JOIN ANALYSIS_RUNS RUN ON RUN.TEMPLATE_EXECUTION_ID = TE.ID" in statements[8]


@pytest.mark.duckdb_integration
def test_overview_http_preserves_seeded_latest_no_data_and_selected_run_errors() -> None:
    initialize_database()
    with connect() as connection:
        no_data = connection.execute(
            """
            SELECT lc.id FROM load_cases lc
            LEFT JOIN analysis_runs run ON run.load_case_id = lc.id
            WHERE run.id IS NULL ORDER BY lc.id LIMIT 1
            """
        ).fetchone()
    assert no_data is not None

    with TestClient(app) as client:
        latest = client.get(f"/api/load-cases/{LOAD_CASE_ID}/overview")
        selected = client.get(
            f"/api/load-cases/{LOAD_CASE_ID}/overview", params={"run_id": "run-drop-baseline-001"}
        )
        no_run = client.get(f"/api/load-cases/{no_data[0]}/overview")
        cross_case = client.get(
            f"/api/load-cases/{LOAD_CASE_ID}/overview", params={"run_id": "run-clamp-001"}
        )
        missing = client.get("/api/load-cases/not-a-load-case/overview")

    assert latest.status_code == 200
    assert latest.json()["run"] == "run-drop-001"
    assert latest.json()["media"]
    assert latest.json()["media"][0]["asset_url"] == f"/api/assets/{latest.json()['media'][0]['id']}"
    assert latest.json()["media"][0]["download_url"] == f"/api/assets/{latest.json()['media'][0]['id']}/download"
    assert selected.status_code == 200 and selected.json()["run"] == "run-drop-baseline-001"
    assert no_run.status_code == 200 and no_run.json()["overall_verdict"] == "NO_DATA"
    assert cross_case.status_code == 404
    assert cross_case.json() == {"detail": "선택한 Run이 이 하중 경우에 존재하지 않습니다."}
    assert missing.status_code == 404
    assert missing.json() == {"detail": "하중 경우를 찾을 수 없습니다."}
