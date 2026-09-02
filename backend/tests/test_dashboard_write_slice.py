from __future__ import annotations

import ast
import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, get_type_hints
from uuid import uuid4

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import app.main as main_module
from app.database import initialize_database
from app.database_connection import connect
from app.main import app


def _dashboard_definition(
    dashboard_id: str,
    *,
    name: str = "쓰기 경계 대시보드",
    description: str = "원본 설명",
    widgets: list[dict[str, object]] | None = None,
    page: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "id": dashboard_id,
        "name": name,
        "description": description,
        "widgets": widgets or [],
        **({"page": page} if page is not None else {}),
    }


class _CommandFake:
    def __init__(
        self,
        events: list[object],
        *,
        dashboard: tuple[object, object] | None = None,
        version_row: tuple[object] | None = (True,),
        valid_history_count: int = 2,
        clone_source: tuple[object, ...] | None = None,
        restore_source: tuple[object] | None = None,
    ) -> None:
        self.events = events
        self.dashboard = dashboard
        self.version_row = version_row
        self.valid_history_count = valid_history_count
        self.clone_source = clone_source
        self.restore_source = restore_source

    def authorize_resource(self, callback: Any) -> None:
        self.events.append("auth")
        callback("same-connection")

    def get_dashboard(self, dashboard_id: str):
        self.events.append(("dashboard", dashboard_id))
        return self.dashboard

    def write_dashboard_definition(self, dashboard_id: str, definition: dict[str, object], created_by: str):
        self.events.append(("write", dashboard_id, definition, created_by))
        return 8, datetime(2026, 9, 1, 1, 2, 3)

    def get_dashboard_version_for_delete(self, dashboard_id: str, version: int):
        self.events.append(("version", dashboard_id, version))
        return self.version_row

    def count_valid_history(self, dashboard_id: str, current_version: int) -> int:
        self.events.append(("history-count", dashboard_id, current_version))
        return self.valid_history_count

    def invalidate_dashboard_version(self, dashboard_id: str, version: int) -> None:
        self.events.append(("invalidate", dashboard_id, version))

    def get_clone_source(self, dashboard_id: str):
        self.events.append(("clone-source", dashboard_id))
        return self.clone_source

    def insert_cloned_dashboard(self, *args: object) -> None:
        self.events.append(("clone-insert", *args))

    def get_valid_dashboard_version(self, dashboard_id: str, version: int):
        self.events.append(("restore-source", dashboard_id, version))
        return self.restore_source


def _provider(repository: _CommandFake, events: list[object]):
    @contextmanager
    def provide() -> Iterator[_CommandFake]:
        events.append("open")
        try:
            yield repository
        finally:
            events.append("close")

    return provide


SAVE_PATH = "/api/dashboards/{dashboard_id}"
VERSION_PATH = "/api/dashboards/{dashboard_id}/versions/{version}"
CLONE_PATH = "/api/dashboards/{dashboard_id}/clone"
RESTORE_PATH = "/api/dashboards/{dashboard_id}/restore/{version}"
PREVIEW_PATH = "/api/dashboard-commands/preview"


def _route(routes: list[APIRoute], path: str, method: str) -> APIRoute:
    return next(route for route in routes if route.path == path and method in (route.methods or set()))


@pytest.mark.contract
def test_dashboard_write_routes_keep_legacy_contract_owner_openapi_and_global_interleaving() -> None:
    """Dashboard writes remain interleaved with reads, but main owns no handler."""
    from app.adapters.http.routers import dashboard_writes as dashboard_writes_router

    routes = [route for route in app.routes if isinstance(route, APIRoute)]
    save = _route(routes, SAVE_PATH, "PUT")
    delete_version = _route(routes, VERSION_PATH, "DELETE")
    clone = _route(routes, CLONE_PATH, "POST")
    restore = _route(routes, RESTORE_PATH, "POST")
    writes = [save, delete_version, clone, restore]

    assert [(route.endpoint.__module__, route.endpoint.__name__) for route in writes] == [
        (dashboard_writes_router.__name__, "save_dashboard"),
        (dashboard_writes_router.__name__, "delete_dashboard_version"),
        (dashboard_writes_router.__name__, "clone_dashboard"),
        (dashboard_writes_router.__name__, "restore_dashboard"),
    ]
    assert [get_type_hints(route.endpoint)["return"] for route in writes] == [dict[str, Any]] * 4
    assert [route.response_model for route in writes] == [dict[str, Any]] * 4
    assert [route.status_code for route in writes] == [None, None, 201, None]
    operation_ids = [route.operation_id or route.unique_id for route in writes]
    assert operation_ids == [
        "save_dashboard_api_dashboards__dashboard_id__put",
        "delete_dashboard_version_api_dashboards__dashboard_id__versions__version__delete",
        "clone_dashboard_api_dashboards__dashboard_id__clone_post",
        "restore_dashboard_api_dashboards__dashboard_id__restore__version__post",
    ]

    schema = app.openapi()
    expected = {
        (SAVE_PATH, "put"): operation_ids[0],
        (VERSION_PATH, "delete"): operation_ids[1],
        (CLONE_PATH, "post"): operation_ids[2],
        (RESTORE_PATH, "post"): operation_ids[3],
    }
    assert {key: schema["paths"][key[0]][key[1]]["operationId"] for key in expected} == expected
    assert set(schema["paths"][SAVE_PATH]["put"]["responses"]) == {"200", "422"}
    assert set(schema["paths"][VERSION_PATH]["delete"]["responses"]) == {"200", "422"}
    assert set(schema["paths"][CLONE_PATH]["post"]["responses"]) == {"201", "422"}
    assert set(schema["paths"][RESTORE_PATH]["post"]["responses"]) == {"200", "422"}

    detail_get = _route(routes, SAVE_PATH, "GET")
    list_get = _route(routes, "/api/dashboards", "GET")
    versions_get = _route(routes, "/api/dashboards/{dashboard_id}/versions", "GET")
    version_get = _route(routes, VERSION_PATH, "GET")
    preview = _route(routes, PREVIEW_PATH, "POST")
    assert [routes.index(route) for route in (
        detail_get,
        list_get,
        save,
        versions_get,
        version_get,
        delete_version,
        clone,
        restore,
        preview,
    )] == sorted(
        routes.index(route)
        for route in (
            detail_get,
            list_get,
            save,
            versions_get,
            version_get,
            delete_version,
            clone,
            restore,
            preview,
        )
    )


@pytest.mark.unit
def test_main_relinquishes_dashboard_write_handlers_and_write_routers_stay_side_effect_free() -> None:
    main_path = Path(main_module.__file__)
    source = main_path.read_text(encoding="utf-8")
    for handler in ("save_dashboard", "delete_dashboard_version", "clone_dashboard", "restore_dashboard"):
        assert f"def {handler}(" not in source
    assert "def _write_dashboard_definition(" not in source
    assert "dashboard_save_router" in source
    assert "dashboard_history_router" in source

    actual = sum(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
        for node in ast.walk(ast.parse(source))
    )
    baseline = json.loads((main_path.parents[1] / "scripts" / "architecture_baseline.json").read_text(encoding="utf-8"))
    assert actual <= 43
    assert baseline["execute_call_ceilings"]["app/main.py"] == actual

    router_path = main_path.parent / "adapters" / "http" / "routers" / "dashboard_writes.py"
    router_source = router_path.read_text(encoding="utf-8")
    assert ".execute(" not in router_source
    assert "write_audit_event" not in router_source
    assert all(marker not in router_source for marker in ("BEGIN", "COMMIT", "ROLLBACK"))


@pytest.mark.unit
def test_dashboard_write_commands_keep_legacy_preflight_authorization_and_history_order() -> None:
    from app.application.dashboard_writes import commands
    from app.domains.dashboard_writes.errors import (
        AnalysisPageUserEditError,
        DashboardIdMismatchError,
        SystemDashboardVersionError,
    )

    mismatch_events: list[object] = []
    with pytest.raises(DashboardIdMismatchError):
        commands.save_dashboard(
            dashboard_id="dashboard-path",
            definition=_dashboard_definition("dashboard-body"),
            authorize=lambda *_args: None,
            repository_provider=_provider(_CommandFake(mismatch_events), mismatch_events),
        )
    assert mismatch_events == []

    dashboard_id = "dashboard-save"
    events: list[object] = []
    original = _dashboard_definition(dashboard_id)
    result = commands.save_dashboard(
        dashboard_id=dashboard_id,
        definition=original,
        authorize=lambda received_id, connection: events.append(("authorizer", received_id, connection)),
        repository_provider=_provider(_CommandFake(events, dashboard=(4, original)), events),
    )
    assert result == {
        "status": "saved",
        "version": 8,
        "updated_at": datetime(2026, 9, 1, 1, 2, 3),
    }
    assert [event if isinstance(event, str) else event[0] for event in events] == [
        "open",
        "auth",
        "authorizer",
        "dashboard",
        "write",
        "close",
    ]
    assert events[2] == ("authorizer", dashboard_id, "same-connection")
    assert events[4][1:] == (dashboard_id, original, "대시보드 사용자")

    page = {
        "kind": "analysis_page",
        "analysis_key": "custom",
        "status": "draft",
        "display_order": 100,
        "is_system": False,
    }
    immutable_events: list[object] = []
    with pytest.raises(AnalysisPageUserEditError):
        commands.save_dashboard(
            dashboard_id=dashboard_id,
            definition=_dashboard_definition(dashboard_id, name="다른 이름", page=page),
            authorize=lambda *_args: None,
            repository_provider=_provider(
                _CommandFake(immutable_events, dashboard=(2, _dashboard_definition(dashboard_id, page=page))),
                immutable_events,
            ),
        )
    assert [event if isinstance(event, str) else event[0] for event in immutable_events] == [
        "open", "auth", "dashboard", "close"
    ]

    system_events: list[object] = []
    with pytest.raises(SystemDashboardVersionError):
        commands.delete_dashboard_version(
            dashboard_id="dashboard-drop-default",
            version=1,
            authorize=lambda *_args: None,
            repository_provider=_provider(
                _CommandFake(system_events, dashboard=(3, _dashboard_definition("dashboard-drop-default"))),
                system_events,
            ),
        )
    # System v1 is rejected before the version-row lookup, as the legacy handler did.
    assert [event if isinstance(event, str) else event[0] for event in system_events] == [
        "open", "auth", "dashboard", "close"
    ]

    delete_events: list[object] = []
    assert commands.delete_dashboard_version(
        dashboard_id=dashboard_id,
        version=2,
        authorize=lambda received_id, connection: delete_events.append(("authorizer", received_id, connection)),
        repository_provider=_provider(
            _CommandFake(delete_events, dashboard=(4, _dashboard_definition(dashboard_id))), delete_events
        ),
    ) == {"status": "invalidated", "dashboard_id": dashboard_id, "version": 2}
    assert [event if isinstance(event, str) else event[0] for event in delete_events] == [
        "open", "auth", "authorizer", "dashboard", "version", "history-count", "invalidate", "close"
    ]


@pytest.mark.unit
def test_dashboard_clone_and_restore_commands_keep_legacy_identity_and_read_order() -> None:
    from app.application.dashboard_writes import commands
    from app.domains.dashboard_writes.errors import PublishedEmptyRestoreError, RestorableVersionNotFoundError

    dashboard_id = "dashboard-source"
    source_definition = _dashboard_definition(
        dashboard_id,
        page={
            "kind": "analysis_page",
            "analysis_key": "custom",
            "status": "draft",
            "display_order": 100,
            "is_system": False,
        },
    )
    clone_events: list[object] = []
    assert commands.clone_dashboard(
        dashboard_id=dashboard_id,
        name="  복제 이름  ",
        description="  복제 설명  ",
        principal_user_id="request-principal",
        authorize=lambda received_id, connection: clone_events.append(("authorizer", received_id, connection)),
        repository_provider=_provider(
            _CommandFake(
                clone_events,
                clone_source=("project-1", "request-1", "loadcase-1", source_definition),
            ),
            clone_events,
        ),
        identifier_factory=lambda: clone_events.append("identifier") or "fixed",
        clock=lambda: clone_events.append("clock") or datetime(2026, 9, 1, 4, 5, 6),
    ) == {"id": "dashboard-fixed", "version": 1, "status": "cloned"}
    assert [event if isinstance(event, str) else event[0] for event in clone_events] == [
        "identifier", "clock", "open", "auth", "authorizer", "clone-source", "clone-insert", "close"
    ]
    inserted = clone_events[6]
    assert inserted[1:5] == ("dashboard-fixed", "project-1", "request-1", "loadcase-1")
    assert inserted[5]["id"] == "dashboard-fixed"
    assert inserted[5]["name"] == "복제 이름"
    assert inserted[5]["description"] == "복제 설명"
    assert "page" not in inserted[5]
    assert inserted[6:] == ("request-principal", datetime(2026, 9, 1, 4, 5, 6))

    missing_events: list[object] = []
    with pytest.raises(RestorableVersionNotFoundError):
        commands.restore_dashboard(
            dashboard_id=dashboard_id,
            version=6,
            authorize=lambda *_args: None,
            repository_provider=_provider(
                _CommandFake(missing_events, dashboard=(4, _dashboard_definition(dashboard_id)), restore_source=None),
                missing_events,
            ),
            validate_definition=lambda definition: definition,
        )
    # A missing version is deliberately combined with the dashboard read into one 404.
    assert [event if isinstance(event, str) else event[0] for event in missing_events] == [
        "open", "auth", "restore-source", "dashboard", "close"
    ]

    page = {
        "kind": "analysis_page",
        "analysis_key": "custom",
        "status": "published",
        "display_order": 107,
        "is_system": False,
    }
    published_events: list[object] = []
    with pytest.raises(PublishedEmptyRestoreError):
        commands.restore_dashboard(
            dashboard_id=dashboard_id,
            version=3,
            authorize=lambda *_args: None,
            repository_provider=_provider(
                _CommandFake(
                    published_events,
                    dashboard=(4, _dashboard_definition(dashboard_id, name="현재 이름", description="현재 설명", page=page)),
                    restore_source=(_dashboard_definition("old-id", widgets=[]),),
                ),
                published_events,
            ),
            validate_definition=lambda definition: definition,
        )
    assert [event if isinstance(event, str) else event[0] for event in published_events] == [
        "open", "auth", "restore-source", "dashboard", "close"
    ]

    restore_events: list[object] = []
    restored = commands.restore_dashboard(
        dashboard_id=dashboard_id,
        version=3,
        authorize=lambda *_args: None,
        repository_provider=_provider(
            _CommandFake(
                restore_events,
                dashboard=(4, _dashboard_definition(dashboard_id, name="현재 이름", description="현재 설명", page=page)),
                restore_source=(_dashboard_definition("old-id", name="오래된 이름", description="오래된 설명", widgets=[{"id": "kpi", "type": "kpi", "title": "KPI", "x": 0, "y": 0, "w": 3, "h": 2, "settings": {}}]),),
            ),
            restore_events,
        ),
        validate_definition=lambda definition: restore_events.append("validate") or definition,
    )
    assert restored == {"status": "restored", "version": 8, "restored_from": 3}
    written = next(event for event in restore_events if isinstance(event, tuple) and event[0] == "write")
    assert written[1] == dashboard_id
    assert written[2]["id"] == dashboard_id
    assert written[2]["name"] == "현재 이름"
    assert written[2]["description"] == "현재 설명"
    assert written[2]["page"] == page
    assert written[3] == "복구 작업"


@pytest.mark.unit
def test_dashboard_write_sql_adapter_decodes_stored_definitions_and_keeps_history_maximum_monotonic() -> None:
    from app.adapters.persistence.dashboard_writes import SQLDashboardWriteRepository

    class Cursor:
        def __init__(self, one: tuple[object, ...] | None) -> None:
            self._one = one

        def fetchone(self):
            return self._one

    class Connection:
        backend = "postgresql"

        def __init__(self, cursors: list[Cursor]) -> None:
            self.cursors = cursors
            self.calls: list[tuple[str, object]] = []

        def execute(self, statement: str, parameters: object = None) -> Cursor:
            self.calls.append((" ".join(statement.split()), parameters))
            return self.cursors.pop(0)

    dashboard_id = "dashboard-write-sql"
    cloned_definition = _dashboard_definition(dashboard_id, name="원본")
    restored_definition = _dashboard_definition(dashboard_id, name="이력")
    connection = Connection(
        [
            Cursor(("project-1", "request-1", "loadcase-1", json.dumps(cloned_definition, ensure_ascii=False))),
            Cursor((json.dumps(restored_definition, ensure_ascii=False),)),
            # Even an invalid historical version participates in max(version)+1.
            Cursor((10,)),
            Cursor(None),
            Cursor(None),
        ]
    )
    repository = SQLDashboardWriteRepository(connection)

    assert repository.get_clone_source(dashboard_id) == ("project-1", "request-1", "loadcase-1", cloned_definition)
    assert repository.get_valid_dashboard_version(dashboard_id, 4) == (restored_definition,)
    version, occurred_at = repository.write_dashboard_definition(dashboard_id, restored_definition, "대시보드 사용자")
    assert version == 10 and isinstance(occurred_at, datetime)
    assert connection.calls[0] == (
        "SELECT project_id, request_id, load_case_id, definition_json FROM dashboards WHERE id = ?",
        [dashboard_id],
    )
    assert connection.calls[1] == (
        "SELECT definition_json FROM dashboard_versions WHERE dashboard_id = ? AND version = ? AND is_valid = true",
        [dashboard_id, 4],
    )
    assert connection.calls[2] == (
        "SELECT COALESCE(max(version), 0) + 1 FROM dashboard_versions WHERE dashboard_id = ?",
        [dashboard_id],
    )
    assert connection.calls[3][0] == "UPDATE dashboards SET name = ?, description = ?, version = ?, definition_json = ?, updated_at = ? WHERE id = ?"
    assert connection.calls[3][1][2] == 10
    assert connection.calls[4][0] == "INSERT INTO dashboard_versions VALUES (?, ?, ?, ?, ?, ?)"
    assert connection.calls[4][1][:4] == [dashboard_id, 10, json.dumps(restored_definition, ensure_ascii=False), "대시보드 사용자"]

    command_source = Path(__import__("app.application.dashboard_writes.commands", fromlist=["*"]).__file__).read_text(encoding="utf-8")
    port_source = Path(__import__("app.domains.dashboard_writes.ports", fromlist=["*"]).__file__).read_text(encoding="utf-8")
    adapter_source = Path(__import__("app.adapters.persistence.dashboard_writes", fromlist=["*"]).__file__).read_text(encoding="utf-8")
    assert all(marker not in command_source for marker in ("BEGIN", "COMMIT", "ROLLBACK"))
    assert all(method not in port_source for method in ("begin_transaction", "commit_transaction", "rollback_transaction"))
    assert all(marker not in adapter_source for marker in ("BEGIN", "COMMIT", "ROLLBACK"))


def _response_detail(response: Any) -> str:
    assert isinstance(response.json(), dict)
    return response.json()["detail"]


@pytest.mark.duckdb_integration
def test_dashboard_write_http_lifecycle_preserves_versions_page_safety_and_principal_identity() -> None:
    initialize_database()
    suffix = uuid4().hex[:10]
    clone_id: str | None = None
    page_id: str | None = None
    load_case_id = "loadcase-drop-bottom-001"
    try:
        with TestClient(app) as client:
            clone = client.post(
                "/api/dashboards/dashboard-drop-default/clone",
                json={
                    "name": f"write-slice-{suffix}",
                    "description": "명령 경계 검증",
                    "created_by": "위조된 생성자",
                },
            )
            assert clone.status_code == 201, clone.text
            clone_id = clone.json()["id"]
            with connect() as connection:
                created_by = connection.execute(
                    "SELECT created_by FROM dashboard_versions WHERE dashboard_id = ? AND version = 1",
                    [clone_id],
                ).fetchone()[0]
            assert created_by != "위조된 생성자"

            clone_definition = client.get(f"/api/dashboards/{clone_id}").json()
            clone_definition["description"] = "두 번째 정상 이력"
            saved = client.put(f"/api/dashboards/{clone_id}", json=clone_definition)
            assert saved.status_code == 200, saved.text
            assert saved.json()["version"] == 2
            current_for_invalid_history = client.get(f"/api/dashboards/{clone_id}").json()
            with connect() as connection:
                # Logical deletion never resets the monotonic history sequence.
                connection.execute(
                    "INSERT INTO dashboard_versions VALUES (?, 9, ?, 'invalid history', current_timestamp, false)",
                    [clone_id, json.dumps(current_for_invalid_history, ensure_ascii=False)],
                )
            clone_definition = client.get(f"/api/dashboards/{clone_id}").json()
            clone_definition["description"] = "무효 이력 뒤 저장"
            saved_after_invalid = client.put(f"/api/dashboards/{clone_id}", json=clone_definition)
            assert saved_after_invalid.status_code == 200, saved_after_invalid.text
            assert saved_after_invalid.json()["version"] == 10

            system_base = client.delete("/api/dashboards/dashboard-drop-default/versions/1")
            assert system_base.status_code == 409
            assert _response_detail(system_base) == "시스템 대시보드의 최초 기준 버전은 삭제할 수 없습니다."
            live = client.delete(f"/api/dashboards/{clone_id}/versions/10")
            assert live.status_code == 409
            assert _response_detail(live) == "현재 사용 중인 live 버전은 삭제할 수 없습니다."
            missing = client.delete(f"/api/dashboards/{clone_id}/versions/987")
            assert missing.status_code == 404
            assert _response_detail(missing) == "삭제할 수 있는 유효한 대시보드 버전을 찾을 수 없습니다."

            created_page = client.post(
                "/api/admin/dashboard-pages",
                json={"load_case_id": load_case_id, "name": f"restore-slice-{suffix}", "description": "빈 v1 보호"},
            )
            assert created_page.status_code == 201, created_page.text
            page_id = created_page.json()["id"]
            page_definition = client.get(f"/api/dashboards/{page_id}").json()
            page_definition["widgets"] = [
                {"id": "slice-kpi", "type": "kpi", "title": "KPI", "x": 0, "y": 0, "w": 3, "h": 2, "settings": {}}
            ]
            assert client.put(f"/api/dashboards/{page_id}", json=page_definition).status_code == 200
            published = client.patch(f"/api/admin/dashboard-pages/{page_id}", json={"status": "published"})
            assert published.status_code == 200, published.text
            unsafe_restore = client.post(f"/api/dashboards/{page_id}/restore/1")
            assert unsafe_restore.status_code == 422
            assert _response_detail(unsafe_restore) == "게시된 분석 페이지를 빈 위젯 버전으로 복구할 수 없습니다."
    finally:
        with connect() as connection:
            for dashboard_id in (page_id, clone_id):
                if dashboard_id:
                    connection.execute("DELETE FROM dashboard_versions WHERE dashboard_id = ?", [dashboard_id])
                    connection.execute("DELETE FROM dashboards WHERE id = ?", [dashboard_id])
