from __future__ import annotations

import ast
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, get_type_hints
from uuid import uuid4

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import database_settings
from app.database import initialize_database
from app.database_connection import connect
from app.main import app
from app.security import hash_password
from app.application.dashboard_reads import queries


DASHBOARD_PATHS = (
    "/api/dashboards/{dashboard_id}",
    "/api/dashboards",
    "/api/dashboards/{dashboard_id}/versions",
    "/api/dashboards/{dashboard_id}/versions/{version}",
)


def _insert_password_user(username: str, *, is_global_admin: bool) -> str:
    user_id = f"dashboard-read-user-{uuid4().hex[:12]}"
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
                user_id,
                username,
                hash_password("correct-horse-battery-staple"),
                username,
                "admin" if is_global_admin else "viewer",
                now,
                now,
                is_global_admin,
            ],
        )
    return user_id


def _dashboard_definition(dashboard_id: str, status: str) -> dict[str, Any]:
    return {
        "id": dashboard_id,
        "name": f"dashboard read {status}",
        "description": "read-boundary verification",
        "widgets": [],
        "page": {
            "kind": "analysis_page",
            "analysis_key": "custom",
            "status": status,
            "display_order": 100,
            "is_system": False,
        },
    }


@pytest.mark.contract
def test_dashboard_read_routes_keep_exact_contract_and_order() -> None:
    routes = [route for route in app.routes if isinstance(route, APIRoute)]
    selected = [
        route
        for route in routes
        if route.path in DASHBOARD_PATHS and "GET" in (route.methods or set())
    ]
    assert [(route.path, route.endpoint.__name__) for route in selected] == [
        ("/api/dashboards/{dashboard_id}", "get_dashboard"),
        ("/api/dashboards", "list_dashboards"),
        ("/api/dashboards/{dashboard_id}/versions", "get_dashboard_versions"),
        ("/api/dashboards/{dashboard_id}/versions/{version}", "get_dashboard_version"),
    ]
    assert [route.endpoint.__module__ for route in selected] == [
        "app.adapters.http.routers.dashboard_reads",
    ] * 4
    assert [get_type_hints(route.endpoint)["return"] for route in selected] == [
        dict[str, Any],
        list[dict[str, Any]],
        list[dict[str, Any]],
        dict[str, Any],
    ]
    assert [route.response_model for route in selected] == [
        dict[str, Any],
        list[dict[str, Any]],
        list[dict[str, Any]],
        dict[str, Any],
    ]
    operation_ids = [route.operation_id or route.unique_id for route in selected]
    assert operation_ids == [
        "get_dashboard_api_dashboards__dashboard_id__get",
        "list_dashboards_api_dashboards_get",
        "get_dashboard_versions_api_dashboards__dashboard_id__versions_get",
        "get_dashboard_version_api_dashboards__dashboard_id__versions__version__get",
    ]
    schema = app.openapi()
    assert set(schema["paths"]) >= set(DASHBOARD_PATHS)
    for path in DASHBOARD_PATHS:
        operation = schema["paths"][path]["get"]
        assert operation["operationId"] in operation_ids
        assert set(operation["responses"]) == {"200", "422"}
    assert routes.index(selected[0]) < routes.index(selected[1]) < routes.index(selected[2]) < routes.index(selected[3])

    save_route = next(
        route
        for route in routes
        if route.path == "/api/dashboards/{dashboard_id}" and "PUT" in (route.methods or set())
    )
    delete_version_route = next(
        route
        for route in routes
        if route.path == "/api/dashboards/{dashboard_id}/versions/{version}" and "DELETE" in (route.methods or set())
    )
    assert save_route.endpoint.__name__ == "save_dashboard"
    assert delete_version_route.endpoint.__name__ == "delete_dashboard_version"
    # Keep the legacy mutation routes interleaved between their read siblings:
    # detail GET < list GET < save PUT < versions GET < version-detail GET < delete.
    assert [routes.index(route) for route in (*selected[:2], save_route, *selected[2:], delete_version_route)] == sorted(
        routes.index(route) for route in (*selected[:2], save_route, *selected[2:], delete_version_route)
    )


@pytest.mark.unit
def test_dashboard_read_queries_forward_arguments_and_close_provider() -> None:
    calls: list[tuple[object, ...]] = []
    closed: list[bool] = []
    project_authorizer = lambda _project_id, _connection: None
    permission_check = lambda _project_id, _connection: True
    resource_authorizer = lambda _dashboard_id, _connection: None

    class Repository:
        def get_dashboard(self, dashboard_id, authorizer):
            calls.append(("detail", dashboard_id, authorizer))
            return {"id": dashboard_id}

        def list_dashboards(self, project_id, has_permission):
            calls.append(("list", project_id, has_permission))
            return [{"id": "dashboard-1"}]

        def get_dashboard_versions(self, dashboard_id, include_invalid, authorizer):
            calls.append(("versions", dashboard_id, include_invalid, authorizer))
            return [{"version": 2}]

        def get_dashboard_version(self, dashboard_id, version, include_invalid, authorizer):
            calls.append(("version", dashboard_id, version, include_invalid, authorizer))
            return {"dashboard_id": dashboard_id, "version": version}

    @contextmanager
    def provider():
        yield Repository()
        closed.append(True)

    assert queries.get_dashboard("dashboard-1", provider, project_authorizer) == {"id": "dashboard-1"}
    assert queries.list_dashboards(provider, "project-1", permission_check) == [{"id": "dashboard-1"}]
    assert queries.get_dashboard_versions("dashboard-1", provider, True, resource_authorizer) == [{"version": 2}]
    assert queries.get_dashboard_version("dashboard-1", 2, provider, False, resource_authorizer) == {
        "dashboard_id": "dashboard-1",
        "version": 2,
    }
    assert calls == [
        ("detail", "dashboard-1", project_authorizer),
        ("list", "project-1", permission_check),
        ("versions", "dashboard-1", True, resource_authorizer),
        ("version", "dashboard-1", 2, False, resource_authorizer),
    ]
    assert len(closed) == 4


@pytest.mark.contract
def test_main_relinquishes_dashboard_read_handlers_and_sql_with_ceiling_60() -> None:
    main_path = Path(main_module.__file__)
    source = main_path.read_text(encoding="utf-8")
    assert all(f"def {name}(" not in source for name in (
        "get_dashboard",
        "list_dashboards",
        "get_dashboard_versions",
        "get_dashboard_version",
    ))
    assert "dashboard_reads_router" in source
    assert "SELECT * FROM dashboards WHERE id = ?" not in source
    assert "SELECT dashboard_id, version, created_by, created_at, is_valid FROM dashboard_versions" not in source
    actual = sum(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
        for node in ast.walk(ast.parse(source))
    )
    baseline = json.loads((main_path.parents[1] / "scripts" / "architecture_baseline.json").read_text(encoding="utf-8"))
    assert actual <= 60
    assert actual == baseline["execute_call_ceilings"]["app/main.py"] == 60

    router_source = (main_path.parent / "adapters" / "http" / "routers" / "dashboard_reads.py").read_text(encoding="utf-8")
    assert ".execute(" not in router_source
    assert "write_audit_event" not in router_source
    assert all(marker not in router_source for marker in ("BEGIN", "COMMIT", "ROLLBACK"))
    assert all(f"@router.{method}(" not in router_source for method in ("post", "put", "patch", "delete"))


@pytest.mark.duckdb_integration
def test_dashboard_read_endpoints_filter_visibility_and_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    initialize_database()
    suffix = uuid4().hex[:10]
    viewer_username = f"dashboard-read-viewer-{suffix}"
    admin_username = f"dashboard-read-admin-{suffix}"
    viewer_id = _insert_password_user(viewer_username, is_global_admin=False)
    admin_id = _insert_password_user(admin_username, is_global_admin=True)
    dashboard_ids = [f"dashboard-read-draft-{suffix}", f"dashboard-read-archived-{suffix}"]
    invalid_dashboard_id = "dashboard-drop-default"
    invalid_version: int | None = None
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        with connect() as conn:
            for dashboard_id, status in zip(dashboard_ids, ("draft", "archived"), strict=True):
                definition = _dashboard_definition(dashboard_id, status)
                encoded = json.dumps(definition, ensure_ascii=False)
                conn.execute(
                    "INSERT INTO dashboards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        dashboard_id,
                        "project-tv-001",
                        "request-drop-001",
                        "loadcase-drop-bottom-001",
                        definition["name"],
                        definition["description"],
                        1,
                        encoded,
                        now,
                    ],
                )
                conn.execute(
                    "INSERT INTO dashboard_versions VALUES (?, ?, ?, ?, ?, false)",
                    [dashboard_id, 1, encoded, "dashboard-read-test", now],
                )

            source = conn.execute(
                "SELECT definition_json FROM dashboard_versions WHERE dashboard_id = ? ORDER BY version DESC LIMIT 1",
                [invalid_dashboard_id],
            ).fetchone()
            invalid_version = int(
                conn.execute(
                    "SELECT COALESCE(max(version), 0) + 1 FROM dashboard_versions WHERE dashboard_id = ?",
                    [invalid_dashboard_id],
                ).fetchone()[0]
            )
            conn.execute(
                "INSERT INTO dashboard_versions VALUES (?, ?, ?, ?, ?, false)",
                [invalid_dashboard_id, invalid_version, source[0], "dashboard-read-invalid", now],
            )

        with TestClient(app) as client:
            public_detail = client.get(f"/api/dashboards/{invalid_dashboard_id}")
            assert public_detail.status_code == 200
            assert {"id", "name", "widgets", "version", "updated_at"} <= set(public_detail.json())

            all_dashboards = client.get("/api/dashboards")
            assert all_dashboards.status_code == 200
            project_dashboards = client.get("/api/dashboards", params={"project_id": "project-tv-001"})
            assert project_dashboards.status_code == 200
            assert project_dashboards.json()
            assert all(item["project_id"] == "project-tv-001" for item in project_dashboards.json())
            assert {item["id"] for item in project_dashboards.json()} <= {item["id"] for item in all_dashboards.json()}

            valid_versions = client.get(f"/api/dashboards/{invalid_dashboard_id}/versions")
            assert valid_versions.status_code == 200
            assert invalid_version not in [item["version"] for item in valid_versions.json()]
            all_versions = client.get(
                f"/api/dashboards/{invalid_dashboard_id}/versions",
                params={"include_invalid": True},
            )
            assert all_versions.status_code == 200
            versions = [item["version"] for item in all_versions.json()]
            assert versions == sorted(versions, reverse=True)
            assert invalid_version in versions
            assert next(item for item in all_versions.json() if item["version"] == invalid_version)["is_valid"] is False

            assert client.get(f"/api/dashboards/{invalid_dashboard_id}/versions/{invalid_version}").status_code == 404
            invalid_detail = client.get(
                f"/api/dashboards/{invalid_dashboard_id}/versions/{invalid_version}",
                params={"include_invalid": True},
            )
            assert invalid_detail.status_code == 200
            assert set(invalid_detail.json()) == {
                "dashboard_id", "version", "definition", "created_by", "created_at", "is_valid",
            }
            assert invalid_detail.json()["dashboard_id"] == invalid_dashboard_id
            assert invalid_detail.json()["version"] == invalid_version
            assert invalid_detail.json()["created_by"] == "dashboard-read-invalid"
            assert invalid_detail.json()["is_valid"] is False
            for params in ({}, {"include_invalid": True}):
                assert client.get(
                    f"/api/dashboards/{invalid_dashboard_id}/versions/999",
                    params=params,
                ).status_code == 404

            monkeypatch.setenv("AUTH_MODE", "password")
            monkeypatch.setenv("AUTH_SECRET_KEY", "dashboard-read-test-secret-key-at-least-32")
            password = "correct-horse-battery-staple"
            viewer_login = client.post("/api/auth/login", json={"username": viewer_username, "password": password})
            admin_login = client.post("/api/auth/login", json={"username": admin_username, "password": password})
            assert viewer_login.status_code == admin_login.status_code == 200
            viewer_headers = {"Authorization": f"Bearer {viewer_login.json()['access_token']}"}
            admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}

            for dashboard_id in dashboard_ids:
                assert client.get(f"/api/dashboards/{dashboard_id}", headers=viewer_headers).status_code == 403
                assert client.get(f"/api/dashboards/{dashboard_id}/versions", headers=viewer_headers).status_code == 403
                assert client.get(f"/api/dashboards/{dashboard_id}/versions/1", headers=viewer_headers).status_code == 403
                assert client.get(f"/api/dashboards/{dashboard_id}", headers=admin_headers).status_code == 200
                assert client.get(f"/api/dashboards/{dashboard_id}/versions", headers=admin_headers).status_code == 200

            viewer_list = client.get("/api/dashboards", headers=viewer_headers)
            admin_list = client.get("/api/dashboards", headers=admin_headers)
            assert viewer_list.status_code == admin_list.status_code == 200
            assert not ({item["id"] for item in viewer_list.json()} & set(dashboard_ids))
            assert set(dashboard_ids) <= {item["id"] for item in admin_list.json()}
            # Resource lookup precedes the draft/archived permission check on
            # all three read paths which receive a dashboard identifier.
            missing_path = f"/api/dashboards/dashboard-read-missing-{suffix}"
            assert client.get(missing_path, headers=viewer_headers).status_code == 404
            assert client.get(f"{missing_path}/versions", headers=viewer_headers).status_code == 404
            assert client.get(f"{missing_path}/versions/1", headers=viewer_headers).status_code == 404
    finally:
        with connect() as conn:
            if invalid_version is not None:
                conn.execute(
                    "DELETE FROM dashboard_versions WHERE dashboard_id = ? AND version = ?",
                    [invalid_dashboard_id, invalid_version],
                )
            for dashboard_id in dashboard_ids:
                conn.execute("DELETE FROM dashboard_versions WHERE dashboard_id = ?", [dashboard_id])
                conn.execute("DELETE FROM dashboards WHERE id = ?", [dashboard_id])
            if database_settings().backend == "duckdb":
                conn.execute("DELETE FROM audit_events WHERE user_id IN (?, ?)", [viewer_id, admin_id])
            conn.execute("DELETE FROM project_memberships WHERE user_id IN (?, ?)", [viewer_id, admin_id])
            conn.execute("DELETE FROM users WHERE id IN (?, ?)", [viewer_id, admin_id])
