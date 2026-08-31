from __future__ import annotations

import inspect
import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.adapters.http.routers import workspace_layouts
from app.adapters.persistence import workspace_layouts as workspace_layouts_persistence
from app.database_connection import connect
from app.main import app


def _route_slice() -> list[APIRoute]:
    return [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.endpoint.__module__ == workspace_layouts.__name__
    ]


@pytest.mark.contract
def test_workspace_layout_router_owns_exact_five_routes_in_legacy_order() -> None:
    routes = _route_slice()

    assert [(route.path, tuple(sorted(route.methods or ()))) for route in routes] == [
        ("/api/projects/{project_id}/workspace-layouts/{layout_kind}", ("GET",)),
        ("/api/workspace-layouts/{layout_kind}", ("GET",)),
        ("/api/projects/{project_id}/workspace-layouts/{layout_kind}", ("PUT",)),
        ("/api/workspace-layouts/{layout_kind}", ("PUT",)),
        ("/api/projects/{project_id}/workspace-layouts/{layout_kind}/versions", ("GET",)),
    ]
    assert [route.endpoint.__name__ for route in routes] == [
        "get_project_workspace_layout",
        "get_workspace_layout",
        "save_project_workspace_layout",
        "save_workspace_layout",
        "get_workspace_layout_versions",
    ]
    assert [route.operation_id or route.unique_id for route in routes] == [
        "get_project_workspace_layout_api_projects__project_id__workspace_layouts__layout_kind__get",
        "get_workspace_layout_api_workspace_layouts__layout_kind__get",
        "save_project_workspace_layout_api_projects__project_id__workspace_layouts__layout_kind__put",
        "save_workspace_layout_api_workspace_layouts__layout_kind__put",
        "get_workspace_layout_versions_api_projects__project_id__workspace_layouts__layout_kind__versions_get",
    ]

    assert [route.response_model for route in routes] == [
        workspace_layouts.WorkspaceLayoutResponse,
        workspace_layouts.WorkspaceLayoutResponse,
        workspace_layouts.WorkspaceLayoutResponse,
        workspace_layouts.WorkspaceLayoutResponse,
        list[workspace_layouts.WorkspaceLayoutVersionResponse],
    ]
    assert [route.deprecated for route in routes] == [None, True, None, True, None]

    openapi_paths = app.openapi()["paths"]
    assert openapi_paths["/api/workspace-layouts/{layout_kind}"]["get"]["deprecated"] is True
    assert openapi_paths["/api/workspace-layouts/{layout_kind}"]["put"]["deprecated"] is True
    assert "deprecated" not in openapi_paths["/api/projects/{project_id}/workspace-layouts/{layout_kind}"]["get"]
    assert "deprecated" not in openapi_paths["/api/projects/{project_id}/workspace-layouts/{layout_kind}"]["put"]

    alias_get, alias_put = routes[1], routes[3]
    for route in (alias_get, alias_put):
        project_parameter = inspect.signature(route.endpoint).parameters["project_id"]
        assert project_parameter.default is not inspect.Parameter.empty
        operation = app.openapi()["paths"][route.path]["get" if route is alias_get else "put"]
        assert any(
            parameter["name"] == "project_id" and parameter["in"] == "query"
            for parameter in operation["parameters"]
        )


@pytest.mark.contract
def test_main_relinquishes_workspace_layout_helpers_and_sql_ownership() -> None:
    source = (Path(__file__).parents[1] / "app" / "main.py").read_text(encoding="utf-8")
    forbidden = (
        "WORKSPACE_LAYOUT_KINDS",
        "_validated_workspace_layout",
        "_get_project_workspace_layout",
        "_save_project_workspace_layout",
        "def get_project_workspace_layout",
        "def get_workspace_layout",
        "def save_project_workspace_layout",
        "def save_workspace_layout",
        "def get_workspace_layout_versions",
        "SELECT project_id, layout_kind, version, definition_json, updated_by, updated_at",
        "FROM project_workspace_layouts",
        "UPDATE project_workspace_layouts",
        "INSERT INTO project_workspace_layout_versions",
        "FROM project_workspace_layout_versions",
    )
    for token in forbidden:
        assert token not in source
    assert "app.include_router(workspace_layouts_router)" in source


def _seed_project_layout(
    *,
    kind: str = "portfolio",
    definition: dict[str, Any] | None = None,
) -> Iterator[tuple[str, dict[str, Any]]]:
    project_id = f"workspace-layout-test-{uuid4().hex[:12]}"
    now = datetime(2026, 1, 2, 3, 4, 5)
    initial = definition or (
        {"fontSize": 10, "chartOrder": ["trend", "status", "quality", "type"]}
        if kind == "portfolio"
        else {"fontSize": 10, "accentColor": "#50d5ff", "items": []}
    )
    with connect() as connection:
        connection.execute(
            "INSERT INTO projects VALUES (?, ?, ?, ?, ?)",
            [project_id, "Workspace layout test", "Test product", "", now],
        )
        encoded = json.dumps(initial, ensure_ascii=False)
        connection.execute(
            "INSERT INTO project_workspace_layouts VALUES (?, ?, 1, ?, 'fixture', ?)",
            [project_id, kind, encoded, now],
        )
        connection.execute(
            "INSERT INTO project_workspace_layout_versions VALUES (?, ?, 1, ?, 'fixture', ?, true)",
            [project_id, kind, encoded, now],
        )
    try:
        yield project_id, initial
    finally:
        with connect() as connection:
            connection.execute(
                "DELETE FROM audit_events WHERE action='PROJECT_WORKSPACE_LAYOUT_UPDATED' "
                "AND json_extract_string(detail_json, '$.project_id')=?",
                [project_id],
            )
            connection.execute("DELETE FROM project_workspace_layout_versions WHERE project_id=?", [project_id])
            connection.execute("DELETE FROM project_workspace_layouts WHERE project_id=?", [project_id])
            connection.execute("DELETE FROM projects WHERE id=?", [project_id])


@contextmanager
def project_layout(**kwargs: Any) -> Iterator[tuple[str, dict[str, Any]]]:
    yield from _seed_project_layout(**kwargs)


@pytest.mark.duckdb_integration
def test_definition_validation_messages_and_normalization_are_preserved() -> None:
    with project_layout() as (project_id, _):
        with TestClient(app) as client:
            portfolio = client.put(
                f"/api/projects/{project_id}/workspace-layouts/portfolio",
                json={
                    "definition": {
                        "fontSize": 12,
                        "chartOrder": ["type", "quality", "status", "trend"],
                        "clientExtension": {"must": "be removed"},
                    },
                    "updated_by": "payload actor",
                },
            )
            assert portfolio.status_code == 200, portfolio.text
            assert portfolio.json()["definition"] == {
                "fontSize": 12,
                "chartOrder": ["type", "quality", "status", "trend"],
            }

        with project_layout(
            kind="workflow",
            definition={"fontSize": 10, "accentColor": "#50d5ff", "items": []},
        ) as (workflow_id, _):
            with TestClient(app) as client:
                workflow = client.put(
                    f"/api/projects/{workflow_id}/workspace-layouts/workflow",
                    json={
                        "definition": {
                            "fontSize": 11,
                            "accentColor": "#AABBCC",
                            "items": [
                                {"requestId": "request-a", "x": 1, "y": 2, "w": 3, "h": 4, "label": "drop"},
                            ],
                            "clientExtension": True,
                        }
                    },
                )
                assert workflow.status_code == 200, workflow.text
                assert workflow.json()["definition"] == {
                    "fontSize": 11,
                    "accentColor": "#AABBCC",
                    "items": [{"requestId": "request-a", "x": 1, "y": 2, "w": 3, "h": 4}],
                }


@pytest.mark.duckdb_integration
@pytest.mark.parametrize(
    ("kind", "definition", "detail"),
    [
        ("portfolio", {"fontSize": 7, "chartOrder": ["trend", "status", "quality", "type"]}, "레이아웃 글자 크기는 8~18 사이의 정수여야 합니다."),
        ("portfolio", {"fontSize": 10, "chartOrder": ["trend", "status"]}, "운영 대시보드 차트 순서가 올바르지 않습니다."),
        ("workflow", {"fontSize": 10, "accentColor": "50d5ff", "items": []}, "워크플로 강조 색상은 #을 포함한 6자리 HEX여야 합니다."),
        ("workflow", {"fontSize": 10, "accentColor": "#50d5ff", "items": [{"x": 0, "y": 0, "w": 1, "h": 1}]}, "워크플로 레이아웃 항목에는 requestId가 필요합니다."),
        ("workflow", {"fontSize": 10, "accentColor": "#50d5ff", "items": [{"requestId": "request-a", "x": 0, "y": 0, "w": 1.5, "h": 1}]}, "워크플로 레이아웃 위치와 크기는 정수여야 합니다."),
    ],
)
def test_layout_validation_error_messages_are_exact(kind: str, definition: dict[str, Any], detail: str) -> None:
    with project_layout(kind=kind) as (project_id, _):
        with TestClient(app) as client:
            response = client.put(
                f"/api/projects/{project_id}/workspace-layouts/{kind}",
                json={"definition": definition},
            )
    assert response.status_code == 422
    assert response.json() == {"detail": detail}


@pytest.mark.duckdb_integration
def test_unsupported_kind_uses_fastapi_literal_validation_and_alias_query_is_required() -> None:
    with TestClient(app) as client:
        unsupported = client.get("/api/projects/project-tv-001/workspace-layouts/unsupported")
        missing_query = client.get("/api/workspace-layouts/portfolio")
    assert unsupported.status_code == 422
    assert unsupported.json()["detail"][0]["type"] == "literal_error"
    assert unsupported.json()["detail"][0]["loc"] == ["path", "layout_kind"]
    assert missing_query.status_code == 422
    assert missing_query.json()["detail"][0]["loc"] == ["query", "project_id"]


@pytest.mark.duckdb_integration
def test_missing_project_and_missing_layout_404_contracts() -> None:
    with TestClient(app) as client:
        for method, path, body in (
            ("get", "/api/projects/missing-workspace-project/workspace-layouts/portfolio", None),
            ("get", "/api/projects/missing-workspace-project/workspace-layouts/portfolio/versions", None),
            ("put", "/api/projects/missing-workspace-project/workspace-layouts/portfolio", {"definition": {"fontSize": 10, "chartOrder": ["trend", "status", "quality", "type"]}}),
        ):
            response = getattr(client, method)(path, json=body) if body is not None else getattr(client, method)(path)
            assert response.status_code == 404, path
            assert response.json() == {"detail": "프로젝트를 찾을 수 없습니다."}, path

    with project_layout() as (project_id, _):
        with TestClient(app) as client:
            # Delete after lifespan bootstrap; startup repairs missing project
            # defaults, so deleting before opening TestClient would be healed.
            with connect() as connection:
                connection.execute(
                    "DELETE FROM project_workspace_layouts WHERE project_id=? AND layout_kind='portfolio'",
                    [project_id],
                )
                connection.execute(
                    "DELETE FROM project_workspace_layout_versions WHERE project_id=? AND layout_kind='portfolio'",
                    [project_id],
                )
            for method, path, body in (
                ("get", f"/api/projects/{project_id}/workspace-layouts/portfolio", None),
                ("put", f"/api/projects/{project_id}/workspace-layouts/portfolio", {"definition": {"fontSize": 10, "chartOrder": ["trend", "status", "quality", "type"]}}),
            ):
                response = getattr(client, method)(path, json=body) if body is not None else getattr(client, method)(path)
                assert response.status_code == 404, path
                assert response.json() == {"detail": "저장된 레이아웃이 없습니다."}, path
            versions = client.get(f"/api/projects/{project_id}/workspace-layouts/portfolio/versions")
            assert versions.status_code == 200
            assert versions.json() == []

    with project_layout() as (project_id, _):
        with TestClient(app) as client:
            # Historical versions remain listable even when the live snapshot
            # has been removed; close the writer before issuing HTTP requests.
            with connect() as connection:
                connection.execute(
                    "DELETE FROM project_workspace_layouts WHERE project_id=? AND layout_kind='portfolio'",
                    [project_id],
                )
            versions = client.get(f"/api/projects/{project_id}/workspace-layouts/portfolio/versions")
            assert versions.status_code == 200
            assert [item["version"] for item in versions.json()] == [1]
            assert versions.json()[0]["created_by"] == "fixture"


@pytest.mark.duckdb_integration
def test_data_view_and_layout_edit_authorization_receive_the_open_connection_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, object, object]] = []
    connections: list[object] = []

    class TrackedConnection:
        def __init__(self, raw: Any) -> None:
            self.raw = raw

        def execute(self, statement: str, parameters: Any | None = None) -> Any:
            events.append(("sql", self, statement))
            return self.raw.execute(statement) if parameters is None else self.raw.execute(statement, parameters)

        def __getattr__(self, name: str) -> Any:
            return getattr(self.raw, name)

    @contextmanager
    def tracked_connect() -> Iterator[TrackedConnection]:
        with connect() as raw:
            connection = TrackedConnection(raw)
            connections.append(connection)
            yield connection

    def authorize(_request: Any, permission: str, _project_id: str, *, conn: Any = None) -> None:
        events.append(("authorize", permission, conn))
        assert conn in connections

    real_provider = workspace_layouts.SQLWorkspaceLayoutRepositoryProvider

    def tracked_provider(*, authorize_read: Any = None, authorize_write: Any = None) -> Any:
        return real_provider(
            lambda: tracked_connect(),
            authorize_read=authorize_read,
            authorize_write=authorize_write,
        )

    monkeypatch.setattr(workspace_layouts, "SQLWorkspaceLayoutRepositoryProvider", tracked_provider)
    monkeypatch.setattr(workspace_layouts, "require_permission", authorize)
    with project_layout() as (project_id, _):
        with TestClient(app) as client:
            read = client.get(f"/api/projects/{project_id}/workspace-layouts/portfolio")
            write = client.put(
                f"/api/projects/{project_id}/workspace-layouts/portfolio",
                json={"definition": {"fontSize": 11, "chartOrder": ["trend", "status", "quality", "type"]}},
            )
    assert read.status_code == 200
    assert write.status_code == 200
    permissions = [item[1] for item in events if item[0] == "authorize"]
    assert permissions == [workspace_layouts.PROJECT_DATA_VIEW, workspace_layouts.PROJECT_LAYOUT_EDIT]
    assert events[0][0] == "sql"
    for index, event in enumerate(events):
        if event[0] != "authorize":
            continue
        assert index > 0
        assert events[index - 1][0] == "sql"
        assert events[index - 1][1] is event[2]
        assert index + 1 < len(events)
        assert events[index + 1][0] == "sql"
        assert events[index + 1][1] is event[2]
    assert all(item[1] in connections for item in events if item[0] == "sql")


@pytest.mark.unit
def test_persistence_transaction_rolls_back_and_preserves_custom_base_exception() -> None:
    events: list[str] = []

    class FakeConnection:
        def execute(self, statement: str, _parameters: Any | None = None) -> None:
            events.append(statement.strip().upper())

    repository = workspace_layouts_persistence.SQLWorkspaceLayoutRepository(FakeConnection())

    class SentinelFailure(BaseException):
        pass

    failure = SentinelFailure()
    with pytest.raises(SentinelFailure) as caught:
        with repository.transaction():
            raise failure

    assert caught.value is failure
    assert events == ["BEGIN TRANSACTION", "ROLLBACK"]


@pytest.mark.duckdb_integration
def test_aliases_have_exact_response_parity_and_payload_actor_is_ignored() -> None:
    with project_layout() as (project_id, original):
        with TestClient(app) as client:
            canonical = client.get(f"/api/projects/{project_id}/workspace-layouts/portfolio")
            alias = client.get("/api/workspace-layouts/portfolio", params={"project_id": project_id})
            assert canonical.status_code == alias.status_code == 200
            assert canonical.json() == alias.json()

            changed = {"fontSize": 12, "chartOrder": ["type", "quality", "status", "trend"]}
            saved = client.put(
                "/api/workspace-layouts/portfolio",
                params={"project_id": project_id},
                json={"definition": changed, "updated_by": "forged actor"},
            )
            assert saved.status_code == 200, saved.text
            assert saved.json()["definition"] == changed
            assert saved.json()["updated_by"] == "로컬 관리자"

            canonical_after = client.get(f"/api/projects/{project_id}/workspace-layouts/portfolio")
            assert canonical_after.json() == saved.json()
            restored = client.put(
                f"/api/projects/{project_id}/workspace-layouts/portfolio",
                json={"definition": original, "updated_by": "forged restore"},
            )
            assert restored.status_code == 200
            assert restored.json()["updated_by"] == "로컬 관리자"


@pytest.mark.duckdb_integration
def test_successful_save_writes_complete_audit_identity_and_request_metadata() -> None:
    with project_layout() as (project_id, _):
        with TestClient(app) as client:
            response = client.put(
                f"/api/projects/{project_id}/workspace-layouts/portfolio",
                headers={"user-agent": "workspace-layout-contract-test"},
                json={
                    "definition": {
                        "fontSize": 13,
                        "chartOrder": ["trend", "status", "quality", "type"],
                    },
                    "updated_by": "forged actor",
                },
            )
            assert response.status_code == 200, response.text

        with connect() as connection:
            audit = connection.execute(
                """
                SELECT user_id, username, role, action, method, path, status_code,
                       request_id, client_ip, user_agent, detail_json, occurred_at
                FROM audit_events
                WHERE action='PROJECT_WORKSPACE_LAYOUT_UPDATED'
                  AND json_extract_string(detail_json, '$.project_id')=?
                ORDER BY occurred_at DESC
                LIMIT 1
                """,
                [project_id],
            ).fetchone()

    assert audit is not None
    assert audit[0:7] == (
        "local-admin",
        "local",
        "admin",
        "PROJECT_WORKSPACE_LAYOUT_UPDATED",
        "PUT",
        f"/api/projects/{project_id}/workspace-layouts/portfolio",
        200,
    )
    assert isinstance(audit[7], str) and audit[7]
    assert isinstance(audit[8], str) and audit[8]
    assert audit[9] == "workspace-layout-contract-test"
    assert json.loads(audit[10]) == {
        "project_id": project_id,
        "layout_kind": "portfolio",
        "version": 2,
    }
    assert isinstance(audit[11], datetime)
    assert audit[11].tzinfo is None


@pytest.mark.duckdb_integration
def test_live_update_version_append_and_audit_are_atomic_on_audit_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with project_layout() as (project_id, original):
        with connect() as connection:
            before_live = connection.execute(
                "SELECT version, definition_json, updated_by FROM project_workspace_layouts WHERE project_id=? AND layout_kind='portfolio'",
                [project_id],
            ).fetchone()
            before_versions = connection.execute(
                "SELECT version, definition_json, created_by, is_valid FROM project_workspace_layout_versions WHERE project_id=? AND layout_kind='portfolio' ORDER BY version",
                [project_id],
            ).fetchall()
            before_audits = connection.execute(
                "SELECT action, status_code, detail_json FROM audit_events WHERE action='PROJECT_WORKSPACE_LAYOUT_UPDATED' AND json_extract_string(detail_json, '$.project_id')=?",
                [project_id],
            ).fetchall()

        def fail_audit(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("workspace audit failed")

        monkeypatch.setattr(workspace_layouts_persistence.SQLWorkspaceLayoutRepository, "add_audit", fail_audit)
        with TestClient(app, raise_server_exceptions=True) as client:
            with pytest.raises(RuntimeError, match="workspace audit failed"):
                client.put(
                    f"/api/projects/{project_id}/workspace-layouts/portfolio",
                    json={
                        "definition": {"fontSize": 15, "chartOrder": ["quality", "trend", "type", "status"]},
                        "updated_by": "forged actor",
                    },
                )

        with connect() as connection:
            after_live = connection.execute(
                "SELECT version, definition_json, updated_by FROM project_workspace_layouts WHERE project_id=? AND layout_kind='portfolio'",
                [project_id],
            ).fetchone()
            after_versions = connection.execute(
                "SELECT version, definition_json, created_by, is_valid FROM project_workspace_layout_versions WHERE project_id=? AND layout_kind='portfolio' ORDER BY version",
                [project_id],
            ).fetchall()
            after_audits = connection.execute(
                "SELECT action, status_code, detail_json FROM audit_events WHERE action='PROJECT_WORKSPACE_LAYOUT_UPDATED' AND json_extract_string(detail_json, '$.project_id')=?",
                [project_id],
            ).fetchall()
    assert after_live == before_live
    assert after_versions == before_versions
    assert after_audits == before_audits


@pytest.mark.duckdb_integration
def test_versions_are_descending_and_include_each_successful_live_update() -> None:
    with project_layout() as (project_id, _):
        with TestClient(app) as client:
            for font_size in (11, 12):
                response = client.put(
                    f"/api/projects/{project_id}/workspace-layouts/portfolio",
                    json={"definition": {"fontSize": font_size, "chartOrder": ["trend", "status", "quality", "type"]}},
                )
                assert response.status_code == 200
        with connect() as connection:
            stored = connection.execute(
                "SELECT version, created_by, is_valid FROM project_workspace_layout_versions WHERE project_id=? AND layout_kind='portfolio' ORDER BY version DESC",
                [project_id],
            ).fetchall()
        with TestClient(app) as client:
            response = client.get(f"/api/projects/{project_id}/workspace-layouts/portfolio/versions")
        assert response.status_code == 200
        assert [item["version"] for item in response.json()] == [3, 2, 1]
        assert [(row[0], row[1], bool(row[2])) for row in stored] == [
            (3, "로컬 관리자", True),
            (2, "로컬 관리자", True),
            (1, "fixture", True),
        ]
