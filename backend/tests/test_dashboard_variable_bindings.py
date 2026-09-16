from __future__ import annotations

import json

import pytest

from app.schemas.api import DashboardDefinition
from app.adapters.persistence.variable_catalog import SQLVariableCatalogRepository
from app.services.dashboard_variable_bindings import (
    referenced_variable_keys,
    validate_dashboard_variable_bindings,
)


def _definition(settings: dict[str, object], *, widget_type: str = "name_value") -> dict[str, object]:
    return {
        "id": "dashboard-variable-bindings",
        "name": "변수 연결",
        "description": "",
        "widgets": [
            {
                "id": "widget-1",
                "type": widget_type,
                "title": "값",
                "x": 0,
                "y": 0,
                "w": 8,
                "h": 4,
                "settings": settings,
            }
        ],
    }


def test_explicit_empty_variable_ids_does_not_fall_back_to_legacy_binding() -> None:
    widget = {"settings": {"variableIds": [], "variableId": "legacy"}}
    assert referenced_variable_keys(widget) == []
    DashboardDefinition.model_validate(_definition(widget["settings"]))


@pytest.mark.unit
def test_dashboard_schema_rejects_variable_ids_for_single_binding_widgets() -> None:
    with pytest.raises(ValueError, match="여러 변수를 지원하지 않습니다"):
        DashboardDefinition.model_validate(_definition({"variableIds": ["stress"]}, widget_type="kpi"))


@pytest.mark.unit
def test_dashboard_schema_rejects_malformed_or_duplicate_variable_ids() -> None:
    with pytest.raises(ValueError, match="문자열 배열"):
        DashboardDefinition.model_validate(_definition({"variableIds": "stress"}))
    with pytest.raises(ValueError, match="중복"):
        DashboardDefinition.model_validate(_definition({"variableIds": ["stress", "stress"]}))


@pytest.mark.unit
def test_dashboard_variable_binding_rejects_unknown_or_incompatible_catalog_variables() -> None:
    class Cursor:
        def fetchall(self):
            return [
                ("stress", json.dumps(["kpi", "name_value"])),
                ("temperature", json.dumps(["time_series"])),
            ]

    class Connection:
        def execute(self, statement: str, parameters: object = None):
            assert "variable_definitions" in statement
            assert parameters == ["load-case-1"]
            return Cursor()

    validate_dashboard_variable_bindings(
        Connection(),
        "load-case-1",
        _definition({"variableIds": ["stress"]}),
    )
    with pytest.raises(ValueError, match="존재하지 않거나 비활성화"):
        validate_dashboard_variable_bindings(
            Connection(),
            "load-case-1",
            _definition({"variableIds": ["missing"]}),
        )
    with pytest.raises(ValueError, match="사용할 수 없는"):
        validate_dashboard_variable_bindings(
            Connection(),
            "load-case-1",
            _definition({"variableIds": ["temperature"]}),
        )


@pytest.mark.unit
def test_legacy_variable_id_remains_accepted_while_usage_counts_include_variable_ids() -> None:
    legacy = {"settings": {"variableId": "legacy-key"}}
    DashboardDefinition.model_validate(_definition(legacy["settings"]))

    class Cursor:
        def fetchall(self):
            return [
                ("legacy-dashboard", json.dumps({"widgets": [legacy]})),
                (
                    "multi-dashboard",
                    json.dumps({"widgets": [{"settings": {"variableIds": ["legacy-key", "other"]}}]}),
                ),
            ]

    class Connection:
        def execute(self, statement: str, parameters: object = None):
            assert "dashboards" in statement
            assert parameters == ["load-case-1"]
            return Cursor()

    repository = SQLVariableCatalogRepository(Connection())
    assert repository.dashboard_references("load-case-1", "legacy-key") == [
        "legacy-dashboard",
        "multi-dashboard",
    ]
