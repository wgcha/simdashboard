from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import app.main as main_module
from app.adapters.http.routers import system_health as system_health_router
from app.adapters.persistence.system_health import SQLSystemHealthProbe
from app.application.system_health.queries import health as health_query
from app.database_connection import connect
from app.main import app


HEALTH_PATH = "/api/health"


@pytest.mark.contract
def test_system_health_router_owns_exact_route_openapi_and_global_adjacency() -> None:
    routes = [route for route in app.routes if isinstance(route, APIRoute)]
    selected = next(route for route in routes if route.path == HEALTH_PATH)

    assert selected.methods == {"GET"}
    assert selected.endpoint.__module__ == system_health_router.__name__
    assert selected.endpoint.__name__ == "health"
    assert (selected.operation_id or selected.unique_id) == "health_api_health_get"
    assert selected.response_model == dict[str, str]
    assert selected.status_code is None

    operation = app.openapi()["paths"][HEALTH_PATH]["get"]
    assert operation["operationId"] == "health_api_health_get"
    assert set(operation["responses"]) == {"200"}
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "additionalProperties": {"type": "string"},
        "type": "object",
        "title": "Response Health Api Health Get",
    }
    assert "parameters" not in operation

    index = routes.index(selected)
    cluster = routes[index - 1:index + 2]
    assert [
        (route.path, tuple(sorted(route.methods or ())), route.endpoint.__module__, route.endpoint.__name__)
        for route in cluster
    ] == [
        (
            "/api/result-imports/{job_id}/retry",
            ("POST",),
            "app.routers.result_folder_refresh",
            "retry_result_import",
        ),
        (HEALTH_PATH, ("GET",), system_health_router.__name__, "health"),
        (
            "/api/feature-examples",
            ("GET",),
            "app.adapters.http.routers.feature_examples",
            "feature_examples",
        ),
    ]


@pytest.mark.contract
def test_main_relinquishes_health_handler_and_keeps_monotonic_execute_baseline() -> None:
    main_path = Path(main_module.__file__)
    source = main_path.read_text(encoding="utf-8")

    assert "def health(" not in source
    assert "database_settings" not in source
    assert "app.include_router(system_health_router)" in source
    retry_include = source.index("app.include_router(result_folder_refresh_router)")
    health_include = source.index("app.include_router(system_health_router)")
    examples_include = source.index("app.include_router(feature_examples_router)")
    assert retry_include < health_include < examples_include

    actual = sum(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
        for node in ast.walk(ast.parse(source))
    )
    baseline = json.loads(
        (main_path.parents[1] / "scripts" / "architecture_baseline.json").read_text(encoding="utf-8")
    )
    assert actual <= 57
    assert baseline["execute_call_ceilings"]["app/main.py"] == actual

    router_source = Path(system_health_router.__file__).read_text(encoding="utf-8")
    assert ".execute(" not in router_source
    assert "write_audit_event" not in router_source
    assert "require_permission" not in router_source
    assert all(token not in router_source for token in ("BEGIN", "COMMIT", "ROLLBACK"))


@pytest.mark.unit
def test_health_query_calls_probe_then_backend_exactly_once() -> None:
    events: list[str] = []

    def probe() -> None:
        events.append("probe")

    def backend() -> str:
        events.append("backend")
        return "duckdb"

    assert health_query(probe, backend) == {
        "status": "ok",
        "database_backend": "duckdb",
    }
    assert events == ["probe", "backend"]


@pytest.mark.unit
def test_health_query_preserves_probe_failure_and_does_not_read_backend() -> None:
    events: list[str] = []
    failure = RuntimeError("database unavailable")

    def probe() -> None:
        events.append("probe")
        raise failure

    def backend() -> str:
        events.append("backend")
        return "must-not-be-read"

    with pytest.raises(RuntimeError) as error:
        health_query(probe, backend)
    assert error.value is failure
    assert events == ["probe"]


class FakeCursor:
    def __init__(
        self,
        events: list[Any],
        result: tuple[int] | None,
        failure: BaseException | None = None,
    ) -> None:
        self.events = events
        self.result = result
        self.failure = failure

    def fetchone(self) -> tuple[int] | None:
        self.events.append("fetchone")
        if self.failure is not None:
            raise self.failure
        return self.result


class FakeConnection:
    def __init__(
        self,
        events: list[Any],
        result: tuple[int] | None,
        *,
        execute_failure: BaseException | None = None,
        fetch_failure: BaseException | None = None,
    ) -> None:
        self.events = events
        self.result = result
        self.execute_failure = execute_failure
        self.fetch_failure = fetch_failure

    def __enter__(self) -> FakeConnection:
        self.events.append("enter")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: Any,
    ) -> bool:
        del traceback
        self.events.append(("exit", exc_type, exc))
        return False

    def execute(self, statement: str, *parameters: Any) -> FakeCursor:
        self.events.append(("execute", statement, parameters))
        if self.execute_failure is not None:
            raise self.execute_failure
        return FakeCursor(self.events, self.result, self.fetch_failure)


@pytest.mark.unit
@pytest.mark.parametrize("result", [(1,), None])
def test_sql_health_probe_preserves_exact_connection_lifecycle_and_ignores_row(
    result: tuple[int] | None,
) -> None:
    events: list[Any] = []
    connection = FakeConnection(events, result)

    def provider() -> FakeConnection:
        events.append("provider")
        return connection

    assert SQLSystemHealthProbe(provider)() is None
    assert events == [
        "provider",
        "enter",
        ("execute", "SELECT 1", ()),
        "fetchone",
        ("exit", None, None),
    ]


@pytest.mark.unit
@pytest.mark.parametrize("stage", ["execute", "fetchone"])
def test_sql_health_probe_preserves_failure_identity_and_always_exits(stage: str) -> None:
    events: list[Any] = []
    failure = RuntimeError(f"{stage} failed")
    connection = FakeConnection(
        events,
        (1,),
        execute_failure=failure if stage == "execute" else None,
        fetch_failure=failure if stage == "fetchone" else None,
    )

    def provider() -> FakeConnection:
        events.append("provider")
        return connection

    with pytest.raises(RuntimeError) as error:
        SQLSystemHealthProbe(provider)()
    assert error.value is failure
    assert events[:3] == ["provider", "enter", ("execute", "SELECT 1", ())]
    if stage == "fetchone":
        assert events[3] == "fetchone"
    assert events[-1] == ("exit", RuntimeError, failure)


@pytest.mark.duckdb_integration
def test_health_http_is_exact_public_ignores_invalid_token_and_writes_no_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-secret-key-that-is-at-least-32-characters")
    request_ids = [f"health-{uuid4().hex}", f"health-{uuid4().hex}"]

    with TestClient(app) as client:
        anonymous = client.get(HEALTH_PATH, headers={"X-Request-Id": request_ids[0]})
        invalid_token = client.get(
            HEALTH_PATH,
            headers={
                "Authorization": "Bearer deliberately-invalid",
                "X-Request-Id": request_ids[1],
            },
        )

    for response, request_id in zip((anonymous, invalid_token), request_ids, strict=True):
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "database_backend": "duckdb"}
        assert response.headers["X-Request-Id"] == request_id

    with connect() as connection:
        placeholders = ", ".join("?" for _ in request_ids)
        count = connection.execute(
            f"SELECT count(*) FROM audit_events WHERE request_id IN ({placeholders})",
            request_ids,
        ).fetchone()[0]
    assert count == 0


@pytest.mark.duckdb_integration
def test_health_http_keeps_unmapped_internal_server_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = RuntimeError("health probe failed")
    monkeypatch.setattr(
        system_health_router,
        "health_query",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(HEALTH_PATH)

    assert response.status_code == 500
    assert response.text == "Internal Server Error"
