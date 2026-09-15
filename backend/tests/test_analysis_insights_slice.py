from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterator, NoReturn

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.adapters.http.routers import analysis_insights
from app.adapters.persistence import analysis_insights as insights_persistence
from app.application.analysis_insights import queries
from app.database import initialize_database
from app.main import app


def _provider(repository: Any):
    @contextmanager
    def provide() -> Iterator[Any]:
        yield repository

    return provide


class FakeInsightsRepository:
    def __init__(self, **overrides: Any) -> None:
        self.comparison_run_rows = overrides.get("comparison_run_rows", [])
        self.scalars = overrides.get("scalars", [])
        self.series = overrides.get("series", [])
        self.points = overrides.get("points", [])
        self.conditions = overrides.get("conditions", [])
        self.run = overrides.get("run")
        self.latest = overrides.get("latest")
        self.metadata = overrides.get("metadata")
        self.job = overrides.get("job")
        self.counts = overrides.get("counts", {"scalar": 0, "time_series": 0, "curve": 0, "media": 0, "location": 0})
        self.keys = overrides.get("keys", set())
        self.catalog = overrides.get("catalog", {})
        self.units = overrides.get("units", [])
        self.validation_rows = overrides.get("validation_rows", [])
        self.opened: list[str] = []
        self.events: list[str] = []

    def comparison_runs(self, *_args: str) -> list[dict[str, Any]]:
        self.events.append("runs")
        return self.comparison_run_rows

    def comparison_scalars(self, *_args: str) -> list[dict[str, Any]]:
        self.events.append("scalars")
        return self.scalars

    def comparison_series(self, *_args: str) -> list[dict[str, Any]]:
        self.events.append("series")
        return self.series

    def comparison_points(self, *_args: str) -> list[dict[str, Any]]:
        self.events.append("points")
        return self.points

    def comparison_conditions(self, *_args: str) -> list[dict[str, Any]]:
        self.events.append("conditions")
        return self.conditions

    def trust_run(self, _run_id: str) -> dict[str, Any] | None:
        self.events.append("run")
        return self.run

    def latest_run_id(self, _load_case_id: str) -> str | None:
        self.events.append("latest")
        return self.latest

    def run_metadata(self, _run_id: str) -> dict[str, Any] | None:
        self.events.append("metadata")
        return self.metadata

    def import_job(self, _run_id: str) -> dict[str, Any] | None:
        self.events.append("job")
        return self.job

    def result_counts(self, _run_id: str) -> dict[str, int]:
        self.events.append("counts")
        return self.counts

    def result_keys(self, _run_id: str) -> set[str]:
        self.events.append("keys")
        return self.keys

    def active_catalog(self, _load_case_id: str) -> dict[str, dict[str, Any]]:
        self.events.append("catalog")
        return self.catalog

    def result_unit_rows(self, _run_id: str) -> list[tuple[Any, Any]]:
        self.events.append("units")
        return self.units

    def validations(self, _run_id: str) -> list[dict[str, Any]]:
        self.events.append("validations")
        return self.validation_rows


def _run(run_id: str, *, load_case_id: str = "load", status: str = "COMPLETED", completed_at: datetime | None = None) -> dict[str, Any]:
    return {"id": run_id, "load_case_id": load_case_id, "status": status, "completed_at": completed_at}


def _scalar(run_id: str, key: str, value: float | int | None, verdict: str, unit: str = "MPa", *, integer: bool = False) -> dict[str, Any]:
    return {
        "analysis_run_id": run_id,
        "variable_key": key,
        "display_name": key,
        "value_double": None if integer else value,
        "value_integer": value if integer else None,
        "verdict": verdict,
        "unit": unit,
    }


@pytest.mark.contract
def test_analysis_insights_router_owns_exact_two_routes_openapi_and_global_adjacency() -> None:
    routes = [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.endpoint.__module__ == analysis_insights.__name__
    ]
    assert [(route.path, tuple(sorted(route.methods or ()))) for route in routes] == [
        ("/api/load-cases/{load_case_id}/run-comparison", ("GET",)),
        ("/api/analysis-runs/{run_id}/trust", ("GET",)),
    ]
    assert [route.endpoint.__name__ for route in routes] == ["compare_analysis_runs", "get_analysis_run_trust"]
    assert [route.operation_id or route.unique_id for route in routes] == [
        "compare_analysis_runs_api_load_cases__load_case_id__run_comparison_get",
        "get_analysis_run_trust_api_analysis_runs__run_id__trust_get",
    ]
    assert [route.status_code for route in routes] == [None, None]
    assert [route.response_model for route in routes] == [dict[str, Any], dict[str, Any]]
    paths = app.openapi()["paths"]
    comparison = paths["/api/load-cases/{load_case_id}/run-comparison"]["get"]
    trust = paths["/api/analysis-runs/{run_id}/trust"]["get"]
    assert set(comparison["responses"]) == {"200", "422"}
    assert set(trust["responses"]) == {"200", "422"}
    assert comparison["responses"]["200"]["content"]["application/json"]["schema"] == {
        "type": "object", "additionalProperties": True,
        "title": "Response Compare Analysis Runs Api Load Cases  Load Case Id  Run Comparison Get",
    }
    assert trust["responses"]["200"]["content"]["application/json"]["schema"] == {
        "type": "object", "additionalProperties": True,
        "title": "Response Get Analysis Run Trust Api Analysis Runs  Run Id  Trust Get",
    }
    parameters = comparison["parameters"]
    assert [(item["name"], item["in"], item["required"]) for item in parameters] == [
        ("load_case_id", "path", True), ("baseline_run_id", "query", True),
        ("target_run_id", "query", True), ("variable_key", "query", False),
    ]
    assert parameters[1]["schema"]["minLength"] == 3 and parameters[1]["schema"]["maxLength"] == 120
    assert parameters[2]["schema"]["minLength"] == 3 and parameters[2]["schema"]["maxLength"] == 120
    assert parameters[3]["schema"]["anyOf"] == [{"type": "string", "maxLength": 120}, {"type": "null"}]
    all_routes = [route for route in app.routes if isinstance(route, APIRoute)]
    index = all_routes.index(routes[0])
    assert all_routes[index - 1].endpoint.__name__ == "list_analysis_runs"
    assert all_routes[index + 2].endpoint.__name__ == "list_review_items"


@pytest.mark.contract
def test_main_relinquishes_analysis_insight_routes_helpers_sql_and_shared_key_import() -> None:
    source = (Path(__file__).parents[1] / "app" / "main.py").read_text(encoding="utf-8")
    for token in (
        "def _run_trust_payload", "def compare_analysis_runs", "def get_analysis_run_trust",
        "SELECT * FROM analysis_runs WHERE load_case_id=? AND id IN", "FROM folder_import_jobs WHERE analysis_run_id=?",
        "SELECT variable_key, unit FROM scalar_results", "from .adapters.persistence.result_keys import run_result_keys",
    ):
        assert token not in source
    assert "app.include_router(analysis_insights_router)" in source


@pytest.mark.contract
def test_analysis_insights_remains_read_only_without_explicit_permission_audit_or_transaction_hooks() -> None:
    router_source = Path(analysis_insights.__file__).read_text(encoding="utf-8")
    query_source = (Path(__file__).parents[1] / "app" / "application" / "analysis_insights" / "queries.py").read_text(encoding="utf-8")
    persistence_source = (Path(__file__).parents[1] / "app" / "adapters" / "persistence" / "analysis_insights.py").read_text(encoding="utf-8")
    for token in ("Request", "require_permission", "require_resource_permission", "write_audit_event"):
        assert token not in router_source
    for token in ("transaction", "add_audit", "authorize_"):
        assert token not in query_source
    for token in ("BEGIN TRANSACTION", "COMMIT", "ROLLBACK", "INSERT ", "UPDATE ", "DELETE "):
        assert token not in persistence_source


@pytest.mark.unit
def test_same_run_comparison_is_422_before_repository_provider_opens() -> None:
    opened = False

    @contextmanager
    def provider() -> Iterator[NoReturn]:
        nonlocal opened
        opened = True
        raise AssertionError("provider must not open")
        yield  # pragma: no cover

    with pytest.raises(queries.MatchingRunComparisonError, match="기준 Run과 대상 Run은 달라야 합니다."):
        queries.compare_analysis_runs("load", "same", "same", None, provider)
    assert opened is False


@pytest.mark.unit
@pytest.mark.parametrize("rows", [[], [_run("baseline")]])
def test_comparison_missing_or_cross_load_case_maps_to_not_found(rows: list[dict[str, Any]]) -> None:
    repository = FakeInsightsRepository(comparison_run_rows=rows)
    with pytest.raises(queries.ComparisonRunsNotFoundError, match="선택한 Run을 하중 경우에서 찾을 수 없습니다."):
        queries.compare_analysis_runs("load", "baseline", "target", None, _provider(repository))


@pytest.mark.unit
def test_comparison_preserves_six_changes_numeric_fallback_and_summary_rules() -> None:
    baseline, target = "base", "target"
    repository = FakeInsightsRepository(
        comparison_run_rows=[_run(baseline), _run(target)],
        scalars=[
            _scalar(target, "added_is_target_only", 1, "PASS"),
            _scalar(baseline, "removed", 1, "PASS"),
            _scalar(baseline, "not_comparable_unit", 1, "PASS", "MPa"),
            _scalar(target, "not_comparable_unit", 2, "FAIL", "N"),
            _scalar(baseline, "not_comparable_null", None, "PASS"),
            _scalar(target, "not_comparable_null", 2, "FAIL"),
            _scalar(baseline, "regression", 1, "PASS"), _scalar(target, "regression", 2, "FAIL"),
            _scalar(baseline, "improved", 2, "FAIL"), _scalar(target, "improved", 1, "PASS"),
            _scalar(baseline, "unchanged_negative", -2, "PASS", integer=True), _scalar(target, "unchanged_negative", -1, "PASS", integer=True),
            _scalar(baseline, "zero", 0, "PASS"), _scalar(target, "zero", 2, "PASS"),
        ],
    )
    payload = queries.compare_analysis_runs("load", baseline, target, None, _provider(repository))
    rows = {item["variable_key"]: item for item in payload["scalar_comparison"]}
    assert rows["added_is_target_only"]["change"] == "ADDED"
    assert rows["removed"]["change"] == "REMOVED"
    assert rows["not_comparable_unit"]["change"] == "NOT_COMPARABLE"
    assert rows["not_comparable_null"]["change"] == "NOT_COMPARABLE"
    assert rows["regression"]["change"] == "REGRESSION"
    assert rows["improved"]["change"] == "IMPROVED"
    assert rows["unchanged_negative"]["change"] == "UNCHANGED"
    assert rows["unchanged_negative"]["delta"] == 1.0
    assert rows["unchanged_negative"]["delta_percent"] == 50.0
    assert rows["zero"]["delta_percent"] is None
    assert payload["summary"] == {"regression": 1, "improved": 1, "unchanged": 2, "comparable": 4}


@pytest.mark.unit
def test_comparison_added_and_series_selection_fallback_no_series_and_time_merge() -> None:
    base, target = "base", "target"
    repository = FakeInsightsRepository(
        comparison_run_rows=[_run(base), _run(target)],
        scalars=[_scalar(target, "added", 4, "PASS")],
        series=[{"variable_key": "a", "display_name": "A", "value_unit": "MPa"}],
        points=[
            {"analysis_run_id": target, "time_value": 1.0, "value": 20, "time_unit": "ms", "value_unit": "MPa"},
            {"analysis_run_id": base, "time_value": 1, "value": 10, "time_unit": "ms", "value_unit": "MPa"},
            {"analysis_run_id": base, "time_value": 2, "value": 30, "time_unit": "ms", "value_unit": "MPa"},
        ],
    )
    payload = queries.compare_analysis_runs("load", base, target, "not-common", _provider(repository))
    assert payload["scalar_comparison"][0]["change"] == "ADDED"
    assert payload["time_series"] == {
        "variable_key": "a", "display_name": "A", "unit": "MPa",
        "points": [
            {"time_value": 1.0, "time_unit": "ms", "baseline_value": 10, "target_value": 20},
            {"time_value": 2, "time_unit": "ms", "baseline_value": 30, "target_value": None},
        ],
    }
    assert repository.events == ["runs", "conditions", "scalars", "series", "points"]
    repository.events.clear()
    repository.series = []
    payload = queries.compare_analysis_runs("load", base, target, "not-common", _provider(repository))
    assert payload["time_series"] is None and payload["available_series"] == []
    assert repository.events == ["runs", "conditions", "scalars", "series"]


@pytest.mark.unit
def test_comparison_exposes_immutable_recorded_criterion_margins_without_verdict_inference() -> None:
    base, target = "base", "target"
    criteria = {
        "upper": {"operator": "LT", "upper": 10, "unit": "MPa", "label": "Peak"},
        "lower": {"operator": "GTE", "lower": -2, "unit": "mm"},
        "band": {"operator": "BETWEEN", "lower": -3, "upper": 4, "unit": "N"},
    }
    repository = FakeInsightsRepository(
        comparison_run_rows=[_run(base), _run(target)],
        conditions=[
            {"id": base, "metadata_json": json.dumps({"result_criteria": criteria})},
            {"id": target, "metadata_json": json.dumps({"result_criteria": criteria})},
        ],
        scalars=[
            _scalar(base, "upper", 10, "PASS"), _scalar(target, "upper", 9, "FAIL"),
            _scalar(base, "lower", -2, "PASS", "mm"), _scalar(target, "lower", -3, "PASS", "mm"),
            _scalar(base, "band", 0, "PASS", "N"), _scalar(target, "band", 5, "PASS", "N"),
            _scalar(base, "absent", 1, "PASS"),
            _scalar(target, "upper", 8, "PASS", "N"),
        ],
    )
    rows = {item["variable_key"]: item for item in queries.compare_analysis_runs("load", base, target, None, _provider(repository))["scalar_comparison"]}
    assert rows["upper"]["baseline_margin"] == {
        "status": "AVAILABLE", "value": 0.0, "unit": "MPa", "criterion_label": "Peak: 값 < 10 MPa", "reason": None,
        "source": "analysis_run_metadata.result_criteria:base", "meets_criterion": False,
    }
    assert rows["upper"]["target_margin"]["status"] == "UNKNOWN"
    assert rows["lower"]["baseline_margin"]["value"] == 0.0
    assert rows["lower"]["target_margin"]["value"] == -1.0
    assert rows["band"]["baseline_margin"]["value"] == 3.0
    assert rows["band"]["target_margin"]["value"] == -1.0
    assert rows["absent"]["baseline_margin"]["reason"] == "CRITERION_NOT_RECORDED"


@pytest.mark.unit
def test_trust_missing_or_expected_load_case_mismatch_is_not_found() -> None:
    for repository, expected in [(FakeInsightsRepository(run=None), None), (FakeInsightsRepository(run=_run("run", load_case_id="other")), "expected")]:
        with pytest.raises(queries.AnalysisRunNotFoundError, match="해석 Run을 찾을 수 없습니다."):
            queries.get_analysis_run_trust("run", _provider(repository), expected)
        assert repository.events == ["run"]


@pytest.mark.unit
def test_trust_preserves_source_catalog_unit_validation_and_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return datetime(2026, 1, 10, tzinfo=timezone.utc).replace(tzinfo=None)

    monkeypatch.setattr(queries, "datetime", FixedDatetime)
    metadata = {"source_name": "metadata-source", "metadata_json": '{"trace":true}'}
    job = {"source_folder": "job-source", "summary_json": '{"count":2}'}
    repository = FakeInsightsRepository(
        run=_run("run", completed_at=datetime(2026, 1, 12)), latest="run", metadata=metadata, job=job,
        counts={"scalar": 1, "time_series": 2, "curve": 3, "media": 4, "location": 5},
        keys={"z-unmapped", "a-mapped"},
        catalog={"a-mapped": {"unit": "MPa"}, "missing": {"unit": "N"}, "dash": {"unit": "-"}},
        units=[("a-mapped", "Pa"), ("a-mapped", "Pa"), ("dash", "bad")],
        validation_rows=[{"verdict": "FAIL", "created_at": datetime(2026, 1, 9)}, {"verdict": "PASS", "created_at": datetime(2026, 1, 8)}],
    )
    payload = queries.get_analysis_run_trust("run", _provider(repository))
    assert repository.events == ["run", "latest", "metadata", "job", "counts", "keys", "catalog", "units", "validations"]
    assert payload["is_latest"] is True and payload["age_days"] == 0
    assert payload["metadata"] == {"source_name": "metadata-source", "metadata": {"trace": True}}
    assert payload["import_job"] == {"source_folder": "job-source", "summary": {"count": 2}}
    assert payload["counts"] == {"scalar": 1, "time_series": 2, "curve": 3, "media": 4, "location": 5}
    assert payload["coverage"] == {"result_variables": 2, "catalog_variables": 3, "unmapped": ["z-unmapped"], "missing": ["dash", "missing"]}
    assert payload["unit_mismatches"] == [{"variable_key": "a-mapped", "expected": "MPa", "actual": "Pa"}]
    assert [item["code"] for item in payload["checks"]] == ["run_status", "source_trace", "catalog_mapping", "catalog_coverage", "unit_consistency", "validation"]
    assert payload["checks"][1]["detail"] == "metadata-source"
    assert payload["checks"][-1]["status"] == "FAIL"
    assert payload["trust_status"] == "FAIL"
    repository.run = _run("run", completed_at=datetime(2026, 1, 7))
    repository.latest = "other"
    repository.metadata = {"source_name": "metadata-source", "metadata_json": "{}"}
    repository.job = {"source_folder": "job-source", "summary_json": "{}"}
    repository.validation_rows = [{"verdict": "PASS", "created_at": datetime(2026, 1, 8)}]
    repository.keys = {"a-mapped"}
    repository.catalog = {"a-mapped": {"unit": "MPa"}}
    repository.units = []
    aged_payload = queries.get_analysis_run_trust("run", _provider(repository))
    assert aged_payload["age_days"] == 3
    assert aged_payload["is_latest"] is False


@pytest.mark.unit
@pytest.mark.parametrize(
    ("run_status", "metadata", "job", "keys", "catalog", "units", "validations", "expected"),
    [
        ("COMPLETED", {"source_name": "source", "metadata_json": "{}"}, None, {"k"}, {"k": {"unit": "MPa"}}, [], [{"verdict": "PASS"}], "TRUSTED"),
        ("COMPLETED", None, None, set(), {}, [], [], "WARN"),
        ("RUNNING", {"source_name": "source", "metadata_json": "{}"}, None, {"k"}, {"k": {"unit": "MPa"}}, [], [{"verdict": "PASS"}], "FAIL"),
    ],
)
def test_trust_status_precedence(run_status: str, metadata: dict[str, Any] | None, job: dict[str, Any] | None, keys: set[str], catalog: dict[str, dict[str, Any]], units: list[tuple[Any, Any]], validations: list[dict[str, Any]], expected: str) -> None:
    repository = FakeInsightsRepository(run=_run("run", status=run_status), metadata=metadata, job=job, keys=keys, catalog=catalog, units=units, validation_rows=validations)
    assert queries.get_analysis_run_trust("run", _provider(repository))["trust_status"] == expected


@pytest.mark.unit
def test_sql_adapter_uses_shared_result_key_helper_with_the_same_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = object()
    seen: list[object] = []

    def shared_key_query(actual_connection: object, run_id: str) -> set[str]:
        seen.extend([actual_connection, run_id])
        return {"shared"}

    monkeypatch.setattr(insights_persistence, "run_result_keys", shared_key_query)
    assert insights_persistence.SQLAnalysisInsightsRepository(connection).result_keys("run") == {"shared"}  # type: ignore[arg-type]
    assert seen == [connection, "run"]


@pytest.mark.duckdb_integration
def test_analysis_insights_http_preserves_read_only_errors_happy_paths_and_no_explicit_permission() -> None:
    initialize_database()
    with TestClient(app) as client:
        same = client.get("/api/load-cases/loadcase-drop-bottom-001/run-comparison", params={"baseline_run_id": "run-drop-001", "target_run_id": "run-drop-001"})
        missing = client.get("/api/load-cases/loadcase-drop-bottom-001/run-comparison", params={"baseline_run_id": "run-drop-001", "target_run_id": "run-clamp-001"})
        comparison = client.get("/api/load-cases/loadcase-drop-bottom-001/run-comparison", params={"baseline_run_id": "run-drop-baseline-001", "target_run_id": "run-drop-001", "variable_key": "bottom_edge_stress_time"})
        trust_missing = client.get("/api/analysis-runs/not-a-run/trust")
        trust = client.get("/api/analysis-runs/run-showcase-trust-2/trust")
    assert same.status_code == 422 and same.json() == {"detail": "기준 Run과 대상 Run은 달라야 합니다."}
    assert missing.status_code == 404 and missing.json() == {"detail": "선택한 Run을 하중 경우에서 찾을 수 없습니다."}
    assert comparison.status_code == 200 and comparison.json()["time_series"]["variable_key"] == "bottom_edge_stress_time"
    assert trust_missing.status_code == 404 and trust_missing.json() == {"detail": "해석 Run을 찾을 수 없습니다."}
    assert trust.status_code == 200 and trust.json()["trust_status"] == "TRUSTED"
