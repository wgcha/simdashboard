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
