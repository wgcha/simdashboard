from __future__ import annotations

import ast
import json
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.main import app
from app.database import connect
from app.application.portfolio import queries
from app.domains.portfolio.policies import portfolio_csv
from app.adapters.persistence.portfolio import SQLPortfolioRepository


def test_routes_and_openapi_preserve_contract() -> None:
    paths = [route.path for route in app.routes if getattr(route, "path", "").startswith("/api/portfolio")]
    assert paths == ["/api/portfolio/overview", "/api/portfolio/export.csv"]
    schema = app.openapi()
    assert set(schema["paths"]) >= set(paths)
    assert schema["paths"]["/api/portfolio/overview"]["get"]["operationId"] == "get_portfolio_overview_api_portfolio_overview_get"
    assert schema["paths"]["/api/portfolio/export.csv"]["get"]["operationId"] == "export_portfolio_csv_api_portfolio_export_csv_get"
    for path in paths:
        params = {item["name"]: item for item in schema["paths"][path]["get"]["parameters"]}
        assert set(params) == {"date_from", "date_to", "project_id", "analysis_type", "status", "search"}
        assert params["search"]["schema"].get("maxLength", params["search"]["schema"].get("anyOf", [{}])[0].get("maxLength")) == 120
    route_map = {route.path: route for route in app.routes if isinstance(route, APIRoute)}
    for path, name in ((paths[0], "get_portfolio_overview"), (paths[1], "export_portfolio_csv")):
        route = route_map[path]
        assert route.endpoint.__module__ == "app.adapters.http.routers.portfolio"
        assert route.endpoint.__name__ == name
        if path == paths[0]:
            assert route.response_model is not None
        else:
            assert route.response_model is None
    all_paths = [getattr(route, "path", "") for route in app.routes]
    assert max(i for i, path in enumerate(all_paths) if path.startswith("/api/import-schemas")) < all_paths.index("/api/portfolio/overview")
    assert all_paths.index("/api/portfolio/export.csv") < all_paths.index("/api/projects/{project_id}/requests")


def test_application_forwards_all_filters_and_closes_provider() -> None:
    calls: list[dict[str, object]] = []
    closed = False

    class Repo:
        def overview(self, **kwargs):
            calls.append(kwargs)
            return {"records": []}

    @contextmanager
    def provider():
        nonlocal closed
        yield Repo()
        closed = True

    result = queries.get_portfolio_overview(provider, date_from=date(2025, 1, 2), date_to=date(2025, 1, 3), project_id="p", analysis_type="DROP", status="DONE", search="needle")
    assert result == {"records": []}
    assert calls == [{"date_from": date(2025, 1, 2), "date_to": date(2025, 1, 3), "project_id": "p", "analysis_type": "DROP", "status": "DONE", "search": "needle"}]
    assert closed


def test_csv_contract() -> None:
    csv = portfolio_csv([{"project_name": "P", "request_title": "R", "extra": "ignored"}])
    assert csv.startswith("\ufeff")
    assert csv.lstrip("\ufeff").splitlines()[0] == "project_name,product_name,request_title,owner,request_status,requested_at,load_case_name,analysis_type,load_case_status,verdict,result_count,completed_at"
    with TestClient(app) as client:
        response = client.get("/api/portfolio/export.csv")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"] == 'attachment; filename="analysis-portfolio.csv"'


def test_repository_uses_same_connection_for_monitoring_and_propagates_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class Cursor:
        def fetchall(self):
            return [{"request_id": "request-1"}]
        def fetchone(self):
            return None

    class Connection:
        def __init__(self):
            self.calls = []
        def execute(self, sql, params=None):
            self.calls.append((sql, params))
            return Cursor()

    conn = Connection()
    monkeypatch.setattr("app.adapters.persistence.portfolio.rows", lambda _: [{"request_id": "request-1"}])
    def fail(connection, request_id):
        assert connection is conn
        raise RuntimeError("monitoring failure")
    monkeypatch.setattr("app.adapters.persistence.portfolio.request_monitoring_summary", fail)
    with pytest.raises(RuntimeError, match="monitoring failure"):
        SQLPortfolioRepository(conn).overview()
    assert conn.calls and "latest_run" in conn.calls[0][0]


def test_main_relinquishes_portfolio_implementation() -> None:
    source = Path("app/main.py").read_text()
    assert "class PortfolioRepository" not in source
    assert "def get_portfolio_overview" not in source
    assert "def export_portfolio_csv" not in source
    assert "portfolio_router" in source
    actual = sum(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "execute" for node in ast.walk(ast.parse(source)))
    baseline = json.loads(Path("scripts/architecture_baseline.json").read_text())["execute_call_ceilings"]["app/main.py"]
    assert actual <= 67
    assert actual == baseline == 67
    router_source = Path("app/adapters/http/routers/portfolio.py").read_text()
    assert ".execute(" not in router_source
    assert all(token not in router_source for token in ("permission", "audit", "BEGIN", "COMMIT", "ROLLBACK"))
    assert not Path("app/repositories/portfolio.py").exists()


def test_duckdb_endpoints_filters_placeholder_monitoring_and_csv() -> None:
    request_id = f"portfolio-slice-{uuid4().hex[:10]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute("INSERT INTO analysis_requests (id, project_id, title, status, owner, requested_at, due_at, overall_note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [request_id, "project-tv-001", "Portfolio slice placeholder", "READY", "slice", now, now + timedelta(days=1), "test"])
    with TestClient(app) as client:
        base = client.get("/api/portfolio/overview")
        assert base.status_code == 200
        payload = base.json()
        assert payload["records"] and payload["kpis"]["requests"] >= 1
        record = payload["records"][0]
        workflow = client.get(f"/api/requests/{record['request_id']}/workflow").json()
        assert record["request_status"] == workflow["request"]["status"]
        assert record["request_progress"] == workflow["progress"]
        for key, value in (("project_id", record["project_id"]), ("analysis_type", record["analysis_type"]), ("status", record["request_status"]), ("search", record["request_title"]), ("date_from", str(record["requested_at"])[:10]), ("date_to", str(record["requested_at"])[:10])):
            filtered = client.get("/api/portfolio/overview", params={key: value}).json()
            for item in filtered["records"]:
                if key in {"project_id", "analysis_type", "status"}:
                    assert item["project_id" if key == "project_id" else "analysis_type" if key == "analysis_type" else "request_status"] == value
                elif key in {"date_from", "date_to"}:
                    actual_date = date.fromisoformat(str(item["requested_at"])[:10])
                    if key == "date_from":
                        assert actual_date >= date.fromisoformat(value)
                    else:
                        assert actual_date <= date.fromisoformat(value)
                else:
                    assert value.casefold() in " ".join(str(item.get(k) or "") for k in ("project_name", "product_name", "request_title", "load_case_name", "owner")).casefold()
        assert client.get("/api/portfolio/overview", params={"search": "no-such-portfolio-record"}).json()["records"] == []
        placeholder = next(item for item in payload["records"] if item["request_id"] == request_id)
        assert placeholder["load_case_id"] == ""
        assert placeholder["load_case_name"] == "하중 경우 미지정"
        assert placeholder["analysis_type"] == placeholder["load_case_status"] == "UNASSIGNED"
        assert placeholder["request_status"] == "READY"
        csv_response = client.get("/api/portfolio/export.csv", params={"analysis_type": "DROP"})
        assert csv_response.content.startswith(b"\xef\xbb\xbf")
        text = csv_response.content.decode("utf-8-sig")
        assert text.splitlines()[0] == "project_name,product_name,request_title,owner,request_status,requested_at,load_case_name,analysis_type,load_case_status,verdict,result_count,completed_at"
        assert all((not line.strip()) or ",DROP," in line for line in text.splitlines()[1:])
