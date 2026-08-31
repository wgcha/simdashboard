from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.adapters.http.routers import project_memberships
from app.main import app
from scripts.check_openapi_contract import check_contract


pytestmark = pytest.mark.unit

BACKEND = Path(__file__).parents[1]


def test_membership_routes_are_registered_in_contract_order_with_stable_operation_ids() -> None:
    routes = [
        route
        for route in app.routes
        if getattr(route, "path", "")
        in {
            "/api/projects/{project_id}/members",
            "/api/projects/{project_id}/members/{user_id}",
        }
    ]
    assert [(route.path, tuple(sorted(route.methods or ()))) for route in routes] == [
        ("/api/projects/{project_id}/members", ("GET",)),
        ("/api/projects/{project_id}/members", ("POST",)),
        ("/api/projects/{project_id}/members/{user_id}", ("PATCH",)),
        ("/api/projects/{project_id}/members/{user_id}", ("DELETE",)),
    ]
    assert [route.endpoint.__name__ for route in routes] == [
        "list_project_members",
        "create_project_member",
        "update_project_member",
        "delete_project_member",
    ]
    assert all(route.endpoint.__module__ == project_memberships.__name__ for route in routes)
    paths = app.openapi()["paths"]
    assert paths["/api/projects/{project_id}/members"]["get"]["operationId"] == (
        "list_project_members_api_projects__project_id__members_get"
    )
    assert paths["/api/projects/{project_id}/members"]["post"]["operationId"] == (
        "create_project_member_api_projects__project_id__members_post"
    )
    assert paths["/api/projects/{project_id}/members/{user_id}"]["patch"]["operationId"] == (
        "update_project_member_api_projects__project_id__members__user_id__patch"
    )
    assert paths["/api/projects/{project_id}/members/{user_id}"]["delete"]["operationId"] == (
        "delete_project_member_api_projects__project_id__members__user_id__delete"
    )


def test_membership_http_adapter_has_no_direct_database_calls() -> None:
    tree = ast.parse(Path(project_memberships.__file__).read_text(encoding="utf-8"))
    assert not any(
        isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Attribute) and node.func.attr == "execute")
            or (isinstance(node.func, ast.Name) and node.func.id in {"connect", "rows"})
        )
        for node in ast.walk(tree)
    )


def test_membership_extraction_removed_legacy_handlers_and_keeps_include_before_directory() -> None:
    source = (BACKEND / "app" / "routers" / "access_control.py").read_text(encoding="utf-8")
    for name in (
        "list_project_members",
        "create_project_member",
        "update_project_member",
        "delete_project_member",
    ):
        assert f"def {name}(" not in source
    assert "@router.get(\"/api/projects/{project_id}/members\")" not in source
    assert "@router.post(\"/api/projects/{project_id}/members\"" not in source
    assert "@router.patch(\"/api/projects/{project_id}/members/{user_id}\")" not in source
    assert "@router.delete(\"/api/projects/{project_id}/members/{user_id}\")" not in source
    assert source.index("router.include_router(project_memberships_router)") < source.index(
        "def search_directory_employees("
    )


def test_extraction_preserves_checked_in_openapi_contract() -> None:
    snapshot = BACKEND.parent / "frontend" / "openapi.json"
    assert check_contract(snapshot, app.openapi()) == []
