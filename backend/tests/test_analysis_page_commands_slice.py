from __future__ import annotations

import ast
import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import app.main as main_module
from app.adapters.http.routers import analysis_pages as analysis_pages_router
from app.adapters.persistence.analysis_pages import SQLAnalysisPageCommandRepository
from app.application.analysis_pages import commands
from app.database import initialize_database
from app.database_connection import connect
from app.domains.analysis_pages.errors import AnalysisPageNameTooShortError
from app.main import app
from app.schemas.api import AnalysisPageSummary, DashboardDefinition


PUBLIC_PATH = "/api/dashboard-pages"
ADMIN_PATH = "/api/admin/dashboard-pages"
ORDER_PATH = f"{ADMIN_PATH}/order"
LOAD_CASE_ID = "loadcase-drop-bottom-001"

NAME_TOO_SHORT = "분석 페이지 이름은 두 글자 이상이어야 합니다."
LOAD_CASE_MISSING = "하중 경우를 찾을 수 없습니다."
DUPLICATE_NAME = "같은 하중 경우에 동일한 분석 페이지 이름이 이미 있습니다."
NOT_MANAGEABLE = "관리 가능한 분석 페이지가 아닙니다."
SYSTEM_LIFECYCLE = "시스템 기본 분석 페이지의 생명주기는 변경할 수 없습니다."
PUBLISH_WITHOUT_WIDGET = "위젯이 없는 분석 페이지는 게시할 수 없습니다."
CUSTOM_DELETE_ONLY = "사용자 정의 분석 페이지만 영구 삭제할 수 있습니다."
WRONG_LOAD_CASE = "분석 페이지가 요청한 하중 경우에 속하지 않습니다."
DUPLICATE_ORDER = "분석 페이지 순서에 중복 ID가 있습니다."
ORDER_SET_MISMATCH = "현재 하중 경우의 보관되지 않은 사용자 분석 페이지를 모두 한 번씩 지정해야 합니다."


def _detail(response: Any) -> str:
    assert isinstance(response.json(), dict)
    return response.json()["detail"]


def _route_index(routes: list[APIRoute], path: str, method: str) -> int:
    return next(index for index, route in enumerate(routes) if route.path == path and method in (route.methods or set()))


@pytest.mark.contract
def test_analysis_page_command_routes_keep_exact_contract_owner_openapi_and_global_order() -> None:
    """The write extraction must leave the public/read router first in app order."""
    routes = [route for route in app.routes if isinstance(route, APIRoute)]
    command_routes = [
        next(route for route in routes if route.path == path and route.methods == {method})
        for path, method in (
            (ADMIN_PATH, "POST"),
            (f"{ADMIN_PATH}/{{dashboard_id}}", "PATCH"),
            (f"{ADMIN_PATH}/{{dashboard_id}}", "DELETE"),
            (ORDER_PATH, "PUT"),
        )
    ]
    assert [(route.endpoint.__module__, route.endpoint.__name__) for route in command_routes] == [
        (analysis_pages_router.__name__, "create_dashboard_page"),
        (analysis_pages_router.__name__, "update_dashboard_page"),
        (analysis_pages_router.__name__, "delete_dashboard_page"),
        (analysis_pages_router.__name__, "reorder_dashboard_pages"),
    ]
    assert [route.response_model for route in command_routes] == [
        DashboardDefinition,
        DashboardDefinition,
        dict[str, str],
        list[AnalysisPageSummary],
    ]
    assert [route.status_code for route in command_routes] == [201, None, None, None]
    assert [route.operation_id or route.unique_id for route in command_routes] == [
        "create_dashboard_page_api_admin_dashboard_pages_post",
        "update_dashboard_page_api_admin_dashboard_pages__dashboard_id__patch",
        "delete_dashboard_page_api_admin_dashboard_pages__dashboard_id__delete",
        "reorder_dashboard_pages_api_admin_dashboard_pages_order_put",
    ]

    schema = app.openapi()
    expected_operations = {
        (ADMIN_PATH, "post"): "create_dashboard_page_api_admin_dashboard_pages_post",
        (f"{ADMIN_PATH}/{{dashboard_id}}", "patch"): "update_dashboard_page_api_admin_dashboard_pages__dashboard_id__patch",
        (f"{ADMIN_PATH}/{{dashboard_id}}", "delete"): "delete_dashboard_page_api_admin_dashboard_pages__dashboard_id__delete",
        (ORDER_PATH, "put"): "reorder_dashboard_pages_api_admin_dashboard_pages_order_put",
    }
    assert {key: schema["paths"][key[0]][key[1]]["operationId"] for key in expected_operations} == expected_operations
    assert set(schema["paths"][ADMIN_PATH]["post"]["responses"]) == {"201", "422"}
    assert set(schema["paths"][f"{ADMIN_PATH}/{{dashboard_id}}"]["patch"]["responses"]) == {"200", "422"}
    assert set(schema["paths"][f"{ADMIN_PATH}/{{dashboard_id}}"]["delete"]["responses"]) == {"200", "422"}
    assert set(schema["paths"][ORDER_PATH]["put"]["responses"]) == {"200", "422"}
    delete_parameters = schema["paths"][f"{ADMIN_PATH}/{{dashboard_id}}"]["delete"]["parameters"]
    assert delete_parameters == [
        {"name": "dashboard_id", "in": "path", "required": True, "schema": {"type": "string", "title": "Dashboard Id"}},
        {"name": "load_case_id", "in": "query", "required": True, "schema": {"type": "string", "minLength": 1, "maxLength": 120, "title": "Load Case Id"}},
    ]

    ordered = [
        _route_index(routes, PUBLIC_PATH, "GET"),
        _route_index(routes, ADMIN_PATH, "GET"),
        _route_index(routes, ADMIN_PATH, "POST"),
        _route_index(routes, f"{ADMIN_PATH}/{{dashboard_id}}", "PATCH"),
        _route_index(routes, f"{ADMIN_PATH}/{{dashboard_id}}", "DELETE"),
        _route_index(routes, ORDER_PATH, "PUT"),
        _route_index(routes, "/api/dashboards/{dashboard_id}", "GET"),
        _route_index(routes, "/api/dashboards/{dashboard_id}", "PUT"),
        _route_index(routes, "/api/dashboards/{dashboard_id}/versions", "GET"),
    ]
    assert ordered == sorted(ordered)


@pytest.mark.unit
def test_main_relinquishes_analysis_page_commands_and_command_router_has_no_sql_or_transaction() -> None:
    main_path = Path(main_module.__file__)
    source = main_path.read_text(encoding="utf-8")
    for handler in (
        "create_dashboard_page",
        "update_dashboard_page",
        "delete_dashboard_page",
        "reorder_dashboard_pages",
    ):
        assert f"def {handler}(" not in source
    for helper in (
        "_page_name_exists",
        "_delete_analysis_page_records",
    ):
        assert f"def {helper}(" not in source
    assert "app.include_router(analysis_pages_router)" in source

    actual = sum(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "execute"
        for node in ast.walk(ast.parse(source))
    )
    baseline = json.loads((main_path.parents[1] / "scripts" / "architecture_baseline.json").read_text(encoding="utf-8"))
    assert actual <= 43
    assert baseline["execute_call_ceilings"]["app/main.py"] == actual

    router_source = Path(analysis_pages_router.__file__).read_text(encoding="utf-8")
    assert ".execute(" not in router_source
    assert "write_audit_event" not in router_source
    assert all(token not in router_source for token in ("BEGIN", "COMMIT", "ROLLBACK"))


def _cleanup_dashboards(prefix: str) -> None:
    with connect() as conn:
        dashboard_ids = [row[0] for row in conn.execute("SELECT id FROM dashboards WHERE id LIKE ?", [f"{prefix}%"]).fetchall()]
        for dashboard_id in dashboard_ids:
            conn.execute("DELETE FROM dashboard_versions WHERE dashboard_id = ?", [dashboard_id])
            conn.execute("DELETE FROM dashboards WHERE id = ?", [dashboard_id])


def _definition(
    dashboard_id: str = "dashboard-custom",
    *,
    name: str = "사용자 페이지",
    widgets: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "id": dashboard_id,
        "name": name,
        "description": "기존 설명",
        "widgets": widgets or [],
        "page": {
            "kind": "analysis_page", "analysis_key": "custom", "status": "draft",
            "display_order": 100, "is_system": False,
        },
    }


class CommandFake:
    def __init__(self, events: list[object], *, context: tuple[str, str] | None = ("project-tv-001", "request-drop-001"), item: dict[str, object] | None = None, candidates: list[dict[str, object]] | None = None, duplicate: bool = False, rollback_error: Exception | None = None) -> None:
        self.events, self.context, self.item = events, context, item
        self.candidates, self.duplicate, self.rollback_error = candidates or [], duplicate, rollback_error

    def load_case_context(self, load_case_id: str): self.events.append(("context", load_case_id)); return self.context
    def authorize(self, callback: Any): self.events.append("authorize"); callback(self)
    def authorize_resource(self, callback: Any): self.events.append("resource-auth"); callback(self)
    def page_name_exists(self, load_case_id: str, name: str, exclude_id: str | None = None): self.events.append(("duplicate", load_case_id, name, exclude_id)); return self.duplicate
    def next_custom_display_order(self, load_case_id: str): self.events.append(("order", load_case_id)); return 201
    def insert_analysis_page(self, *args: object): self.events.append(("insert", *args))
    def get_analysis_page(self, dashboard_id: str): self.events.append(("get", dashboard_id)); return self.item
    def get_analysis_page_for_delete(self, dashboard_id: str):
        self.events.append(("get", dashboard_id))
        return None if self.item is None else (self.item["load_case_id"], self.item["definition"])
    def write_dashboard_definition(self, dashboard_id: str, definition: dict[str, object], created_by: str): self.events.append(("write", dashboard_id, definition, created_by)); return 2, datetime(2026, 1, 1)
    def reorderable_analysis_pages(self, load_case_id: str): self.events.append(("candidates", load_case_id)); return self.candidates
    def list_analysis_pages(self, load_case_id: str, *, include_private: bool, include_archived: bool): self.events.append(("list", load_case_id, include_private, include_archived)); return self.candidates
    def begin_transaction(self): self.events.append("BEGIN")
    def commit_transaction(self): self.events.append("COMMIT")
    def rollback_transaction(self):
        self.events.append("ROLLBACK")
        if self.rollback_error: raise self.rollback_error
    def delete_analysis_page_records(self, dashboard_id: str): self.events.append(("delete", dashboard_id))


def _provider(repository: CommandFake, events: list[object]):
    @contextmanager
    def provide() -> Iterator[CommandFake]:
        events.append("open")
        try: yield repository
        finally: events.append("close")
    return provide


@pytest.mark.unit
def test_analysis_page_command_fakes_preserve_create_update_and_reorder_stop_points() -> None:
    create_events: list[object] = []
    created = commands.create_analysis_page(
        load_case_id=LOAD_CASE_ID, name="  생성 페이지  ", description=" 설명 ",
        authorize=lambda project_id, connection: create_events.append(("permission", project_id, connection)),
        repository_provider=_provider(CommandFake(create_events), create_events),
        validate_definition=lambda definition: DashboardDefinition.model_validate(definition).model_dump(),
        identifier_factory=lambda: "fixed", clock=lambda: datetime(2026, 9, 1),
    )
    assert created["id"] == "dashboard-fixed"
    assert created["name"] == "생성 페이지"
    assert created["description"] == "설명"
    assert created["page"] == {"kind": "analysis_page", "analysis_key": "custom", "status": "draft", "display_order": 201, "is_system": False}
    assert [event if isinstance(event, str) else event[0] for event in create_events] == ["open", "context", "authorize", "permission", "duplicate", "order", "insert", "close"]
    assert create_events[-2][1:5] == ("dashboard-fixed", "project-tv-001", "request-drop-001", LOAD_CASE_ID)

    invalid_events: list[object] = []
    with pytest.raises(AnalysisPageNameTooShortError):
        commands.create_analysis_page(load_case_id=LOAD_CASE_ID, name=" x ", authorize=lambda *_args: None, repository_provider=_provider(CommandFake(invalid_events), invalid_events), validate_definition=lambda definition: definition)
    assert invalid_events == []
    missing_events: list[object] = []
    with pytest.raises(Exception) as missing:
        commands.create_analysis_page(load_case_id=LOAD_CASE_ID, name="이름", authorize=lambda *_args: None, repository_provider=_provider(CommandFake(missing_events, context=None), missing_events), validate_definition=lambda definition: definition)
    assert type(missing.value).__name__ == "LoadCaseNotFoundError"
    assert missing_events == ["open", ("context", LOAD_CASE_ID), "close"]

    update_events: list[object] = []
    update_fake = CommandFake(update_events, item={"id": "dashboard-update", "load_case_id": LOAD_CASE_ID, "version": 1, "definition": _definition("dashboard-update", widgets=[{"id": "kpi", "type": "kpi", "title": "KPI", "x": 0, "y": 0, "w": 3, "h": 2, "settings": {}}])})
    updated = commands.update_analysis_page(dashboard_id="dashboard-update", name=" 수정 ", description=None, status="published", authorize=lambda dashboard_id, connection: update_events.append(("resource", dashboard_id, connection)), repository_provider=_provider(update_fake, update_events), validate_definition=lambda definition: DashboardDefinition.model_validate(definition).model_dump())
    assert updated["name"] == "수정" and updated["page"]["status"] == "published"
    assert [event if isinstance(event, str) else event[0] for event in update_events] == ["open", "resource-auth", "resource", "get", "duplicate", "write", "close"]

    first = {"id": "one", "version": 1, "definition": _definition("one")}
    second = {"id": "two", "version": 7, "definition": _definition("two")}
    reorder_events: list[object] = []
    reordered = commands.reorder_analysis_pages(load_case_id=LOAD_CASE_ID, page_ids=["two", "one"], authorize=lambda project_id, connection: reorder_events.append(("permission", project_id, connection)), repository_provider=_provider(CommandFake(reorder_events, candidates=[first, second]), reorder_events), validate_definition=lambda definition: DashboardDefinition.model_validate(definition).model_dump())
    assert reordered == [first, second]
    assert second["definition"]["page"]["display_order"] == 100
    assert first["definition"]["page"]["display_order"] == 101
    assert [event if isinstance(event, str) else event[0] for event in reorder_events] == ["open", "context", "authorize", "permission", "candidates", "write", "write", "context", "list", "close"]


@pytest.mark.unit
def test_analysis_page_delete_command_keeps_legacy_commit_rollback_and_exception_identity() -> None:
    item = {"id": "dashboard-delete", "load_case_id": LOAD_CASE_ID, "version": 1, "definition": _definition("dashboard-delete")}
    events: list[object] = []
    assert commands.delete_analysis_page(dashboard_id="dashboard-delete", load_case_id=LOAD_CASE_ID, authorize=lambda dashboard_id, connection: events.append(("resource", dashboard_id, connection)), repository_provider=_provider(CommandFake(events, item=item), events)) == {"status": "deleted", "id": "dashboard-delete", "load_case_id": LOAD_CASE_ID}
    assert [event if isinstance(event, str) else event[0] for event in events] == ["open", "resource-auth", "resource", "get", "context", "BEGIN", "delete", "COMMIT", "close"]

    body_error = RuntimeError("body error")
    rollback_events: list[object] = []
    rollback_fake = CommandFake(rollback_events, item=item)
    with pytest.raises(RuntimeError) as raised:
        commands.delete_analysis_page(dashboard_id="dashboard-delete", load_case_id=LOAD_CASE_ID, authorize=lambda *_args: None, repository_provider=_provider(rollback_fake, rollback_events), delete_records=lambda repository, dashboard_id: (repository.delete_analysis_page_records(dashboard_id), (_ for _ in ()).throw(body_error)))
    assert raised.value is body_error
    assert [event if isinstance(event, str) else event[0] for event in rollback_events] == ["open", "resource-auth", "get", "context", "BEGIN", "delete", "ROLLBACK", "close"]

    rollback_error = RuntimeError("rollback error")
    rollback_failure_events: list[object] = []
    with pytest.raises(RuntimeError) as raised_rollback:
        commands.delete_analysis_page(dashboard_id="dashboard-delete", load_case_id=LOAD_CASE_ID, authorize=lambda *_args: None, repository_provider=_provider(CommandFake(rollback_failure_events, item=item, rollback_error=rollback_error), rollback_failure_events), delete_records=lambda *_args: (_ for _ in ()).throw(body_error))
    assert raised_rollback.value is rollback_error


@pytest.mark.unit
def test_analysis_page_command_sql_adapter_keeps_postgresql_mapping_parameters_and_transaction_boundaries() -> None:
    class Cursor:
        def __init__(self, *, one: tuple[object, ...] | None = None, many: list[tuple[object, ...]] | None = None, keys: list[str] | None = None) -> None:
            self._one, self._many, self._keys = one, many or [], keys or []
        def fetchone(self): return self._one
        def fetchall(self): return self._many
        def keys(self): return self._keys

    class Connection:
        backend = "postgresql"
        def __init__(self, cursors: list[Cursor]) -> None: self.cursors, self.calls = cursors, []
        def execute(self, statement: str, parameters: object = None):
            self.calls.append((" ".join(statement.split()), parameters))
            return self.cursors.pop(0)

    definition = _definition("dashboard-sql", name="SQL 페이지")
    encoded = json.dumps(definition, ensure_ascii=False)
    connection = Connection([
        Cursor(),
        Cursor(),
        Cursor(one=(4,)),
        Cursor(),
        Cursor(),
        Cursor(one=(LOAD_CASE_ID, encoded)),
        Cursor(),
        Cursor(),
        Cursor(),
    ])
    repository = SQLAnalysisPageCommandRepository(connection)
    occurred_at = datetime(2026, 9, 1, 1, 2, 3)
    repository.insert_analysis_page("dashboard-sql", "project-tv-001", "request-drop-001", LOAD_CASE_ID, definition, occurred_at)
    version, written_at = repository.write_dashboard_definition("dashboard-sql", definition, "관리자")
    assert version == 4 and isinstance(written_at, datetime)
    assert connection.calls[0] == (
        "INSERT INTO dashboards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ["dashboard-sql", "project-tv-001", "request-drop-001", LOAD_CASE_ID, "SQL 페이지", "기존 설명", 1, encoded, occurred_at],
    )
    assert connection.calls[1] == ("INSERT INTO dashboard_versions VALUES (?, ?, ?, ?, ?, ?)", ["dashboard-sql", 1, encoded, "관리자", occurred_at, True])
    assert connection.calls[2] == ("SELECT COALESCE(max(version), 0) + 1 FROM dashboard_versions WHERE dashboard_id = ?", ["dashboard-sql"])
    assert connection.calls[3][0] == "UPDATE dashboards SET name = ?, description = ?, version = ?, definition_json = ?, updated_at = ? WHERE id = ?"
    assert connection.calls[3][1][:4] == ["SQL 페이지", "기존 설명", 4, encoded]
    assert connection.calls[3][1][-1] == "dashboard-sql"
    assert connection.calls[4][0] == "INSERT INTO dashboard_versions VALUES (?, ?, ?, ?, ?, ?)"
    assert connection.calls[4][1][:4] == ["dashboard-sql", 4, encoded, "관리자"]
    assert all(call[0] not in {"BEGIN TRANSACTION", "COMMIT", "ROLLBACK"} for call in connection.calls)

    assert repository.get_analysis_page_for_delete("dashboard-sql") == (LOAD_CASE_ID, definition)
    assert connection.calls[5] == (
        "SELECT load_case_id, definition_json FROM dashboards WHERE id = ?",
        ["dashboard-sql"],
    )

    repository.begin_transaction(); repository.commit_transaction(); repository.rollback_transaction()
    assert [call[0] for call in connection.calls[-3:]] == ["BEGIN TRANSACTION", "COMMIT", "ROLLBACK"]


@pytest.mark.duckdb_integration
def test_analysis_page_commands_preserve_http_lifecycle_messages_orders_versions_and_dashboard_compatibility() -> None:
    """Exercise the command slice end-to-end without touching seeded system pages."""
    initialize_database()
    prefix = f"analysis-command-{uuid4().hex[:10]}"
    created_ids: list[str] = []
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            too_short = client.post(ADMIN_PATH, json={"load_case_id": LOAD_CASE_ID, "name": " a ", "description": "x"})
            assert too_short.status_code == 422
            assert _detail(too_short) == NAME_TOO_SHORT
            missing = client.post(ADMIN_PATH, json={"load_case_id": f"missing-{prefix}", "name": "유효 이름", "description": "x"})
            assert missing.status_code == 404
            assert _detail(missing) == LOAD_CASE_MISSING

            first = client.post(ADMIN_PATH, json={"load_case_id": LOAD_CASE_ID, "name": f" {prefix}-A ", "description": " 설명 A "})
            assert first.status_code == 201, first.text
            first_definition = first.json()
            first_id = first_definition["id"]
            created_ids.append(first_id)
            assert first_definition["name"] == f"{prefix}-A"
            assert first_definition["description"] == "설명 A"
            assert first_definition["widgets"] == []
            assert first_definition["page"] == {"kind": "analysis_page", "analysis_key": "custom", "status": "draft", "display_order": 100, "is_system": False}
            duplicate = client.post(ADMIN_PATH, json={"load_case_id": LOAD_CASE_ID, "name": f" {prefix}-a ", "description": "dup"})
            assert duplicate.status_code == 409
            assert _detail(duplicate) == DUPLICATE_NAME
            publish_without_widget = client.patch(f"{ADMIN_PATH}/{first_id}", json={"status": "published"})
            assert publish_without_widget.status_code == 422
            assert _detail(publish_without_widget) == PUBLISH_WITHOUT_WIDGET

            archive = client.patch(f"{ADMIN_PATH}/{first_id}", json={"status": "archived"})
            assert archive.status_code == 200
            second = client.post(ADMIN_PATH, json={"load_case_id": LOAD_CASE_ID, "name": f"{prefix}-B", "description": "B"})
            assert second.status_code == 201, second.text
            second_id = second.json()["id"]
            created_ids.append(second_id)
            # Archived custom pages intentionally remain in the create-order max.
            assert second.json()["page"]["display_order"] == 101

            non_page = client.post("/api/dashboards/dashboard-drop-default/clone", json={"name": f"{prefix}-clone", "description": "clone"})
            assert non_page.status_code == 201, non_page.text
            clone_id = non_page.json()["id"]
            created_ids.append(clone_id)
            not_page = client.patch(f"{ADMIN_PATH}/{clone_id}", json={"name": f"{prefix}-clone2"})
            assert not_page.status_code == 404
            assert _detail(not_page) == NOT_MANAGEABLE
            system = client.patch(f"{ADMIN_PATH}/dashboard-drop-default", json={"status": "archived"})
            assert system.status_code == 409
            assert _detail(system) == SYSTEM_LIFECYCLE
            delete_clone = client.delete(f"{ADMIN_PATH}/{clone_id}", params={"load_case_id": LOAD_CASE_ID})
            assert delete_clone.status_code == 409
            assert _detail(delete_clone) == CUSTOM_DELETE_ONLY
            wrong_context = client.delete(f"{ADMIN_PATH}/{second_id}", params={"load_case_id": "loadcase-clamp-left-001"})
            assert wrong_context.status_code == 409
            assert _detail(wrong_context) == WRONG_LOAD_CASE

            duplicate_order = client.put(ORDER_PATH, json={"load_case_id": LOAD_CASE_ID, "page_ids": [second_id, second_id]})
            assert duplicate_order.status_code == 422
            assert _detail(duplicate_order) == DUPLICATE_ORDER
            mismatched_order = client.put(ORDER_PATH, json={"load_case_id": LOAD_CASE_ID, "page_ids": []})
            assert mismatched_order.status_code == 422
            assert _detail(mismatched_order) == ORDER_SET_MISMATCH
            reordered = client.put(ORDER_PATH, json={"load_case_id": LOAD_CASE_ID, "page_ids": [second_id]})
            assert reordered.status_code == 200, reordered.text
            assert next(item for item in reordered.json() if item["id"] == second_id)["page"]["display_order"] == 100

            stored = client.get(f"/api/dashboards/{second_id}").json()
            stored["widgets"] = [{"id": "kpi", "type": "kpi", "title": "KPI", "x": 0, "y": 0, "w": 3, "h": 2, "settings": {}}]
            saved = client.put(f"/api/dashboards/{second_id}", json=stored)
            assert saved.status_code == 200, saved.text
            assert saved.json()["version"] == 3
            published = client.patch(f"{ADMIN_PATH}/{second_id}", json={"status": "published"})
            assert published.status_code == 200, published.text
            assert client.get(PUBLIC_PATH, params={"load_case_id": LOAD_CASE_ID}).status_code == 200

            deleted = client.delete(f"{ADMIN_PATH}/{second_id}", params={"load_case_id": LOAD_CASE_ID})
            assert deleted.status_code == 200
            assert deleted.json() == {"status": "deleted", "id": second_id, "load_case_id": LOAD_CASE_ID}
            created_ids.remove(second_id)
            with connect() as conn:
                assert conn.execute("SELECT count(*) FROM dashboards WHERE id = ?", [second_id]).fetchone()[0] == 0
                assert conn.execute("SELECT count(*) FROM dashboard_versions WHERE dashboard_id = ?", [second_id]).fetchone()[0] == 0
    finally:
        _cleanup_dashboards(prefix)
        # The generated IDs do not carry the human-readable name prefix, so
        # delete only the test-owned ids, never a seeded system dashboard.
        with connect() as conn:
            for dashboard_id in created_ids:
                conn.execute("DELETE FROM dashboard_versions WHERE dashboard_id = ?", [dashboard_id])
                conn.execute("DELETE FROM dashboards WHERE id = ?", [dashboard_id])
