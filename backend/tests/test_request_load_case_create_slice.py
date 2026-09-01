from __future__ import annotations

import ast
import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, get_type_hints

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.requests import Request

import app.main as main_module
from app.adapters.http.routers import request_load_cases as load_cases_router
from app.adapters.persistence import request_load_cases as load_case_persistence
from app.adapters.persistence.request_load_cases import SQLRequestLoadCaseWriteRepository
from app.application.request_load_cases import commands
from app.database_connection import connect
from app.domains.request_load_cases.errors import RequestNotFoundError
from app.main import app
from app.schemas.api import LoadCaseCreate


LOAD_CASE_PATH = "/api/requests/{request_id}/load-cases"


def _route(routes: list[APIRoute], path: str, method: str) -> APIRoute:
    return next(route for route in routes if route.path == path and route.methods == {method})


@pytest.mark.contract
def test_load_case_get_and_create_routes_keep_exact_contract_and_global_interleaving() -> None:
    routes = [route for route in app.routes if isinstance(route, APIRoute)]
    load_case_get = _route(routes, LOAD_CASE_PATH, "GET")
    load_case_post = _route(routes, LOAD_CASE_PATH, "POST")

    assert [
        (route.endpoint.__module__, route.endpoint.__name__)
        for route in (load_case_get, load_case_post)
    ] == [
        (load_cases_router.__name__, "get_load_cases"),
        (load_cases_router.__name__, "create_load_case"),
    ]
    assert [get_type_hints(route.endpoint)["return"] for route in (load_case_get, load_case_post)] == [
        list[dict[str, Any]],
        dict[str, Any],
    ]
    assert [route.response_model for route in (load_case_get, load_case_post)] == [
        list[dict[str, Any]],
        dict[str, Any],
    ]
    assert [route.status_code for route in (load_case_get, load_case_post)] == [None, 201]
    assert [route.operation_id or route.unique_id for route in (load_case_get, load_case_post)] == [
        "get_load_cases_api_requests__request_id__load_cases_get",
        "create_load_case_api_requests__request_id__load_cases_post",
    ]

    operation = app.openapi()["paths"][LOAD_CASE_PATH]
    assert operation["get"]["operationId"] == "get_load_cases_api_requests__request_id__load_cases_get"
    assert operation["post"]["operationId"] == "create_load_case_api_requests__request_id__load_cases_post"
    assert set(operation["get"]["responses"]) == {"200", "422"}
    assert set(operation["post"]["responses"]) == {"201", "422"}
    assert operation["post"]["requestBody"] == {
        "required": True,
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/LoadCaseCreate"}}},
    }
    assert operation["post"]["parameters"] == [{
        "name": "request_id",
        "in": "path",
        "required": True,
        "schema": {"type": "string", "title": "Request Id"},
    }]

    ordered_cluster = routes[routes.index(load_case_get):routes.index(load_case_post) + 2]
    assert [
        (route.path, tuple(sorted(route.methods or ())), route.endpoint.__module__, route.endpoint.__name__)
        for route in ordered_cluster
    ] == [
        (LOAD_CASE_PATH, ("GET",), load_cases_router.__name__, "get_load_cases"),
        (
            "/api/load-cases/{load_case_id}/drop-videos",
            ("GET",),
            "app.adapters.http.routers.drop_videos",
            "get_drop_videos",
        ),
        ("/api/drop-videos/{video_id}/content", ("HEAD",), "app.main", "get_drop_video_content"),
        ("/api/drop-videos/{video_id}/content", ("GET",), "app.main", "get_drop_video_content"),
        ("/api/drop-videos/{video_id}/download", ("HEAD",), "app.main", "download_drop_video"),
        ("/api/drop-videos/{video_id}/download", ("GET",), "app.main", "download_drop_video"),
        (LOAD_CASE_PATH, ("POST",), load_cases_router.__name__, "create_load_case"),
        (
            "/api/result-import/template/{file_format}",
            ("GET",),
            "app.routers.result_ingestion",
            "get_result_import_template",
        ),
    ]


@pytest.mark.unit
def test_main_relinquishes_create_handler_and_pins_execute_baseline_28() -> None:
    main_path = Path(main_module.__file__)
    source = main_path.read_text(encoding="utf-8")
    assert "def create_load_case(" not in source
    assert "SELECT id FROM analysis_requests WHERE id = ?" not in source
    assert "INSERT INTO load_cases VALUES (?, ?, ?, ?, ?, ?, ?)" not in source
    assert "request_load_case_create_router" in source

    get_include = source.index("app.include_router(request_load_cases_router)")
    catalog_include = source.index("app.include_router(drop_videos_router)")
    content_route = source.index('@app.get("/api/drop-videos/{video_id}/content"')
    download_route = source.index('@app.get("/api/drop-videos/{video_id}/download"')
    create_include = source.index("app.include_router(request_load_case_create_router)")
    ingestion_include = source.index("app.include_router(result_ingestion_router)")
    assert get_include < catalog_include < content_route < download_route < create_include < ingestion_include

    actual = sum(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
        for node in ast.walk(ast.parse(source))
    )
    baseline = json.loads(
        (main_path.parents[1] / "scripts" / "architecture_baseline.json").read_text(encoding="utf-8")
    )
    assert actual == 28
    assert baseline["execute_call_ceilings"]["app/main.py"] == 28

    router_source = Path(load_cases_router.__file__).read_text(encoding="utf-8")
    assert ".execute(" not in router_source
    assert "write_audit_event" not in router_source
    assert all(token not in router_source for token in ("BEGIN", "COMMIT", "ROLLBACK"))


@pytest.mark.unit
def test_create_router_passes_provider_and_exact_request_edit_authorizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    provider = object()
    connection = object()
    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/requests/request-1/load-cases",
        "headers": [],
        "query_string": b"",
    })
    payload = LoadCaseCreate(
        name="  직접 호출 하중  ",
        analysis_type="DROP",
        parameters={"시험": "낙하"},
    )

    def require_permission(
        bound_request: Request,
        permission: str,
        resource_kind: str,
        resource_id: str,
        *,
        conn: object,
    ) -> object:
        events.append(
            ("permission", bound_request, permission, resource_kind, resource_id, conn)
        )
        return object()

    def command(**kwargs: object) -> dict[str, object]:
        events.append((
            "command",
            kwargs["request_id"],
            kwargs["name"],
            kwargs["analysis_type"],
            kwargs["parameters"],
            kwargs["repository_provider"],
        ))
        authorize = kwargs["authorize"]
        assert callable(authorize)
        authorize("request-1", connection)
        return {"status": "created"}

    monkeypatch.setattr(load_cases_router, "require_resource_permission", require_permission)
    monkeypatch.setattr(
        load_cases_router,
        "SQLRequestLoadCaseWriteRepositoryProvider",
        lambda: provider,
    )
    monkeypatch.setattr(load_cases_router, "create_load_case_command", command)

    assert load_cases_router.create_load_case("request-1", payload, request) == {
        "status": "created"
    }
    assert events == [
        (
            "command",
            "request-1",
            "  직접 호출 하중  ",
            "DROP",
            {"시험": "낙하"},
            provider,
        ),
        (
            "permission",
            request,
            load_cases_router.REQUEST_EDIT,
            "request",
            "request-1",
            connection,
        ),
    ]


class _WriteRepository:
    def __init__(self, events: list[object], *, request_exists: bool = True) -> None:
        self.events = events
        self._request_exists = request_exists

    def authorize_resource(self, callback: Any) -> None:
        self.events.append("authorize")
        callback("same-connection")

    def request_exists(self, request_id: str) -> bool:
        self.events.append(("exists", request_id))
        return self._request_exists

    def insert_load_case(self, *values: object) -> None:
        self.events.append(("insert", *values))


def _provider(repository: _WriteRepository, events: list[object]):
    @contextmanager
    def provide() -> Iterator[_WriteRepository]:
        events.append("open")
        try:
            yield repository
        finally:
            events.append("close")

    return provide


@pytest.mark.unit
def test_command_generates_identity_and_time_before_one_provider_then_authorizes_exists_and_inserts() -> None:
    events: list[object] = []
    occurred_at = datetime(2026, 9, 1, 2, 3, 4)
    repository = _WriteRepository(events)

    def identifier() -> str:
        events.append("identifier")
        return "fixed123"

    def clock() -> datetime:
        events.append("clock")
        return occurred_at

    result = commands.create_load_case(
        request_id="request-1",
        name="  하부 낙하 검증  ",
        analysis_type="DROP",
        parameters={"시험": "낙하", "질량_kg": 15},
        authorize=lambda resource_id, connection: events.append(
            ("permission", resource_id, connection)
        ),
        repository_provider=_provider(repository, events),
        identifier_factory=identifier,
        clock=clock,
    )

    assert result == {
        "id": "loadcase-fixed123",
        "request_id": "request-1",
        "name": "하부 낙하 검증",
        "analysis_type": "DROP",
        "status": "READY",
        "parameters": {"시험": "낙하", "질량_kg": 15},
        "created_at": occurred_at,
    }
    assert events == [
        "identifier",
        "clock",
        "open",
        "authorize",
        ("permission", "request-1", "same-connection"),
        ("exists", "request-1"),
        (
            "insert",
            "loadcase-fixed123",
            "request-1",
            "하부 낙하 검증",
            "DROP",
            {"시험": "낙하", "질량_kg": 15},
            occurred_at,
        ),
        "close",
    ]


@pytest.mark.unit
def test_command_authorization_and_missing_request_fail_closed_without_insert() -> None:
    occurred_at = datetime(2026, 9, 1)
    denied_events: list[object] = []
    denied_repository = _WriteRepository(denied_events)

    def deny(resource_id: str, connection: object) -> None:
        denied_events.append(("deny", resource_id, connection))
        raise PermissionError("denied")

    with pytest.raises(PermissionError, match="denied"):
        commands.create_load_case(
            request_id="request-denied",
            name="거부 테스트",
            analysis_type="DROP",
            parameters={},
            authorize=deny,
            repository_provider=_provider(denied_repository, denied_events),
            identifier_factory=lambda: "denied",
            clock=lambda: occurred_at,
        )
    assert denied_events == [
        "open",
        "authorize",
        ("deny", "request-denied", "same-connection"),
        "close",
    ]

    missing_events: list[object] = []
    missing_repository = _WriteRepository(missing_events, request_exists=False)
    with pytest.raises(RequestNotFoundError, match="해석 의뢰를 찾을 수 없습니다."):
        commands.create_load_case(
            request_id="request-missing",
            name="누락 테스트",
            analysis_type="SIDE_CLAMP",
            parameters={},
            authorize=lambda resource_id, connection: missing_events.append(
                ("permission", resource_id, connection)
            ),
            repository_provider=_provider(missing_repository, missing_events),
            identifier_factory=lambda: "missing",
            clock=lambda: occurred_at,
        )
    assert missing_events == [
        "open",
        "authorize",
        ("permission", "request-missing", "same-connection"),
        ("exists", "request-missing"),
        "close",
    ]


class _Cursor:
    def __init__(self, row: tuple[object, ...] | None = None) -> None:
        self._row = row

    def fetchone(self) -> tuple[object, ...] | None:
        return self._row


class _Connection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def execute(self, statement: str, parameters: object = None) -> _Cursor:
        self.calls.append((statement, parameters))
        return _Cursor(("request-1",)) if statement.startswith("SELECT id FROM analysis_requests") else _Cursor()


@pytest.mark.unit
def test_sql_write_adapter_uses_exact_parameters_unicode_json_same_connection_and_no_transaction() -> None:
    connection = _Connection()
    repository = SQLRequestLoadCaseWriteRepository(connection)  # type: ignore[arg-type]
    authorization_connections: list[object] = []
    occurred_at = datetime(2026, 9, 1, 3, 4, 5)
    parameters = {"시험": "하부 낙하", "속도_mps": 4.2}

    repository.authorize_resource(authorization_connections.append)
    assert repository.request_exists("request-1") is True
    repository.insert_load_case(
        "loadcase-1",
        "request-1",
        "한글 하중 조건",
        "DROP",
        parameters,
        occurred_at,
    )

    assert authorization_connections == [connection]
    assert connection.calls == [
        ("SELECT id FROM analysis_requests WHERE id = ?", ["request-1"]),
        (
            "INSERT INTO load_cases VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                "loadcase-1",
                "request-1",
                "한글 하중 조건",
                "DROP",
                "READY",
                '{"시험": "하부 낙하", "속도_mps": 4.2}',
                occurred_at,
            ],
        ),
    ]
    adapter_text = Path(load_case_persistence.__file__).read_text(encoding="utf-8")
    assert all(token not in adapter_text for token in ("BEGIN", "COMMIT", "ROLLBACK"))


@pytest.mark.duckdb_integration
def test_http_validation_precedes_command_and_missing_error_mapping_is_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command_calls: list[dict[str, object]] = []

    def unexpected_command(**kwargs: object) -> dict[str, object]:
        command_calls.append(kwargs)
        raise AssertionError("Pydantic validation must run before the command")

    monkeypatch.setattr(load_cases_router, "create_load_case_command", unexpected_command)
    with TestClient(app) as client:
        malformed = client.post(
            "/api/requests/request-drop-001/load-cases",
            json={"name": "x", "analysis_type": "CRUSH", "parameters": []},
        )
        assert malformed.status_code == 422
        assert command_calls == []

        def missing_command(**kwargs: object) -> dict[str, object]:
            command_calls.append(kwargs)
            raise RequestNotFoundError()

        monkeypatch.setattr(load_cases_router, "create_load_case_command", missing_command)
        missing = client.post(
            "/api/requests/request-missing/load-cases",
            json={"name": "누락 요청", "analysis_type": "DROP", "parameters": {}},
        )
        assert missing.status_code == 404
        assert missing.json() == {"detail": "해석 의뢰를 찾을 수 없습니다."}
        assert len(command_calls) == 1


@pytest.mark.duckdb_integration
def test_duckdb_http_create_returns_and_persists_exact_row_then_cleans_up() -> None:
    created_id: str | None = None
    parameters = {"시험조건": "하부 낙하", "질량_kg": 15, "속도_mps": 4.2}
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/requests/request-drop-001/load-cases",
                json={
                    "name": "  로컬 노트북 기능 검증  ",
                    "analysis_type": "DROP",
                    "parameters": parameters,
                },
            )
        body = response.json()
        if isinstance(body, dict) and isinstance(body.get("id"), str):
            created_id = body["id"]
        assert response.status_code == 201
        assert created_id is not None
        assert created_id.startswith("loadcase-")
        assert body == {
            "id": created_id,
            "request_id": "request-drop-001",
            "name": "로컬 노트북 기능 검증",
            "analysis_type": "DROP",
            "status": "READY",
            "parameters": parameters,
            "created_at": body["created_at"],
        }

        with connect() as connection:
            row = connection.execute(
                "SELECT id, request_id, name, analysis_type, status, parameters_json, created_at "
                "FROM load_cases WHERE id = ?",
                [created_id],
            ).fetchone()
        assert row is not None
        assert row[:5] == (
            created_id,
            "request-drop-001",
            "로컬 노트북 기능 검증",
            "DROP",
            "READY",
        )
        assert json.loads(str(row[5])) == parameters
        assert row[6] == datetime.fromisoformat(body["created_at"])
    finally:
        if created_id is not None:
            with connect() as connection:
                connection.execute("DELETE FROM load_cases WHERE id = ?", [created_id])
