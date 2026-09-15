from __future__ import annotations

import ast
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import app.main as main_module
from app.adapters.http.routers import requests as request_router
from app.adapters.persistence import requests as request_persistence
from app.application.requests import queries
from app.database_connection import connect
from app.domains.requests.errors import ProjectMembershipRequiredError
from app.main import app
from app.security import hash_password


REQUEST_SQL = "SELECT * FROM analysis_requests WHERE project_id = ? ORDER BY requested_at DESC"
PROJECT_REQUEST_PATH = "/api/projects/{project_id}/requests"


@pytest.mark.contract
def test_project_request_route_preserves_http_openapi_and_global_registration_order() -> None:
    routes = [route for route in app.routes if isinstance(route, APIRoute)]
    route = next(
        route
        for route in routes
        if route.path == PROJECT_REQUEST_PATH and route.methods == {"GET"}
    )
    assert route.endpoint.__module__ == request_router.__name__
    assert route.endpoint.__name__ == "get_requests"
    assert route.response_model == list[dict[str, Any]]
    assert (route.operation_id or route.unique_id) == "get_requests_api_projects__project_id__requests_get"
    assert app.openapi()["paths"][PROJECT_REQUEST_PATH]["get"]["operationId"] == (
        "get_requests_api_projects__project_id__requests_get"
    )

    ordered = [(route.path, tuple(sorted(route.methods or ()))) for route in routes]
    portfolio_gets = [
        index
        for index, (path, methods) in enumerate(ordered)
        if path.startswith("/api/portfolio/") and methods == ("GET",)
    ]
    project_get = ordered.index((PROJECT_REQUEST_PATH, ("GET",)))
    project_post = ordered.index((PROJECT_REQUEST_PATH, ("POST",)))
    request_patch = ordered.index(("/api/requests/{request_id}/assignee", ("PATCH",)))
    load_case_get = ordered.index(("/api/requests/{request_id}/load-cases", ("GET",)))
    assert portfolio_gets and max(portfolio_gets) < project_get < project_post < request_patch < load_case_get


@pytest.mark.unit
def test_main_relinquishes_project_request_list_sql_and_slice_layers_remain_independent() -> None:
    main_path = Path(main_module.__file__)
    source = main_path.read_text(encoding="utf-8")
    assert "def get_requests(" not in source
    assert REQUEST_SQL not in source
    assert "app.include_router(request_query_router)" in source

    actual = sum(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
        for node in ast.walk(ast.parse(source))
    )
    baseline = json.loads((main_path.parents[1] / "scripts" / "architecture_baseline.json").read_text(encoding="utf-8"))
    assert actual <= 59
    assert baseline["execute_call_ceilings"]["app/main.py"] == actual

    router_source = Path(request_router.__file__).read_text(encoding="utf-8")
    assert ".execute(" not in router_source
    assert all(token not in router_source for token in ("write_audit_event", "BEGIN", "COMMIT", "ROLLBACK"))

    for source_path in (Path(queries.__file__), Path("app/domains/requests/ports.py")):
        layer_source = source_path.read_text(encoding="utf-8")
        assert "fastapi" not in layer_source
        assert "database_connection" not in layer_source
        assert "adapters.persistence" not in layer_source


@pytest.mark.unit
def test_application_closes_provider_and_distinguishes_membership_from_authorization_failure() -> None:
    events: list[str] = []
    closed = False

    class Repository:
        def authorize_project(self, project_id: str) -> bool:
            events.append(f"authorize:{project_id}")
            return project_id == "project-ok"

        def list_requests(self, project_id: str) -> list[dict[str, Any]]:
            events.append(f"list:{project_id}")
            return [{"id": "request-1", "project_id": project_id}]

    @contextmanager
    def provider() -> Iterator[Repository]:
        nonlocal closed
        events.append("open")
        try:
            yield Repository()
        finally:
            closed = True
            events.append("close")

    assert queries.list_requests("project-ok", provider) == [{"id": "request-1", "project_id": "project-ok"}]
    assert events == ["open", "authorize:project-ok", "list:project-ok", "close"]
    assert closed

    events.clear()
    closed = False
    with pytest.raises(ProjectMembershipRequiredError) as error:
        queries.list_requests("project-no-membership", provider)
    assert error.value.project_id == "project-no-membership"
    assert events == ["open", "authorize:project-no-membership", "close"]
    assert closed


@pytest.mark.unit
def test_sql_adapter_authorizes_then_queries_on_the_same_connection_and_preserves_exact_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class Connection:
        def __enter__(self) -> "Connection":
            events.append("open")
            return self

        def __exit__(self, *_args: object) -> None:
            events.append("close")

        def execute(self, statement: str, parameters: list[str]) -> object:
            events.append(f"query:{statement}:{parameters}")
            return object()

    connection = Connection()

    def authorize(project_id: str, received_connection: object) -> bool:
        events.append(f"authorize:{project_id}:{received_connection is connection}")
        return True

    monkeypatch.setattr(request_persistence, "rows", lambda _cursor: [{"id": "request-1"}])
    result = queries.list_requests(
        "project-1",
        request_persistence.SQLRequestQueryRepositoryProvider(authorize, lambda: connection),  # type: ignore[arg-type]
    )
    assert result == [{"id": "request-1"}]
    assert events == [
        "open",
        "authorize:project-1:True",
        f"query:{REQUEST_SQL}:['project-1']",
        "close",
    ]


@pytest.mark.unit
def test_router_preserves_generic_permission_detail_and_allows_company_project_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = SimpleNamespace(state=SimpleNamespace())
    generic_detail = {"code": "PERMISSION_DENIED", "project_id": "project-1"}

    class FakeProvider:
        def __init__(self, authorize: Any) -> None:
            self._authorize = authorize

        @contextmanager
        def __call__(self) -> Iterator[Any]:
            class Repository:
                def authorize_project(_self, project_id: str) -> bool:
                    return self._authorize(project_id, object())

                def list_requests(_self, _project_id: str) -> list[dict[str, Any]]:
                    return []

            yield Repository()

    monkeypatch.setattr(request_router, "SQLRequestQueryRepositoryProvider", FakeProvider)
    monkeypatch.setattr(
        request_router,
        "require_permission",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(HTTPException(403, detail=generic_detail)),
    )
    with pytest.raises(HTTPException) as generic_error:
        request_router.get_requests("project-1", request)
    assert generic_error.value.detail == generic_detail
    assert not hasattr(request.state, "authorization_detail")

    monkeypatch.setattr(
        request_router,
        "require_permission",
        lambda *_args, **_kwargs: SimpleNamespace(project_role=None),
    )
    assert request_router.get_requests("project-1", request) == []
    assert not hasattr(request.state, "authorization_detail")


def _add_password_user(
    user_id: str,
    username: str,
    password: str,
    *,
    global_admin: bool = False,
) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO users
                (id, username, password_hash, display_name, legacy_role, account_status,
                 is_global_admin, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'viewer', 'ACTIVE', ?, true, ?, ?)
            """,
            [user_id, username, hash_password(password), username, global_admin, now, now],
        )


@pytest.mark.duckdb_integration
def test_member_and_company_reader_list_order_and_global_admin_empty_projects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suffix = uuid4().hex[:10]
    project_id = "project-tv-001"
    password = "project-request-query-password"
    member_id, member_name = f"member-{suffix}", f"member-{suffix}"
    outsider_id, outsider_name = f"outsider-{suffix}", f"outsider-{suffix}"
    admin_id, admin_name = f"admin-{suffix}", f"admin-{suffix}"
    older_id, newer_id = f"request-old-{suffix}", f"request-new-{suffix}"
    user_ids = [member_id, outsider_id, admin_id]
    try:
        _add_password_user(member_id, member_name, password)
        _add_password_user(outsider_id, outsider_name, password)
        _add_password_user(admin_id, admin_name, password, global_admin=True)
        with connect() as connection:
            connection.execute(
                """INSERT INTO project_memberships
                   (id, project_id, user_id, role, created_by, created_at, updated_by, updated_at)
                   VALUES (?, ?, ?, 'general', 'test', ?, 'test', ?)""",
                [f"membership-{suffix}", project_id, member_id, datetime(2026, 1, 1), datetime(2026, 1, 1)],
            )
            for request_id, requested_at in ((older_id, datetime(2099, 1, 1)), (newer_id, datetime(2099, 1, 2))):
                connection.execute(
                    """INSERT INTO analysis_requests
                       (id, project_id, title, status, owner, requested_at, due_at, overall_note)
                       VALUES (?, ?, ?, 'READY', 'test', ?, ?, 'query contract')""",
                    [request_id, project_id, request_id, requested_at, requested_at],
                )

        monkeypatch.setenv("AUTH_MODE", "password")
        monkeypatch.setenv("AUTH_SECRET_KEY", "test-secret-key-that-is-at-least-32-characters")
        with TestClient(app) as client:
            def headers(username: str) -> dict[str, str]:
                response = client.post("/api/auth/login", json={"username": username, "password": password})
                assert response.status_code == 200, response.text
                return {"Authorization": f"Bearer {response.json()['access_token']}"}

            member_response = client.get(f"/api/projects/{project_id}/requests", headers=headers(member_name))
            assert member_response.status_code == 200, member_response.text
            member_ids = [item["id"] for item in member_response.json()]
            assert member_ids.index(newer_id) < member_ids.index(older_id)

            outsider_headers = headers(outsider_name)
            company_reader = client.get(f"/api/projects/{project_id}/requests", headers=outsider_headers)
            assert company_reader.status_code == 200, company_reader.text
            assert [item["id"] for item in company_reader.json()][:2] == [newer_id, older_id]
            company_reader_write = client.patch(
                f"/api/requests/{older_id}/assignee",
                headers=outsider_headers,
                json={"owner_user_id": member_id},
            )
            assert company_reader_write.status_code == 403
            assert company_reader_write.json()["detail"]["required_permission"] == "request.edit"

            assert client.get(f"/api/projects/{project_id}/requests", headers=headers(admin_name)).status_code == 200
            missing = client.get(f"/api/projects/project-missing-{suffix}/requests", headers=headers(admin_name))
            assert missing.status_code == 200
            assert missing.json() == []

    finally:
        with connect() as connection:
            placeholders = ", ".join("?" for _ in user_ids)
            connection.execute(f"DELETE FROM audit_events WHERE user_id IN ({placeholders})", user_ids)
            connection.execute(f"DELETE FROM project_memberships WHERE user_id IN ({placeholders})", user_ids)
            connection.execute("DELETE FROM analysis_requests WHERE id IN (?, ?)", [older_id, newer_id])
            connection.execute(f"DELETE FROM users WHERE id IN ({placeholders})", user_ids)
