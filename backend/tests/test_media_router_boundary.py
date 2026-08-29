from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.main import app
from app.routers import media
from scripts.check_openapi_contract import check_contract


def test_result_media_read_routes_are_owned_by_media_router_in_contract_order() -> None:
    routes = [
        route
        for route in app.routes
        if getattr(route, "path", "") in {
            "/api/assets/{asset_id}",
            "/api/assets/{asset_id}/download",
        }
    ]
    assert [(route.path, tuple(sorted(route.methods or ()))) for route in routes] == [
        ("/api/assets/{asset_id}", ("HEAD",)),
        ("/api/assets/{asset_id}", ("GET",)),
        ("/api/assets/{asset_id}/download", ("HEAD",)),
        ("/api/assets/{asset_id}/download", ("GET",)),
    ]
    assert all(route.endpoint.__module__ == media.__name__ for route in routes)
    paths = [getattr(route, "path", "") for route in app.routes]
    assert paths.index("/api/load-cases/{load_case_id}/results/import") < paths.index(
        "/api/assets/{asset_id}"
    ) < paths.index("/api/load-cases/{load_case_id}/overview")
    get_routes = [route for route in routes if getattr(route, "methods", set()) == {"GET"}]
    assert [route.operation_id for route in get_routes] == [
        "get_result_asset",
        "download_result_asset",
    ]


def test_main_no_longer_owns_media_read_handlers_and_router_has_no_direct_sql_execution() -> None:
    main_source = Path(__file__).parents[1].joinpath("app", "main.py").read_text()
    assert "def _result_asset_response" not in main_source
    assert "def get_result_asset" not in main_source
    assert "def download_result_asset" not in main_source

    tree = ast.parse(Path(media.__file__).read_text())
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "execute"
        for node in ast.walk(tree)
    )


def test_legacy_media_path_stays_inside_the_backend_assets_root() -> None:
    expected = Path(media.__file__).resolve().parents[2] / "assets" / "sample-contour.svg"
    assert media._legacy_asset_path("assets/sample-contour.svg") == expected.resolve()
    with pytest.raises(HTTPException) as exc_info:
        media._legacy_asset_path("../public_assets/sample-contour.svg")
    assert exc_info.value.status_code == 404


def test_media_read_extraction_preserves_the_checked_in_openapi_contract() -> None:
    snapshot = Path(__file__).parents[2] / "frontend" / "openapi.json"
    assert check_contract(snapshot, app.openapi()) == []
