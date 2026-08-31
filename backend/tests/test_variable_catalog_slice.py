from __future__ import annotations

import inspect
import json
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.adapters.http.routers import variable_catalog
from app.application.variable_catalog import commands as variable_commands
from app.database import initialize_database
from app.database_connection import connect
from app.domains.variable_catalog.errors import (
    LoadCaseNotFoundError,
    VariableAlreadyExistsError,
    VariableCatalogValidationError,
    VariableNotFoundError,
)
from app.main import app
from app.schemas.api import VariableCreate, VariableUpdate


LOAD_CASE_ID = "loadcase-drop-bottom-001"


@pytest.mark.contract
def test_variable_catalog_router_owns_exactly_four_routes_in_legacy_order() -> None:
    routes = [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.endpoint.__module__ == variable_catalog.__name__
    ]

    assert [(route.path, tuple(sorted(route.methods or ()))) for route in routes] == [
        ("/api/load-cases/{load_case_id}/variables", ("GET",)),
        ("/api/load-cases/{load_case_id}/variables", ("POST",)),
        ("/api/load-cases/{load_case_id}/variables/{variable_key}", ("PUT",)),
        ("/api/load-cases/{load_case_id}/variables/{variable_key}", ("DELETE",)),
    ]
    assert [route.endpoint.__name__ for route in routes] == [
        "get_variables",
        "create_variable",
        "update_variable",
        "delete_variable",
    ]
    assert [route.operation_id or route.unique_id for route in routes] == [
        "get_variables_api_load_cases__load_case_id__variables_get",
        "create_variable_api_load_cases__load_case_id__variables_post",
        "update_variable_api_load_cases__load_case_id__variables__variable_key__put",
        "delete_variable_api_load_cases__load_case_id__variables__variable_key__delete",
    ]


@pytest.mark.contract
def test_main_relinquishes_variable_catalog_ownership() -> None:
    source = (Path(__file__).parents[1] / "app" / "main.py").read_text(encoding="utf-8")
    for token in (
        "VariableCatalogRepository",
        "VariableCreate",
        "VariableUpdate",
        "def get_variables",
        "def create_variable",
        "def update_variable",
        "def delete_variable",
        "def _catalog_error",
        "PROJECT_VARIABLE_MANAGE",
    ):
        assert token not in source
    assert "app.include_router(variable_catalog_router)" in source


@pytest.mark.unit
def test_get_variables_has_no_explicit_permission_check() -> None:
    source = inspect.getsource(variable_catalog.get_variables)
    assert "require_resource_permission" not in source


@pytest.mark.duckdb_integration
def test_get_variables_preserves_legacy_open_access_shape_and_missing_load_case_404() -> None:
    initialize_database()
    with TestClient(app) as client:
        response = client.get("/api/load-cases/missing-variable-catalog-load/variables")
    assert response.status_code == 404
    assert response.json() == {"detail": "하중 경우를 찾을 수 없습니다."}


@pytest.mark.unit
def test_mutations_authorize_on_the_existing_connection_before_catalog_mutation(monkeypatch) -> None:
    events: list[str] = []
    connection = object()

    class FakeRepository:
        def authorize_mutation(self, load_case_id: str) -> None:
            assert load_case_id == LOAD_CASE_ID
            events.append(f"authorize:{self.connection is connection}")

        def create(self, *_args):
            events.append("create")
            return {"id": "variable-test"}

        def update(self, *_args):
            events.append("update")
            return {"id": "variable-test"}

        def dashboard_references(self, *_args):
            events.append("references")
            return []

        def deactivate(self, *_args):
            events.append("deactivate")

    def make_repository() -> FakeRepository:
        repository = FakeRepository()
        repository.connection = connection
        events.append("open")
        return repository

    @contextmanager
    def provider():
        repository = make_repository()
        try:
            yield repository
        finally:
            events.append("close")

    create_payload = VariableCreate(
        variable_key="variable_test",
        display_name="Test variable",
        data_type="NUMBER",
        unit="mm",
        threshold=1.0,
        updated_by="client actor",
    )
    update_payload = VariableUpdate(
        display_name="Updated variable",
        unit="mm",
        threshold=2.0,
        updated_by="client actor",
    )

    variable_commands.create_variable(LOAD_CASE_ID, create_payload.model_dump(), provider)
    assert events == ["open", "authorize:True", "create", "close"]
    events.clear()
    variable_commands.update_variable(LOAD_CASE_ID, "variable_test", update_payload.model_dump(), provider)
    assert events == ["open", "authorize:True", "update", "close"]
    events.clear()
    variable_commands.delete_variable(LOAD_CASE_ID, "variable_test", None, provider)
    assert events == [
        "open",
        "authorize:True",
        "references",
        "deactivate",
        "close",
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("error", "status", "detail"),
    [
        (VariableAlreadyExistsError(), 409, "같은 변수 키가 이미 존재합니다."),
        (VariableNotFoundError(), 404, "변수를 찾을 수 없습니다."),
        (LoadCaseNotFoundError(), 404, "하중 경우를 찾을 수 없습니다."),
        (VariableCatalogValidationError("INVALID_CATALOG_OPTIONS"), 422, "데이터 유형에 허용되지 않은 위젯 또는 집계 방식입니다."),
        (VariableCatalogValidationError("NUMBER_THRESHOLD_REQUIRED"), 422, "숫자 변수에는 판정 기준값이 필요합니다."),
    ],
)
def test_catalog_error_map_is_exact(error: Exception, status: int, detail: str) -> None:
    with pytest.raises(Exception) as exc_info:
        variable_catalog._catalog_error(error)  # type: ignore[arg-type]
    assert exc_info.value.status_code == status
    assert exc_info.value.detail == detail


def _number_payload(key: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "variable_key": key,
        "display_name": "Catalog variable",
        "data_type": "NUMBER",
        "unit": "mm",
        "description": "catalog test",
        "threshold": 10.0,
        "allowed_widgets": ["kpi", "result_table"],
        "allowed_aggregations": ["AVG", "LATEST"],
        "result_group": "CUSTOM",
        "updated_by": "client actor",
    }
    payload.update(overrides)
    return payload


@pytest.mark.duckdb_integration
def test_create_duplicate_reactivate_update_immutable_fields_and_client_actor() -> None:
    initialize_database()
    suffix = uuid4().hex[:10]
    key = f"catalog_{suffix}"
    created_id = None
    try:
        with TestClient(app) as client:
            created = client.post(f"/api/load-cases/{LOAD_CASE_ID}/variables", json=_number_payload(key))
            assert created.status_code == 201, created.text
            created_id = created.json()["definition_id"]
            assert created.json()["id"] == key
            assert created.json()["updated_by"] == "client actor"

            duplicate = client.post(f"/api/load-cases/{LOAD_CASE_ID}/variables", json=_number_payload(key))
            assert duplicate.status_code == 409
            assert duplicate.json() == {"detail": "같은 변수 키가 이미 존재합니다."}

            updated = client.put(
                f"/api/load-cases/{LOAD_CASE_ID}/variables/{key}",
                json={
                    "variable_key": f"renamed_{suffix}",
                    "data_type": "TEXT",
                    "display_name": "Updated catalog variable",
                    "unit": "mm",
                    "threshold": 20.0,
                    "allowed_widgets": ["kpi"],
                    "allowed_aggregations": ["MAX"],
                    "updated_by": "client update",
                },
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()["id"] == key
            assert updated.json()["variable_key"] == key
            assert updated.json()["data_type"] == "NUMBER"
            assert updated.json()["updated_by"] == "client update"

            deleted = client.delete(f"/api/load-cases/{LOAD_CASE_ID}/variables/{key}")
            assert deleted.status_code == 200
            assert deleted.json() == {"status": "deactivated", "variable_key": key}

            reactivated = client.post(
                f"/api/load-cases/{LOAD_CASE_ID}/variables",
                json=_number_payload(
                    key,
                    display_name="Reactivated text variable",
                    data_type="TEXT",
                    threshold=None,
                    allowed_widgets=["note"],
                    allowed_aggregations=["LATEST"],
                    updated_by="reactivation actor",
                ),
            )
            assert reactivated.status_code == 201, reactivated.text
            # This slice preserves the existing reactivation behavior: the
            # inactive row may be reactivated with a changed data_type.
            assert reactivated.json()["data_type"] == "TEXT"
            assert reactivated.json()["updated_by"] == "reactivation actor"

        with connect() as connection:
            stored = connection.execute(
                "SELECT variable_key, data_type, is_active, updated_by FROM variable_definitions WHERE id=?",
                [created_id],
            ).fetchone()
            assert stored == (key, "TEXT", True, "reactivation actor")
    finally:
        with connect() as connection:
            connection.execute(
                "DELETE FROM variable_definitions WHERE load_case_id=? AND variable_key=?",
                [LOAD_CASE_ID, key],
            )


@pytest.mark.duckdb_integration
def test_catalog_options_threshold_and_list_hydration_data_and_dashboard_usage() -> None:
    initialize_database()
    suffix = uuid4().hex[:10]
    first_key = f"catalog_a_{suffix}"
    second_key = f"catalog_z_{suffix}"
    dashboard_id = f"dashboard-catalog-{suffix}"
    run_id = "run-drop-001"
    try:
        with TestClient(app) as client:
            invalid_options = client.post(
                f"/api/load-cases/{LOAD_CASE_ID}/variables",
                json=_number_payload(f"catalog_bad_{suffix}", allowed_widgets=["note"]),
            )
            assert invalid_options.status_code == 422
            assert invalid_options.json() == {"detail": "데이터 유형에 허용되지 않은 위젯 또는 집계 방식입니다."}

            missing_threshold = client.post(
                f"/api/load-cases/{LOAD_CASE_ID}/variables",
                json=_number_payload(f"catalog_threshold_{suffix}", threshold=None),
            )
            assert missing_threshold.status_code == 422
            assert missing_threshold.json() == {"detail": "숫자 변수에는 판정 기준값이 필요합니다."}

            first = client.post(
                f"/api/load-cases/{LOAD_CASE_ID}/variables",
                json=_number_payload(first_key, display_name="A catalog variable"),
            )
            second = client.post(
                f"/api/load-cases/{LOAD_CASE_ID}/variables",
                json=_number_payload(second_key, display_name="Z catalog variable"),
            )
            assert first.status_code == second.status_code == 201

        with connect() as connection:
            connection.execute(
                "INSERT INTO scalar_results VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)",
                [f"scalar-catalog-{suffix}", run_id, first_key, "A catalog variable", 4.5, "mm", 10.0, "PASS"],
            )
            definition = json.dumps(
                {
                    "id": dashboard_id,
                    "name": "catalog usage",
                    "description": "",
                    "widgets": [
                        {
                            "id": "catalog-widget",
                            "type": "kpi",
                            "settings": {"variableId": first_key},
                        }
                    ],
                }
            )
            connection.execute(
                "INSERT INTO dashboards VALUES (?, 'project-tv-001', 'request-drop-001', ?, 'catalog usage', '', 1, ?, now())",
                [dashboard_id, LOAD_CASE_ID, definition],
            )

        with TestClient(app) as client:
            listed = client.get(f"/api/load-cases/{LOAD_CASE_ID}/variables")
            assert listed.status_code == 200, listed.text
            selected = [item for item in listed.json() if item["id"] in {first_key, second_key}]
            assert [item["id"] for item in selected] == [first_key, second_key]
            first_item = selected[0]
            assert first_item["definition_id"].startswith("variable-")
            assert first_item["threshold"] == 10.0
            assert first_item["allowed_widgets"] == ["kpi", "result_table"]
            assert first_item["allowed_aggregations"] == ["AVG", "LATEST"]
            assert first_item["has_data"] is True
            assert first_item["dashboard_usage_count"] == 1
            assert selected[1]["has_data"] is False
            assert selected[1]["dashboard_usage_count"] == 0
    finally:
        with connect() as connection:
            connection.execute("DELETE FROM dashboards WHERE id=?", [dashboard_id])
            connection.execute("DELETE FROM scalar_results WHERE id=?", [f"scalar-catalog-{suffix}"])
            connection.execute(
                "DELETE FROM variable_definitions WHERE load_case_id=? AND variable_key LIKE ?",
                [LOAD_CASE_ID, f"catalog_%_{suffix}"],
            )


@pytest.mark.duckdb_integration
def test_referenced_delete_returns_detail_and_soft_deletes_with_default_actor() -> None:
    initialize_database()
    suffix = uuid4().hex[:10]
    key = f"catalog_delete_{suffix}"
    dashboard_id = f"dashboard-delete-catalog-{suffix}"
    try:
        with TestClient(app) as client:
            created = client.post(
                f"/api/load-cases/{LOAD_CASE_ID}/variables",
                json=_number_payload(key),
            )
            assert created.status_code == 201, created.text
        with connect() as connection:
            connection.execute(
                "INSERT INTO dashboards VALUES (?, 'project-tv-001', 'request-drop-001', ?, 'catalog usage', '', 1, ?, now())",
                [
                    dashboard_id,
                    LOAD_CASE_ID,
                    json.dumps({"widgets": [{"settings": {"variableId": key}}]}),
                ],
            )
        with TestClient(app) as client:
            blocked = client.delete(f"/api/load-cases/{LOAD_CASE_ID}/variables/{key}")
            assert blocked.status_code == 409
            assert blocked.json() == {
                "detail": {
                    "message": "대시보드에서 사용 중인 변수는 삭제할 수 없습니다.",
                    "dashboard_ids": [dashboard_id],
                }
            }
        with connect() as connection:
            connection.execute("DELETE FROM dashboards WHERE id=?", [dashboard_id])
        with TestClient(app) as client:
            deleted = client.delete(f"/api/load-cases/{LOAD_CASE_ID}/variables/{key}")
            assert deleted.status_code == 200
            assert client.get(f"/api/load-cases/{LOAD_CASE_ID}/variables").json()
            assert all(item["id"] != key for item in client.get(f"/api/load-cases/{LOAD_CASE_ID}/variables").json())
        with connect() as connection:
            stored = connection.execute(
                "SELECT is_active, updated_by FROM variable_definitions WHERE load_case_id=? AND variable_key=?",
                [LOAD_CASE_ID, key],
            ).fetchone()
            assert stored == (False, "관리자")
    finally:
        with connect() as connection:
            connection.execute("DELETE FROM dashboards WHERE id=?", [dashboard_id])
            connection.execute(
                "DELETE FROM variable_definitions WHERE load_case_id=? AND variable_key=?",
                [LOAD_CASE_ID, key],
            )
