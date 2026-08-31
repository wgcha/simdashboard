from __future__ import annotations

import json
from copy import deepcopy
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient
from fastapi.routing import APIRoute

from app.adapters.http.routers import reports as reports_router
from app.adapters.persistence.reports import SQLReportLayoutRepository
from app.application.reports.commands import (
    create_report_layout,
    deactivate_report_layout,
    update_report_layout,
)
from app.application.reports.policies import validate_report_layout
from app.application.reports.queries import (
    get_report_layout_version,
    list_report_layout_versions,
    list_report_layouts,
)
from app.database import json_value
from app.database_connection import connect, rows
from app.domains.reports.models import (
    InvalidReportLayoutError,
    ReportLayout,
    ReportLayoutNotFoundError,
    ReportLayoutVersionNotFoundError,
    SystemReportLayoutDeactivationError,
)
from app.domains.reports.ports import ReportLayoutRepository
from app.main import app
from app.schemas.reports import ReportLayoutSavedResponse
from app.security import hash_password


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
            assert statement == (
                "SELECT * FROM report_layouts WHERE is_active=true "
                "ORDER BY is_system DESC, updated_at DESC, name"
            )
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
    update_schema = paths["/api/report-layouts/{layout_id}"]["put"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert update_schema == {
        "$ref": "#/components/schemas/ReportLayoutSavedResponse"
    }
    versions_schema = paths["/api/report-layouts/{layout_id}/versions"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert versions_schema == {
        "items": {"$ref": "#/components/schemas/ReportLayoutVersionSummaryResponse"},
        "type": "array",
        "title": "Response Get Report Layout Versions Api Report Layouts  Layout Id  Versions Get",
    }
    detail_schema = paths["/api/report-layouts/{layout_id}/versions/{version}"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    assert detail_schema == {
        "$ref": "#/components/schemas/ReportLayoutVersionDetailResponse"
    }
    delete_schema = paths["/api/report-layouts/{layout_id}"]["delete"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert delete_schema == {
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


_DEFAULT_STATE = object()


class FakeLayoutRepository:
    def __init__(
        self,
        events: list[str],
        *,
        state: dict[str, Any] | None | object = _DEFAULT_STATE,
        versions: list[dict[str, Any]] | None = None,
    ) -> None:
        self.events = events
        self.state = {"version": 4, "is_system": False} if state is _DEFAULT_STATE else state
        self.versions = [] if versions is None else versions
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_active_layouts(self) -> list[ReportLayout]:
        self.events.append("list")
        return []

    def get_active_state(self, layout_id: str) -> dict[str, Any] | None:
        self.events.append(f"read:{layout_id}")
        return self.state if isinstance(self.state, dict) else None

    def list_versions(self, layout_id: str) -> list[dict[str, Any]]:
        self.events.append(f"versions:{layout_id}")
        return self.versions

    def get_version(self, layout_id: str, version: int) -> dict[str, Any] | None:
        self.events.append(f"version:{layout_id}:{version}")
        return next((item for item in self.versions if item["version"] == version), None)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.events.append("BEGIN")
        try:
            yield
            self.events.append("COMMIT")
        except BaseException:
            self.events.append("ROLLBACK")
            raise

    def insert_layout(self, **values: Any) -> None:
        self.events.append("insert-live")
        self.calls.append(("insert-live", values))

    def update_layout(self, **values: Any) -> None:
        self.events.append("update-live")
        self.calls.append(("update-live", values))

    def insert_version(self, **values: Any) -> None:
        self.events.append("insert-version")
        self.calls.append(("insert-version", values))

    def add_audit(self, audit: dict[str, Any]) -> None:
        self.events.append("audit")
        self.calls.append(("audit", audit))

    def deactivate(self, layout_id: str, occurred_at: datetime) -> None:
        self.events.append(f"deactivate:{layout_id}")
        self.calls.append(("deactivate", {"layout_id": layout_id, "occurred_at": occurred_at}))


def _fake_provider(repository: FakeLayoutRepository, events: list[str]):
    @contextmanager
    def provide() -> Iterator[FakeLayoutRepository]:
        events.append("open")
        yield repository
        events.append("close")

    return provide


def _authorize(events: list[str]):
    def authorize() -> object:
        events.append("authorize")
        return object()

    return authorize


def _audit_context(method: str = "POST") -> dict[str, Any]:
    return {
        "user_id": "actor-1",
        "username": "actor",
        "role": "admin",
        "method": method,
        "path": "/api/report-layouts",
        "request_id": "request-1",
        "client_ip": None,
        "user_agent": "pytest",
    }


@pytest.mark.unit
def test_application_create_authorizes_before_open_and_writes_one_atomic_snapshot() -> None:
    events: list[str] = []
    repository = FakeLayoutRepository(events)
    result = create_report_layout(
        name="  새 보고서  ",
        description="  설명  ",
        definition=_report_payload()["definition"],
        actor_name="실제 사용자",
        audit=_audit_context(),
        authorize=_authorize(events),
        repository_provider=_fake_provider(repository, events),
        id_factory=lambda: "report-layout-fixed",
        clock=lambda: datetime(2026, 1, 2, 3, 4, 5),
    )
    assert events == [
        "authorize",
        "open",
        "BEGIN",
        "insert-live",
        "insert-version",
        "audit",
        "COMMIT",
        "close",
    ]
    assert result["id"] == "report-layout-fixed"
    assert (result["name"], result["description"], result["updated_by"]) == (
        "새 보고서",
        "설명",
        "실제 사용자",
    )
    assert result["definition"]["id"] == "report-layout-fixed"
    assert result["definition"]["version"] == 1
    assert repository.calls[1][1]["definition"] == result["definition"]
    assert repository.calls[2][1]["action"] == "REPORT_LAYOUT_CREATED"


@pytest.mark.unit
def test_application_update_reads_active_state_before_transaction_and_preserves_system_flag() -> None:
    events: list[str] = []
    repository = FakeLayoutRepository(events, state={"version": 7, "is_system": True})
    result = update_report_layout(
        layout_id="layout-1",
        name="수정 보고서",
        description="수정 설명",
        definition=_report_payload()["definition"],
        actor_name="실제 사용자",
        audit=_audit_context("PUT"),
        authorize=_authorize(events),
        repository_provider=_fake_provider(repository, events),
        clock=lambda: datetime(2026, 1, 2, 3, 4, 5),
    )
    assert events == [
        "authorize",
        "open",
        "read:layout-1",
        "BEGIN",
        "update-live",
        "insert-version",
        "audit",
        "COMMIT",
        "close",
    ]
    assert result["version"] == 8 and result["is_system"] is True
    assert repository.calls[0][1]["version"] == 8
    assert repository.calls[1][1]["definition"]["version"] == 8
    assert repository.calls[2][1]["detail"] == {"layout_id": "layout-1", "version": 8}


@pytest.mark.unit
def test_application_delete_guards_and_deactivates_only_user_layouts() -> None:
    missing_events: list[str] = []
    with pytest.raises(ReportLayoutNotFoundError):
        deactivate_report_layout(
            layout_id="missing",
            authorize=_authorize(missing_events),
            repository_provider=_fake_provider(
                FakeLayoutRepository(missing_events, state=None),
                missing_events,
            ),
        )
    assert missing_events == ["authorize", "open", "read:missing"]

    system_events: list[str] = []
    with pytest.raises(SystemReportLayoutDeactivationError):
        deactivate_report_layout(
            layout_id="system",
            authorize=_authorize(system_events),
            repository_provider=_fake_provider(
                FakeLayoutRepository(system_events, state={"version": 1, "is_system": True}),
                system_events,
            ),
        )
    assert not any(event.startswith("deactivate:") for event in system_events)

    user_events: list[str] = []
    result = deactivate_report_layout(
        layout_id="user",
        authorize=_authorize(user_events),
        repository_provider=_fake_provider(FakeLayoutRepository(user_events), user_events),
        clock=lambda: datetime(2026, 1, 2, 3, 4, 5),
    )
    assert result == {"status": "deactivated", "id": "user"}
    assert user_events == ["authorize", "open", "read:user", "deactivate:user", "close"]


@pytest.mark.unit
def test_application_queries_authorize_before_open_and_keep_unknown_contracts() -> None:
    events: list[str] = []
    repository = FakeLayoutRepository(events, versions=[])
    provider = _fake_provider(repository, events)
    assert list_report_layout_versions("unknown", _authorize(events), provider) == []
    assert events == ["authorize", "open", "versions:unknown", "close"]

    events.clear()
    with pytest.raises(ReportLayoutVersionNotFoundError):
        get_report_layout_version("unknown", 99, _authorize(events), provider)
    assert events == ["authorize", "open", "version:unknown:99", "close"]


@pytest.mark.unit
def test_application_validation_is_exact_and_never_opens_persistence() -> None:
    events: list[str] = []
    repository = FakeLayoutRepository(events)
    with pytest.raises(InvalidReportLayoutError) as caught:
        create_report_layout(
            name="오류 보고서",
            description="오류",
            definition=_invalid_definition("cover"),
            actor_name="사용자",
            audit=_audit_context(),
            authorize=_authorize(events),
            repository_provider=_fake_provider(repository, events),
        )
    assert caught.value.message == "지원하지 않는 표지 형식입니다."
    assert events == ["authorize"]


class SQLCursorFake:
    def __init__(self, row: tuple[Any, ...] | None = None) -> None:
        self._row = row
        self.description: list[tuple[str]] = []

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row

    def fetchall(self) -> list[tuple[Any, ...]]:
        return []


class SQLConnectionFake:
    def __init__(self) -> None:
        self.events: list[str] = []

    def execute(self, statement: str, parameters: Any | None = None) -> SQLCursorFake:
        del parameters
        normalized = " ".join(statement.split())
        self.events.append(normalized)
        if normalized.startswith("SELECT version, is_system"):
            return SQLCursorFake((3, False))
        return SQLCursorFake()


@contextmanager
def _sql_repository_provider(repository: SQLReportLayoutRepository):
    yield repository


@pytest.mark.unit
def test_sql_update_reads_before_begin_and_writes_version_audit_commit_without_pg_lock() -> None:
    connection = SQLConnectionFake()
    repository = SQLReportLayoutRepository(connection)  # type: ignore[arg-type]
    events = connection.events
    update_report_layout(
        layout_id="layout-1",
        name="SQL 보고서",
        description="순서",
        definition=_report_payload()["definition"],
        actor_name="사용자",
        audit=_audit_context("PUT"),
        authorize=lambda: events.append("authorize"),
        repository_provider=lambda: _sql_repository_provider(repository),
        clock=lambda: datetime(2026, 1, 2, 3, 4, 5),
    )
    read_index = next(index for index, event in enumerate(events) if event.startswith("SELECT version"))
    begin_index = events.index("BEGIN TRANSACTION")
    live_index = next(index for index, event in enumerate(events) if event.startswith("UPDATE report_layouts"))
    version_index = next(
        index for index, event in enumerate(events) if event.startswith("INSERT INTO report_layout_versions")
    )
    audit_index = next(index for index, event in enumerate(events) if event.startswith("INSERT INTO audit_events"))
    commit_index = events.index("COMMIT")
    assert events[0] == "authorize"
    assert read_index < begin_index < live_index < version_index < audit_index < commit_index
    assert not any("LOCK TABLE" in event for event in events)


@pytest.mark.unit
def test_sql_transaction_rolls_back_exception_and_never_commits() -> None:
    connection = SQLConnectionFake()
    repository = SQLReportLayoutRepository(connection)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="write failed"):
        with repository.transaction():
            connection.events.append("WRITE")
            raise RuntimeError("write failed")
    assert connection.events == ["BEGIN TRANSACTION", "WRITE", "ROLLBACK"]


def _report_payload(name: str = "테스트 보고서") -> dict[str, Any]:
    return {
        "name": name,
        "description": "레이아웃 슬라이스 계약",
        "definition": {
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
            "slides": [
                {
                    "id": "cover",
                    "name": "표지",
                    "kind": "cover",
                    "elements": [
                        {
                            "id": "title",
                            "type": "title",
                            "text": "검증 보고서",
                            "x": 1,
                            "y": 1,
                            "w": 20,
                            "h": 2,
                        }
                    ],
                }
            ],
            "templateSource": "native",
            "templateBindings": {},
            "client_extension": {"nested": ["kept", {"enabled": True}]},
        },
        "updated_by": "클라이언트 위조 이름",
    }


@pytest.mark.contract
def test_report_layout_router_owns_six_routes_in_legacy_registration_order() -> None:
    routes = [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.endpoint.__module__ == reports_router.__name__
    ]
    assert [(route.path, tuple(sorted(route.methods or ()))) for route in routes] == [
        ("/api/report-layouts", ("GET",)),
        ("/api/report-layouts", ("POST",)),
        ("/api/report-layouts/{layout_id}", ("PUT",)),
        ("/api/report-layouts/{layout_id}/versions", ("GET",)),
        ("/api/report-layouts/{layout_id}/versions/{version}", ("GET",)),
        ("/api/report-layouts/{layout_id}", ("DELETE",)),
    ]
    assert [route.endpoint.__name__ for route in routes] == [
        "list_report_layouts",
        "create_report_layout",
        "update_report_layout",
        "get_report_layout_versions",
        "get_report_layout_version",
        "delete_report_layout",
    ]
    expected_operation_ids = [
        "list_report_layouts_api_report_layouts_get",
        "create_report_layout_api_report_layouts_post",
        "update_report_layout_api_report_layouts__layout_id__put",
        "get_report_layout_versions_api_report_layouts__layout_id__versions_get",
        "get_report_layout_version_api_report_layouts__layout_id__versions__version__get",
        "delete_report_layout_api_report_layouts__layout_id__delete",
    ]
    assert [route.operation_id or route.unique_id for route in routes] == expected_operation_ids


@pytest.mark.contract
def test_main_relinquishes_layout_crud_but_keeps_report_template_ownership() -> None:
    source = (Path(__file__).parents[1] / "app" / "main.py").read_text(encoding="utf-8")
    forbidden = (
        "_validated_report_layout",
        "def create_report_layout",
        "def update_report_layout",
        "def get_report_layout_versions",
        "def get_report_layout_version",
        "def delete_report_layout",
        "INSERT INTO report_layouts",
        "UPDATE report_layouts",
        "SELECT * FROM report_layouts",
        "report_layout_versions",
    )
    for token in forbidden:
        assert token not in source
    for token in (
        "REPORT_TEMPLATE_DIR",
        "def list_report_templates",
        "def upload_report_template",
        "def render_report_template",
        "def delete_report_template",
        "report_template_assets",
    ):
        assert token in source


@pytest.mark.contract
def test_duckdb_create_update_preserve_live_snapshot_audit_and_actor_identity() -> None:
    payload = _report_payload()
    with TestClient(app) as client:
        created = client.post("/api/report-layouts", json=payload)
        assert created.status_code == 201, created.text
        created_body = created.json()
        layout_id = created_body["id"]
        assert created_body["updated_by"] == "로컬 관리자"
        assert created_body["definition"]["client_extension"] == payload["definition"]["client_extension"]

        updated_payload = deepcopy(payload)
        updated_payload["name"] = "수정된 보고서"
        updated_payload["definition"]["accentColor"] = "FF9948"
        updated = client.put(f"/api/report-layouts/{layout_id}", json=updated_payload)
        assert updated.status_code == 200, updated.text
        updated_body = updated.json()
        assert updated_body["version"] == 2
        assert updated_body["updated_by"] == "로컬 관리자"

    with connect() as connection:
        live = connection.execute(
            "SELECT name, version, definition_json, updated_by FROM report_layouts WHERE id=?",
            [layout_id],
        ).fetchone()
        versions = connection.execute(
            """
            SELECT version, definition_json, created_by, is_valid
            FROM report_layout_versions WHERE layout_id=? ORDER BY version
            """,
            [layout_id],
        ).fetchall()
        audit_rows = rows(
            connection.execute(
                """
                SELECT action, status_code, detail_json FROM audit_events
                WHERE action IN ('REPORT_LAYOUT_CREATED', 'REPORT_LAYOUT_UPDATED')
                ORDER BY occurred_at
                """
            )
        )
    assert live is not None
    assert (live[0], live[1], live[3]) == ("수정된 보고서", 2, "로컬 관리자")
    assert json_value(live[2]) == updated_body["definition"]
    assert [item[0] for item in versions] == [1, 2]
    assert json_value(versions[0][1]) == created_body["definition"]
    assert json_value(versions[1][1]) == updated_body["definition"]
    assert [(item[2], bool(item[3])) for item in versions] == [
        ("로컬 관리자", True),
        ("로컬 관리자", True),
    ]
    matching_audits = [
        item
        for item in audit_rows
        if (json_value(item["detail_json"]) or {}).get("layout_id") == layout_id
    ]
    assert [(item["action"], item["status_code"]) for item in matching_audits] == [
        ("REPORT_LAYOUT_CREATED", 201),
        ("REPORT_LAYOUT_UPDATED", 200),
    ]
    assert json_value(matching_audits[0]["detail_json"]) == {"layout_id": layout_id}
    assert json_value(matching_audits[1]["detail_json"]) == {
        "layout_id": layout_id,
        "version": 2,
    }


@pytest.mark.contract
def test_duckdb_update_audit_failure_rolls_back_live_version_and_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _report_payload("롤백 검증 보고서")
    with TestClient(app) as client:
        created = client.post("/api/report-layouts", json=payload)
        assert created.status_code == 201, created.text
        layout_id = created.json()["id"]

    def snapshot() -> tuple[tuple[Any, ...], list[tuple[Any, ...]], list[tuple[Any, ...]]]:
        with connect() as connection:
            live = connection.execute(
                """
                SELECT name, description, version, definition_json, updated_at, updated_by, is_active
                FROM report_layouts WHERE id=?
                """,
                [layout_id],
            ).fetchone()
            versions = connection.execute(
                """
                SELECT version, definition_json, created_by, created_at, is_valid
                FROM report_layout_versions WHERE layout_id=? ORDER BY version
                """,
                [layout_id],
            ).fetchall()
            audits = connection.execute(
                """
                SELECT action, status_code, detail_json FROM audit_events
                WHERE action='REPORT_LAYOUT_UPDATED' ORDER BY occurred_at
                """
            ).fetchall()
        assert live is not None
        return live, versions, audits

    before = snapshot()

    def fail_audit(self: SQLReportLayoutRepository, audit: dict[str, Any]) -> None:
        del self, audit
        raise RuntimeError("audit failed")

    monkeypatch.setattr(SQLReportLayoutRepository, "add_audit", fail_audit)
    changed = deepcopy(payload)
    changed["name"] = "롤백되면 안 보이는 이름"
    changed["definition"]["accentColor"] = "FF9948"
    with TestClient(app, raise_server_exceptions=True) as client:
        with pytest.raises(RuntimeError, match="audit failed"):
            client.put(f"/api/report-layouts/{layout_id}", json=changed)
    assert snapshot() == before


@pytest.mark.contract
def test_layout_missing_inactive_version_and_delete_guards_are_exact() -> None:
    payload = _report_payload("삭제 검증 보고서")
    missing_id = f"missing-{uuid4().hex}"
    invalid_layout_id = f"invalid-version-{uuid4().hex}"
    invalid_definition = {
        **payload["definition"],
        "id": invalid_layout_id,
        "name": payload["name"],
        "description": payload["description"],
        "version": 1,
    }
    with connect() as connection:
        connection.execute(
            "INSERT INTO report_layout_versions VALUES (?, 1, ?, 'tester', ?, false)",
            [invalid_layout_id, json.dumps(invalid_definition), datetime(2026, 1, 2, 3, 4, 5)],
        )
    with TestClient(app) as client:
        missing_update = client.put(f"/api/report-layouts/{missing_id}", json=payload)
        assert missing_update.status_code == 404
        assert missing_update.json() == {"detail": "보고서 레이아웃을 찾을 수 없습니다."}
        assert client.get(f"/api/report-layouts/{missing_id}/versions").json() == []
        missing_version = client.get(f"/api/report-layouts/{missing_id}/versions/99")
        assert missing_version.status_code == 404
        assert missing_version.json() == {"detail": "보고서 레이아웃 버전을 찾을 수 없습니다."}
        invalid_version = client.get(f"/api/report-layouts/{invalid_layout_id}/versions/1")
        assert invalid_version.status_code == 404
        assert invalid_version.json() == {"detail": "보고서 레이아웃 버전을 찾을 수 없습니다."}

        system_before = next(
            item for item in client.get("/api/report-layouts").json() if item["id"] == "report-layout-standard"
        )
        protected = client.delete("/api/report-layouts/report-layout-standard")
        assert protected.status_code == 409
        assert protected.json() == {
            "detail": "기본 레이아웃은 삭제할 수 없습니다. 수정하면 새 버전으로 보존됩니다."
        }
        system_after = next(
            item for item in client.get("/api/report-layouts").json() if item["id"] == "report-layout-standard"
        )
        assert system_after == system_before

        created = client.post("/api/report-layouts", json=payload)
        assert created.status_code == 201, created.text
        layout_id = created.json()["id"]
        assert layout_id in {item["id"] for item in client.get("/api/report-layouts").json()}
        deleted = client.delete(f"/api/report-layouts/{layout_id}")
        assert deleted.status_code == 200
        assert deleted.json() == {"status": "deactivated", "id": layout_id}
        assert layout_id not in {item["id"] for item in client.get("/api/report-layouts").json()}
        assert client.delete(f"/api/report-layouts/{layout_id}").status_code == 404
        assert client.put(f"/api/report-layouts/{layout_id}", json=payload).status_code == 404

    with connect() as connection:
        stored = connection.execute(
            "SELECT is_active FROM report_layouts WHERE id=?",
            [layout_id],
        ).fetchone()
    assert stored is not None and not bool(stored[0])


def _invalid_definition(case: str) -> dict[str, Any]:
    definition = deepcopy(_report_payload()["definition"])
    if case == "cover":
        definition["coverVariant"] = "unknown"
    elif case == "sections":
        definition["sectionOrder"] = ["series", "series", "media"]
    elif case == "hex":
        definition["accentColor"] = "#1898D5"
    elif case == "master-design":
        definition["slideMaster"] = {"design": "unknown"}
    elif case == "master-background":
        definition["slideMaster"] = {
            "design": "plain",
            "backgroundColor": "bad",
            "accentColor": "1898D5",
        }
    elif case == "master-accent":
        definition["slideMaster"] = {
            "design": "plain",
            "backgroundColor": "FFFFFF",
            "accentColor": "bad",
        }
    elif case == "placements-array":
        definition["variablePlacements"] = None
    elif case == "placement-item":
        definition["variablePlacements"] = [
            {"variableKey": "", "presentation": "unknown", "order": 0}
        ]
    elif case == "slides-nonempty":
        definition["slides"] = []
    elif case == "slide-id-kind":
        definition["slides"] = [{"id": "", "kind": "unknown", "elements": []}]
    elif case == "slide-duplicate":
        definition["slides"].append(deepcopy(definition["slides"][0]))
    elif case == "style-use-master":
        definition["slides"][0]["style"] = {"useMaster": "yes"}
    elif case == "style-design":
        definition["slides"][0]["style"] = {"useMaster": True, "design": "unknown"}
    elif case == "style-background":
        definition["slides"][0]["style"] = {"useMaster": True, "backgroundColor": "bad"}
    elif case == "style-accent":
        definition["slides"][0]["style"] = {"useMaster": True, "accentColor": "bad"}
    elif case == "elements-array":
        definition["slides"][0]["elements"] = None
    elif case == "element-type":
        definition["slides"][0]["elements"][0]["type"] = "unknown"
    elif case == "element-duplicate":
        definition["slides"][0]["elements"].append(
            deepcopy(definition["slides"][0]["elements"][0])
        )
    elif case == "element-text":
        definition["slides"][0]["elements"][0]["text"] = 1
    elif case in {"coordinate-x", "coordinate-w", "coordinate-y", "coordinate-h"}:
        definition["slides"][0]["elements"][0][case[-1]] = -1
    elif case == "bounds":
        definition["slides"][0]["elements"][0].update({"x": 31, "w": 2})
    elif case == "template-source":
        definition["templateSource"] = "unknown"
    elif case == "pptx-asset":
        definition["templateSource"] = "pptx_upload"
    elif case == "template-bindings":
        definition["templateBindings"] = []
    return definition


VALIDATION_CASES = [
    ("cover", "지원하지 않는 표지 형식입니다."),
    ("sections", "근거 페이지 순서는 series, scalar, media를 한 번씩 포함해야 합니다."),
    ("hex", "강조색은 6자리 HEX 색상이어야 합니다."),
    ("master-design", "슬라이드 마스터에는 올바른 디자인이 필요합니다."),
    ("master-background", "슬라이드 마스터 backgroundColor은 6자리 HEX 색상이어야 합니다."),
    ("master-accent", "슬라이드 마스터 accentColor은 6자리 HEX 색상이어야 합니다."),
    ("placements-array", "variablePlacements 배열이 필요합니다."),
    ("placement-item", "변수 배치에는 variableKey와 올바른 presentation이 필요합니다."),
    ("slides-nonempty", "slides는 1개 이상의 배열이어야 합니다."),
    ("slide-id-kind", "각 슬라이드에는 고유 id와 올바른 kind가 필요합니다."),
    ("slide-duplicate", "슬라이드 id는 중복될 수 없습니다."),
    ("style-use-master", "슬라이드 스타일에는 useMaster 불리언 값이 필요합니다."),
    ("style-design", "지원하지 않는 슬라이드 디자인입니다."),
    ("style-background", "슬라이드 backgroundColor은 6자리 HEX 색상이어야 합니다."),
    ("style-accent", "슬라이드 accentColor은 6자리 HEX 색상이어야 합니다."),
    ("elements-array", "슬라이드 elements는 80개 이하의 배열이어야 합니다."),
    ("element-type", "지원하지 않는 보고서 위젯 형식입니다."),
    ("element-duplicate", "슬라이드 안의 위젯 id는 고유해야 합니다."),
    ("element-text", "텍스트 상자 내용은 문자열이어야 합니다."),
    ("coordinate-x", "위젯 x 좌표가 캔버스 범위를 벗어났습니다."),
    ("coordinate-w", "위젯 w 좌표가 캔버스 범위를 벗어났습니다."),
    ("coordinate-y", "위젯 y 좌표가 캔버스 범위를 벗어났습니다."),
    ("coordinate-h", "위젯 h 좌표가 캔버스 범위를 벗어났습니다."),
    ("bounds", "위젯 영역이 슬라이드 경계를 벗어났습니다."),
    ("template-source", "지원하지 않는 템플릿 원본 형식입니다."),
    ("pptx-asset", "업로드 PPTX 템플릿 ID가 필요합니다."),
    ("template-bindings", "templateBindings는 객체여야 합니다."),
]


@pytest.mark.parametrize(("case", "detail"), VALIDATION_CASES)
@pytest.mark.unit
def test_report_layout_policy_preserves_every_validation_message(case: str, detail: str) -> None:
    with pytest.raises(InvalidReportLayoutError) as caught:
        validate_report_layout(_invalid_definition(case))
    assert caught.value.message == detail


@pytest.mark.parametrize(
    ("case", "detail"),
    [
        VALIDATION_CASES[0],
        VALIDATION_CASES[1],
        VALIDATION_CASES[2],
        VALIDATION_CASES[10],
        VALIDATION_CASES[17],
        VALIDATION_CASES[23],
        VALIDATION_CASES[25],
    ],
)
@pytest.mark.contract
def test_report_layout_validation_preserves_exact_boundary_details(case: str, detail: str) -> None:
    payload = _report_payload()
    payload["definition"] = _invalid_definition(case)
    with TestClient(app) as client:
        response = client.post("/api/report-layouts", json=payload)
    assert response.status_code == 422
    assert response.json() == {"detail": detail}


@pytest.mark.contract
def test_report_layout_accepts_extensions_and_more_than_forty_slides() -> None:
    payload = _report_payload("마흔한 장 보고서")
    payload["definition"]["slides"] = [
        {
            "id": f"slide-{index}",
            "name": f"슬라이드 {index}",
            "kind": "custom",
            "elements": [],
            "slide_extension": {"index": index},
        }
        for index in range(41)
    ]
    with TestClient(app) as client:
        response = client.post("/api/report-layouts", json=payload)
    assert response.status_code == 201, response.text
    definition = response.json()["definition"]
    assert len(definition["slides"]) == 41
    assert definition["client_extension"] == payload["definition"]["client_extension"]
    assert definition["slides"][40]["slide_extension"] == {"index": 40}
    assert definition["variablePlacements"][0]["placement_extension"] == {"visible": True}


def _insert_password_user(*, is_global_admin: bool, display_name: str) -> tuple[str, str, str]:
    suffix = uuid4().hex[:10]
    user_id = f"report-user-{suffix}"
    username = f"report-{suffix}"
    password = "correct-horse-battery-staple"
    now = datetime(2026, 1, 2, 3, 4, 5)
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO users
                (id, username, password_hash, display_name, legacy_role, is_active,
                 created_at, updated_at, account_status, is_global_admin)
            VALUES (?, ?, ?, ?, 'viewer', true, ?, ?, 'ACTIVE', ?)
            """,
            [user_id, username, hash_password(password), display_name, now, now, is_global_admin],
        )
    return user_id, username, password


def _login_headers(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.contract
def test_password_active_general_can_read_history_but_mutations_require_global_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "report-layout-secret-key-at-least-32-characters")
    _, username, password = _insert_password_user(
        is_global_admin=False,
        display_name="일반 사용자",
    )
    payload = _report_payload("권한 없는 보고서")
    readable_layout_id = f"readable-{uuid4().hex}"
    readable_definition = {
        **payload["definition"],
        "id": readable_layout_id,
        "name": payload["name"],
        "description": payload["description"],
        "version": 1,
    }
    with connect() as connection:
        connection.execute(
            "INSERT INTO report_layout_versions VALUES (?, 1, ?, 'tester', ?, true)",
            [readable_layout_id, json.dumps(readable_definition), datetime(2026, 1, 2, 3, 4, 5)],
        )
    with TestClient(app) as client:
        headers = _login_headers(client, username, password)
        catalog = client.get("/api/report-layouts", headers=headers)
        versions = client.get(
            f"/api/report-layouts/{readable_layout_id}/versions",
            headers=headers,
        )
        detail = client.get(
            f"/api/report-layouts/{readable_layout_id}/versions/1",
            headers=headers,
        )
        assert catalog.status_code == 200 and catalog.json()
        assert versions.status_code == 200 and versions.json()
        assert detail.status_code == 200
        assert client.post("/api/report-layouts", headers=headers, json=payload).status_code == 403
        assert client.put(
            "/api/report-layouts/report-layout-standard",
            headers=headers,
            json=payload,
        ).status_code == 403
        assert client.delete(
            "/api/report-layouts/report-layout-standard",
            headers=headers,
        ).status_code == 403


@pytest.mark.contract
def test_password_admin_demotion_after_login_is_fresh_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "report-layout-secret-key-at-least-32-characters")
    user_id, username, password = _insert_password_user(
        is_global_admin=True,
        display_name="처음에는 관리자",
    )
    with TestClient(app) as client:
        headers = _login_headers(client, username, password)
        with connect() as connection:
            before = connection.execute("SELECT count(*) FROM report_layouts").fetchone()[0]
            connection.execute(
                "UPDATE users SET is_global_admin=false WHERE id=?",
                [user_id],
            )
        provider_opens: list[str] = []

        class FailIfOpened:
            def __call__(self):
                provider_opens.append("open")
                raise AssertionError("persistence opened after authorization denial")

        monkeypatch.setattr(
            reports_router,
            "SQLReportLayoutRepositoryProvider",
            lambda: FailIfOpened(),
        )
        denied_create = client.post(
            "/api/report-layouts",
            headers=headers,
            json=_report_payload("강등 후 보고서"),
        )
        denied_update = client.put(
            "/api/report-layouts/report-layout-standard",
            headers=headers,
            json=_report_payload("강등 후 수정"),
        )
        denied_delete = client.delete(
            "/api/report-layouts/report-layout-standard",
            headers=headers,
        )
        assert [denied_create.status_code, denied_update.status_code, denied_delete.status_code] == [
            403,
            403,
            403,
        ]
        assert provider_opens == []
    with connect() as connection:
        after = connection.execute("SELECT count(*) FROM report_layouts").fetchone()[0]
    assert after == before
