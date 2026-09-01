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
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import app.main as main_module
from app.adapters.http.routers import analysis_pages as analysis_pages_router
from app.adapters.persistence.analysis_pages import SQLAnalysisPageRepository
from app.application.analysis_pages import queries
from app.database import initialize_database
from app.database_connection import connect
from app.domains.analysis_pages import policies as analysis_page_policies
from app.domains.analysis_pages.errors import LoadCaseNotFoundError
from app.domains.analysis_pages.policies import (
    SYSTEM_ANALYSIS_PAGE_IDS,
    sort_analysis_pages,
    summarize_analysis_page,
)
from app.main import app
from app.schemas.api import AnalysisPageSummary
from app.security import hash_password


PUBLIC_PATH = "/api/dashboard-pages"
ADMIN_PATH = "/api/admin/dashboard-pages"
LOAD_CASE_ID = "loadcase-drop-bottom-001"


class FakeRepository:
    def __init__(
        self,
        events: list[str],
        *,
        context: tuple[str, str] | None = ("project-tv-001", "request-drop-001"),
        candidates: list[dict[str, Any]] | None = None,
    ) -> None:
        self.events = events
        self.context = context
        self.candidates = candidates or []

    def load_case_context(self, load_case_id: str) -> tuple[str, str] | None:
        self.events.append(f"context:{load_case_id}")
        return self.context

    def authorize(self, callback: Any) -> None:
        self.events.append("authorize")
        callback(self)

    def list_analysis_pages(
        self,
        load_case_id: str,
        *,
        include_private: bool,
        include_archived: bool,
    ) -> list[dict[str, Any]]:
        self.events.append(f"candidates:{load_case_id}:{include_private}:{include_archived}")
        return self.candidates


def _provider(repository: FakeRepository, events: list[str]):
    @contextmanager
    def provide() -> Iterator[FakeRepository]:
        events.append("open")
        try:
            yield repository
        finally:
            events.append("close")

    return provide


def _definition(
    *,
    key: str = "custom",
    status: str = "published",
    display_order: int = 100,
    is_system: bool = False,
) -> dict[str, Any]:
    return {
        "id": "not-used-by-policy",
        "name": "정책 페이지",
        "description": "",
        "widgets": [],
        "page": {
            "kind": "analysis_page",
            "analysis_key": key,
            "status": status,
            "display_order": display_order,
            "is_system": is_system,
        },
    }


def _candidate(
    dashboard_id: str = "custom-page",
    *,
    load_case_id: str | None = LOAD_CASE_ID,
    name: str = "정책 페이지",
    description: str | None = "설명",
    definition: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": dashboard_id,
        "project_id": "project-tv-001",
        "request_id": "request-drop-001",
        "load_case_id": load_case_id,
        "name": name,
        "description": description,
        "version": 2,
        "updated_at": datetime(2026, 1, 2, 3, 4, 5),
        "definition_json": json.dumps(definition or _definition(), ensure_ascii=False),
    }


@pytest.mark.contract
def test_analysis_page_read_routes_openapi_operation_ids_and_global_order_are_exact() -> None:
    routes = [route for route in app.routes if isinstance(route, APIRoute)]
    target = [
        route
        for route in routes
        if route.path in {PUBLIC_PATH, ADMIN_PATH} and route.methods == {"GET"}
    ]
    assert [(route.path, route.endpoint.__module__, route.endpoint.__name__) for route in target] == [
        (PUBLIC_PATH, analysis_pages_router.__name__, "list_public_dashboard_pages"),
        (ADMIN_PATH, analysis_pages_router.__name__, "list_admin_dashboard_pages"),
    ]
    assert [route.response_model for route in target] == [list[AnalysisPageSummary]] * 2
    assert [route.operation_id or route.unique_id for route in target] == [
        "list_public_dashboard_pages_api_dashboard_pages_get",
        "list_admin_dashboard_pages_api_admin_dashboard_pages_get",
    ]

    paths = app.openapi()["paths"]
    public, admin = paths[PUBLIC_PATH]["get"], paths[ADMIN_PATH]["get"]
    assert public["operationId"] == "list_public_dashboard_pages_api_dashboard_pages_get"
    assert admin["operationId"] == "list_admin_dashboard_pages_api_admin_dashboard_pages_get"
    assert set(public["responses"]) == set(admin["responses"]) == {"200", "422"}
    assert public["responses"]["200"]["content"]["application/json"]["schema"] == {
        "items": {"$ref": "#/components/schemas/AnalysisPageSummary"},
        "type": "array",
        "title": "Response List Public Dashboard Pages Api Dashboard Pages Get",
    }
    assert admin["responses"]["200"]["content"]["application/json"]["schema"] == {
        "items": {"$ref": "#/components/schemas/AnalysisPageSummary"},
        "type": "array",
        "title": "Response List Admin Dashboard Pages Api Admin Dashboard Pages Get",
    }
    assert public["parameters"] == [{
        "name": "load_case_id", "in": "query", "required": True,
        "schema": {"type": "string", "minLength": 1, "maxLength": 120, "title": "Load Case Id"},
    }]
    assert admin["parameters"] == [
        public["parameters"][0],
        {
            "name": "include_archived", "in": "query", "required": False,
            "schema": {"type": "boolean", "default": False, "title": "Include Archived"},
        },
    ]

    def route_index(path: str, method: str) -> int:
        return next(index for index, route in enumerate(routes) if route.path == path and method in (route.methods or set()))

    ordered = [
        route_index("/api/report-templates/{template_id}", "DELETE"),
        route_index(PUBLIC_PATH, "GET"),
        route_index(ADMIN_PATH, "GET"),
        route_index(ADMIN_PATH, "POST"),
        route_index("/api/admin/dashboard-pages/{dashboard_id}", "PATCH"),
        route_index("/api/admin/dashboard-pages/{dashboard_id}", "DELETE"),
        route_index("/api/admin/dashboard-pages/order", "PUT"),
        route_index("/api/dashboards/{dashboard_id}", "GET"),
        route_index("/api/dashboards", "GET"),
        route_index("/api/dashboards/{dashboard_id}", "PUT"),
        route_index("/api/dashboards/{dashboard_id}/versions", "GET"),
        route_index("/api/dashboards/{dashboard_id}/versions/{version}", "GET"),
        route_index("/api/dashboards/{dashboard_id}/versions/{version}", "DELETE"),
        route_index("/api/dashboards/{dashboard_id}/clone", "POST"),
        route_index("/api/dashboards/{dashboard_id}/restore/{version}", "POST"),
        route_index("/api/dashboard-commands/preview", "POST"),
    ]
    assert ordered == sorted(ordered)


@pytest.mark.contract
def test_main_relinquishes_analysis_page_gets_and_router_keeps_http_boundary_clean() -> None:
    main_path = Path(main_module.__file__)
    source = main_path.read_text(encoding="utf-8")
    assert "def list_public_dashboard_pages(" not in source
    assert "def list_admin_dashboard_pages(" not in source
    assert "app.include_router(analysis_pages_router)" in source
    actual = sum(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
        for node in ast.walk(ast.parse(source))
    )
    baseline = json.loads((main_path.parents[1] / "scripts" / "architecture_baseline.json").read_text(encoding="utf-8"))
    assert actual <= 43
    assert actual == baseline["execute_call_ceilings"]["app/main.py"]

    router_source = Path(analysis_pages_router.__file__).read_text(encoding="utf-8")
    assert ".execute(" not in router_source
    assert "write_audit_event" not in router_source
    assert all(token not in router_source for token in ("BEGIN", "COMMIT", "ROLLBACK"))


@pytest.mark.unit
def test_analysis_page_queries_use_one_repository_and_fail_closed_at_each_boundary() -> None:
    public_events: list[str] = []
    public = FakeRepository(public_events)
    assert queries.list_public_analysis_pages(LOAD_CASE_ID, _provider(public, public_events)) == []
    assert public_events == [
        "open", f"context:{LOAD_CASE_ID}", f"candidates:{LOAD_CASE_ID}:False:False", "close",
    ]

    admin_events: list[str] = []
    admin = FakeRepository(admin_events)

    def authorize(project_id: str, connection: object) -> None:
        assert connection is admin
        admin_events.append(f"authorize-callback:{project_id}")

    assert queries.list_admin_analysis_pages(LOAD_CASE_ID, False, authorize, _provider(admin, admin_events)) == []
    assert admin_events == [
        "open", f"context:{LOAD_CASE_ID}", "authorize", "authorize-callback:project-tv-001",
        f"context:{LOAD_CASE_ID}", f"candidates:{LOAD_CASE_ID}:True:False", "close",
    ]

    for query, arguments in (
        (queries.list_public_analysis_pages, (LOAD_CASE_ID,)),
        (queries.list_admin_analysis_pages, (LOAD_CASE_ID, False, lambda _project, _connection: None)),
    ):
        missing_events: list[str] = []
        missing = FakeRepository(missing_events, context=None)
        with pytest.raises(LoadCaseNotFoundError, match="하중 경우를 찾을 수 없습니다."):
            query(*arguments, _provider(missing, missing_events))
        assert missing_events == ["open", f"context:{LOAD_CASE_ID}", "close"]

    denied_events: list[str] = []
    denied = FakeRepository(denied_events)

    def deny(_project_id: str, _connection: object) -> None:
        denied_events.append("deny")
        raise PermissionError("denied")

    with pytest.raises(PermissionError, match="denied"):
        queries.list_admin_analysis_pages(LOAD_CASE_ID, False, deny, _provider(denied, denied_events))
    assert denied_events == ["open", f"context:{LOAD_CASE_ID}", "authorize", "deny", "close"]


@pytest.mark.unit
def test_analysis_page_policy_filters_identity_visibility_sorting_and_malformed_rows() -> None:
    custom = _candidate("custom", description="", definition=_definition(status="published", display_order=2))
    system_draft = _candidate(
        "dashboard-drop-default", load_case_id=None, name="Alpha", definition=_definition(
            key="open_cell", status="draft", display_order=1, is_system=True,
        ),
    )
    system_archived = _candidate(
        "dashboard-chassis-default", load_case_id=None, name="archived", definition=_definition(
            key="chassis_rear", status="archived", display_order=1, is_system=True,
        ),
    )
    foreign_system_flag = _candidate("not-system", definition=_definition(key="open_cell", is_system=True))
    custom_wrong_key = _candidate("custom-wrong-key", definition=_definition(key="open_cell"))
    non_page = _candidate("non-page", definition={"page": {"kind": "other"}})

    public = [
        summarize_analysis_page(
            {key: value for key, value in item.items() if key != "definition_json"},
            json.loads(item["definition_json"]),
            load_case_id=LOAD_CASE_ID,
            include_private=False,
            include_archived=False,
        )
        for item in (
            custom,
            _candidate("draft-public-custom", definition=_definition(status="draft")),
            system_draft,
            system_archived,
            foreign_system_flag,
            custom_wrong_key,
            non_page,
        )
    ]
    assert [item["id"] for item in public if item is not None] == ["custom", "dashboard-drop-default"]
    assert next(item for item in public if item and item["id"] == "custom")["description"] == ""

    draft_custom = _candidate("draft-custom", definition=_definition(status="draft", display_order=2))
    archived_custom = _candidate("archived-custom", definition=_definition(status="archived", display_order=2))
    admin_visible = [
        summarize_analysis_page(
            {key: value for key, value in item.items() if key != "definition_json"}, json.loads(item["definition_json"]),
            load_case_id=LOAD_CASE_ID, include_private=True, include_archived=False,
        )
        for item in (draft_custom, archived_custom, system_archived)
    ]
    assert [item["id"] for item in admin_visible if item is not None] == ["draft-custom"]
    admin_archived = [
        summarize_analysis_page(
            {key: value for key, value in item.items() if key != "definition_json"}, json.loads(item["definition_json"]),
            load_case_id=LOAD_CASE_ID, include_private=True, include_archived=True,
        )
        for item in (draft_custom, archived_custom, system_archived)
    ]
    assert {item["id"] for item in admin_archived if item is not None} == {
        "draft-custom", "archived-custom", "dashboard-chassis-default",
    }

    sort_rows = [
        {"id": "z", "name": "alpha", "page": {"display_order": 4}},
        {"id": "a", "name": "Alpha", "page": {"display_order": 4}},
        {"id": "middle", "name": "Zulu", "page": {"display_order": 3}},
    ]
    assert [item["id"] for item in sort_analysis_pages(sort_rows)] == ["middle", "a", "z"]
    with pytest.raises(KeyError):
        sort_analysis_pages([{"id": "malformed", "name": "x", "page": {}}])
    assert SYSTEM_ANALYSIS_PAGE_IDS == {
        "dashboard-drop-default", "dashboard-chassis-default", "dashboard-run-comparison-default",
    }


class TrackingCursor:
    def __init__(self, *, one: Any = None, many: list[Any] | None = None, columns: list[str] | None = None) -> None:
        self._one = one
        self._many = many or []
        self._columns = columns or []

    def fetchone(self) -> Any:
        return self._one

    def fetchall(self) -> list[Any]:
        return self._many

    def keys(self) -> list[str]:
        return self._columns


class MappingRow:
    """Small SQLAlchemy Row-like object: mapping metadata, value iteration."""

    def __init__(self, values: tuple[Any, ...], keys: tuple[str, ...]) -> None:
        self._values = values
        self._mapping = dict(zip(keys, values, strict=True))

    def __iter__(self):
        return iter(self._values)


class TrackingConnection:
    backend = "postgresql"

    def __init__(self, cursors: list[TrackingCursor]) -> None:
        self.cursors = cursors
        self.calls: list[tuple[str, Any]] = []

    def execute(self, statement: str, parameters: Any = None) -> TrackingCursor:
        self.calls.append((" ".join(statement.split()), parameters))
        return self.cursors.pop(0)


@pytest.mark.unit
def test_analysis_page_sql_mapping_preserves_query_shape_and_open_connection_compatibility() -> None:
    columns = [
        "id", "project_id", "request_id", "load_case_id", "name", "description", "version", "definition_json", "updated_at",
    ]
    definition = _definition()
    mapping_row = MappingRow(
        ("custom-page", "project-tv-001", "request-drop-001", LOAD_CASE_ID, "Custom", "", 1, json.dumps(definition), datetime(2026, 1, 1)),
        tuple(columns),
    )
    connection = TrackingConnection([TrackingCursor(many=[mapping_row], columns=columns)])
    repository = SQLAnalysisPageRepository(connection)
    assert repository.list_analysis_pages(LOAD_CASE_ID, include_private=False, include_archived=False) == [{
        "id": "custom-page", "project_id": "project-tv-001", "request_id": "request-drop-001",
        "load_case_id": LOAD_CASE_ID, "name": "Custom", "description": "", "version": 1,
        "updated_at": datetime(2026, 1, 1), "page": definition["page"],
    }]
    statement, parameters = connection.calls[0]
    assert statement == (
        "SELECT id, project_id, request_id, load_case_id, name, description, version, definition_json, updated_at "
        "FROM dashboards WHERE load_case_id = ? OR id IN "
        "('dashboard-drop-default', 'dashboard-chassis-default', 'dashboard-run-comparison-default')"
    )
    assert "ORDER BY" not in statement
    assert parameters == [LOAD_CASE_ID]

    tuple_connection = TrackingConnection([TrackingCursor(one=("project-tv-001", "request-drop-001"))])
    assert SQLAnalysisPageRepository(tuple_connection).load_case_context(LOAD_CASE_ID) == ("project-tv-001", "request-drop-001")
    mapping_connection = TrackingConnection([
        TrackingCursor(one=MappingRow(("project-tv-001", "request-drop-001"), ("project_id", "request_id")))
    ])
    assert SQLAnalysisPageRepository(mapping_connection).load_case_context(LOAD_CASE_ID) == ("project-tv-001", "request-drop-001")
    assert mapping_connection.calls[0][1] == [LOAD_CASE_ID]

def _insert_password_user(username: str, *, is_global_admin: bool) -> str:
    user_id = f"analysis-page-read-{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO users
                (id, username, password_hash, display_name, legacy_role, is_active,
                 created_at, updated_at, account_status, is_global_admin)
            VALUES (?, ?, ?, ?, ?, true, ?, ?, 'ACTIVE', ?)
            """,
            [
                user_id, username, hash_password("correct-horse-battery-staple"), username,
                "admin" if is_global_admin else "viewer", now, now, is_global_admin,
            ],
        )
    return user_id


def _insert_page(dashboard_id: str, *, status: str, definition_key: str = "custom") -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    definition = _definition(key=definition_key, status=status, display_order=200, is_system=False)
    definition.update({"id": dashboard_id, "name": dashboard_id, "description": ""})
    encoded = json.dumps(definition, ensure_ascii=False)
    with connect() as conn:
        conn.execute(
            "INSERT INTO dashboards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [dashboard_id, "project-tv-001", "request-drop-001", LOAD_CASE_ID, dashboard_id, "", 1, encoded, now],
        )
        conn.execute("INSERT INTO dashboard_versions VALUES (?, ?, ?, ?, ?, ?)", [dashboard_id, 1, encoded, "test", now, True])


@pytest.mark.duckdb_integration
def test_analysis_page_http_duckdb_public_admin_and_password_permission_parity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initialize_database()
    suffix = uuid4().hex[:10]
    published, draft, archived = (f"analysis-read-{kind}-{suffix}" for kind in ("published", "draft", "archived"))
    viewer_username, admin_username = f"analysis-read-viewer-{suffix}", f"analysis-read-admin-{suffix}"
    viewer_id = admin_id = ""
    empty_load_case_id = f"analysis-read-empty-{suffix}"
    try:
        _insert_page(published, status="published")
        _insert_page(draft, status="draft")
        _insert_page(archived, status="archived")
        with TestClient(app) as client:
            public = client.get(PUBLIC_PATH, params={"load_case_id": LOAD_CASE_ID})
            admin = client.get(ADMIN_PATH, params={"load_case_id": LOAD_CASE_ID})
            assert public.status_code == admin.status_code == 200
            assert published in {item["id"] for item in public.json()}
            assert draft not in {item["id"] for item in public.json()}
            assert {published, draft} <= {item["id"] for item in admin.json()}
            assert archived not in {item["id"] for item in admin.json()}
            archived_admin = client.get(ADMIN_PATH, params={"load_case_id": LOAD_CASE_ID, "include_archived": True})
            assert archived_admin.status_code == 200
            assert archived in {item["id"] for item in archived_admin.json()}
            for path in (PUBLIC_PATH, ADMIN_PATH):
                missing = client.get(path, params={"load_case_id": f"missing-{suffix}"})
                assert missing.status_code == 404
                assert missing.json() == {"detail": "하중 경우를 찾을 수 없습니다."}

            # A real load case with neither a qualifying custom page nor a
            # visible system page remains a successful empty read, not a 404.
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            with connect() as conn:
                conn.execute(
                    "INSERT INTO load_cases VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [empty_load_case_id, "request-drop-001", "빈 분석 페이지", "DROP", "READY", "{}", now],
                )
            with monkeypatch.context() as policy_patch:
                policy_patch.setattr(analysis_page_policies, "SYSTEM_ANALYSIS_PAGE_IDS", frozenset())
                assert client.get(PUBLIC_PATH, params={"load_case_id": empty_load_case_id}).json() == []

        viewer_id = _insert_password_user(viewer_username, is_global_admin=False)
        admin_id = _insert_password_user(admin_username, is_global_admin=True)
        monkeypatch.setenv("AUTH_MODE", "password")
        monkeypatch.setenv("AUTH_SECRET_KEY", "analysis-pages-test-secret-key-at-least-32")
        with TestClient(app) as client:
            def headers(username: str) -> dict[str, str]:
                login = client.post("/api/auth/login", json={"username": username, "password": "correct-horse-battery-staple"})
                assert login.status_code == 200, login.text
                return {"Authorization": f"Bearer {login.json()['access_token']}"}

            viewer_headers, admin_headers = headers(viewer_username), headers(admin_username)
            # Public is an authenticated read with no DASHBOARD_EDIT requirement.
            assert client.get(PUBLIC_PATH, params={"load_case_id": LOAD_CASE_ID}, headers=viewer_headers).status_code == 200
            denied = client.get(ADMIN_PATH, params={"load_case_id": LOAD_CASE_ID}, headers=viewer_headers)
            assert denied.status_code == 403
            assert denied.json()["detail"]["required_permission"] == "dashboard.edit"
            assert client.get(ADMIN_PATH, params={"load_case_id": LOAD_CASE_ID}, headers=admin_headers).status_code == 200
    finally:
        with connect() as conn:
            for dashboard_id in (published, draft, archived):
                conn.execute("DELETE FROM dashboard_versions WHERE dashboard_id = ?", [dashboard_id])
                conn.execute("DELETE FROM dashboards WHERE id = ?", [dashboard_id])
            conn.execute("DELETE FROM load_cases WHERE id = ?", [empty_load_case_id])
            if viewer_id or admin_id:
                conn.execute("DELETE FROM audit_events WHERE user_id IN (?, ?)", [viewer_id, admin_id])
                conn.execute("DELETE FROM users WHERE id IN (?, ?)", [viewer_id, admin_id])
