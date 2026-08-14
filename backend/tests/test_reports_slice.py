from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

import pytest
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient

from app.adapters.persistence.reports import SQLReportLayoutRepository
from app.application.reports.queries import list_report_layouts
from app.database import json_value
from app.database_connection import connect, rows
from app.domains.reports.models import ReportLayout
from app.domains.reports.ports import ReportLayoutRepository
from app.main import app
from app.schemas.reports import ReportLayoutSavedResponse


class FakeReportLayoutRepository:
    def __init__(self, layouts: list[ReportLayout], events: list[str]) -> None:
        self._layouts = layouts
        self._events = events

    def list_active_layouts(self) -> list[ReportLayout]:
        self._events.append("list")
        return self._layouts


@pytest.mark.unit
def test_report_query_authorizes_before_repository_provider() -> None:
    events: list[str] = []
    expected: list[ReportLayout] = [
        {
            "id": "layout-a",
            "name": "A",
            "description": "",
            "version": 1,
            "is_system": True,
            "is_active": True,
            "created_at": datetime(2025, 1, 1),
            "updated_at": datetime(2025, 1, 1),
            "updated_by": "system",
            "definition": {},
        }
    ]

    def authorize() -> object:
        events.append("authorize")
        return object()

    @contextmanager
    def provider() -> Iterator[ReportLayoutRepository]:
        events.append("open")
        yield FakeReportLayoutRepository(expected, events)
        events.append("close")

    assert list_report_layouts(authorize, provider) == expected
    assert events == ["authorize", "open", "list", "close"]


@pytest.mark.unit
def test_report_query_denial_does_not_open_repository() -> None:
    opened = False

    def deny() -> object:
        raise PermissionError("denied")

    @contextmanager
    def provider() -> Iterator[ReportLayoutRepository]:
        nonlocal opened
        opened = True
        yield FakeReportLayoutRepository([], [])

    with pytest.raises(PermissionError, match="denied"):
        list_report_layouts(deny, provider)
    assert not opened


@pytest.mark.unit
def test_report_sql_adapter_preserves_legacy_definition_mapping() -> None:
    class Cursor:
        description = [
            ("id",),
            ("name",),
            ("description",),
            ("version",),
            ("definition_json",),
            ("is_system",),
            ("is_active",),
            ("created_at",),
            ("updated_at",),
            ("updated_by",),
        ]

        def fetchall(self) -> list[tuple[Any, ...]]:
            return [
                (
                    "layout-a",
                    "Layout A",
                    None,
                    2,
                    '{"coverVariant":"balanced"}',
                    True,
                    True,
                    datetime(2025, 1, 1),
                    datetime(2025, 1, 2),
                    "editor",
                )
            ]

    class Connection:
        def execute(self, statement: str, parameters: Any | None = None) -> Cursor:
            assert statement == "SELECT * FROM report_layouts WHERE is_active=true ORDER BY is_system DESC, updated_at DESC, name"
            assert parameters is None
            return Cursor()

    assert SQLReportLayoutRepository(Connection()).list_active_layouts() == [  # type: ignore[arg-type]
        {
            "id": "layout-a",
            "name": "Layout A",
            "description": None,
            "version": 2,
            "is_system": True,
            "is_active": True,
            "created_at": datetime(2025, 1, 1),
            "updated_at": datetime(2025, 1, 2),
            "updated_by": "editor",
            "definition": {
                "coverVariant": "balanced",
                "id": "layout-a",
                "name": "Layout A",
                "description": "",
                "version": 2,
            },
        }
    ]


@pytest.mark.contract
def test_report_layout_list_endpoint_preserves_seeded_response_contract() -> None:
    with connect() as connection:
        expected_items = rows(
            connection.execute(
                "SELECT * FROM report_layouts WHERE is_active=true ORDER BY is_system DESC, updated_at DESC, name"
            )
        )
    expected: list[dict[str, Any]] = []
    for item in expected_items:
        definition = json_value(item.pop("definition_json")) or {}
        definition.update(
            {
                "id": item["id"],
                "name": item["name"],
                "description": item.get("description") or "",
                "version": item["version"],
            }
        )
        expected.append({**item, "definition": definition})

    with TestClient(app) as client:
        response = client.get("/api/report-layouts")

    assert response.status_code == 200
    assert response.json() == jsonable_encoder(expected)


@pytest.mark.unit
def test_report_layout_response_definition_keeps_extension_keys() -> None:
    response = ReportLayoutSavedResponse.model_validate(
        {
            "id": "layout-a",
            "name": "Layout A",
            "description": "",
            "version": 1,
            "definition": {
                "id": "layout-a",
                "name": "Layout A",
                "description": "",
                "version": 1,
                "coverVariant": "balanced",
                "accentColor": "1898D5",
                "sectionOrder": ["series", "scalar", "media"],
                "variablePlacements": [
                    {
                        "variableKey": "top_edge_max_stress",
                        "presentation": "table",
                        "order": 0,
                        "placement_extension": {"visible": True},
                    }
                ],
                "includeMedia": False,
                "client_extension": {"nested": ["preserved", {"enabled": True}]},
            },
            "is_system": False,
            "updated_at": datetime(2025, 1, 1),
            "updated_by": "editor",
        }
    )

    assert response.model_dump()["definition"]["client_extension"] == {
        "nested": ["preserved", {"enabled": True}]
    }
    assert response.model_dump()["definition"]["variablePlacements"][0]["placement_extension"] == {
        "visible": True
    }


@pytest.mark.contract
def test_report_layout_response_models_are_named_openapi_refs() -> None:
    schema = app.openapi()
    paths = schema["paths"]
    components = schema["components"]["schemas"]

    assert paths["/api/report-layouts"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "items": {"$ref": "#/components/schemas/ReportLayoutCatalogResponse"},
        "type": "array",
        "title": "Response List Report Layouts Api Report Layouts Get",
    }
    assert paths["/api/report-layouts"]["post"]["responses"]["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ReportLayoutSavedResponse"
    }
    assert paths["/api/report-layouts/{layout_id}"]["put"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ReportLayoutSavedResponse"
    }
    assert paths["/api/report-layouts/{layout_id}/versions"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "items": {"$ref": "#/components/schemas/ReportLayoutVersionSummaryResponse"},
        "type": "array",
        "title": "Response Get Report Layout Versions Api Report Layouts  Layout Id  Versions Get",
    }
    assert paths["/api/report-layouts/{layout_id}/versions/{version}"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ReportLayoutVersionDetailResponse"
    }
    assert paths["/api/report-layouts/{layout_id}"]["delete"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ReportLayoutDeactivationResponse"
    }
    assert components["ReportLayoutDefinition"]["additionalProperties"] is True
    assert components["ReportLayoutDefinition"]["properties"]["variablePlacements"]["items"] == {
        "$ref": "#/components/schemas/ReportVariablePlacement"
    }
    assert components["ReportVariablePlacement"]["additionalProperties"] is True


@pytest.mark.contract
def test_report_layout_mutation_response_models_preserve_response_shape_and_definition_extensions() -> None:
    payload = {
        "name": "응답 DTO 회귀",
        "description": "저장 JSON 확장 키 보존",
        "definition": {
            "coverVariant": "balanced",
            "accentColor": "1898D5",
            "sectionOrder": ["series", "scalar", "media"],
            "variablePlacements": [],
            "includeMedia": False,
            "client_extension": {"nested": ["preserved", {"enabled": True}]},
        },
        "updated_by": "계약 검사",
    }
    layout_id: str | None = None
    try:
        with TestClient(app) as client:
            created = client.post("/api/report-layouts", json=payload)
            assert created.status_code == 201
            layout_id = created.json()["id"]
            assert set(created.json()) == {
                "id",
                "name",
                "description",
                "version",
                "definition",
                "is_system",
                "updated_at",
                "updated_by",
            }
            assert created.json()["definition"]["client_extension"] == payload["definition"]["client_extension"]

            updated = client.put(f"/api/report-layouts/{layout_id}", json=payload)
            assert updated.status_code == 200
            assert updated.json()["version"] == 2
            assert updated.json()["definition"]["client_extension"] == payload["definition"]["client_extension"]

            summaries = client.get(f"/api/report-layouts/{layout_id}/versions")
            assert summaries.status_code == 200
            assert set(summaries.json()[0]) == {"layout_id", "version", "created_by", "created_at", "is_valid"}

            detail = client.get(f"/api/report-layouts/{layout_id}/versions/1")
            assert detail.status_code == 200
            assert set(detail.json()) == {"layout_id", "version", "definition", "created_by", "created_at"}
            assert detail.json()["definition"]["client_extension"] == payload["definition"]["client_extension"]

            deactivated = client.delete(f"/api/report-layouts/{layout_id}")
            assert deactivated.status_code == 200
            assert deactivated.json() == {"status": "deactivated", "id": layout_id}
    finally:
        if layout_id:
            with connect() as connection:
                connection.execute("DELETE FROM report_layout_versions WHERE layout_id=?", [layout_id])
                connection.execute("DELETE FROM report_layouts WHERE id=?", [layout_id])
