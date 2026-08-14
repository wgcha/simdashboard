from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import cast

import pytest
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient

from app.application.projects.commands import create_project
from app.application.projects.queries import list_projects
from app.adapters.persistence import projects as project_persistence
from app.database_connection import connect, rows
from app.domains.projects.models import (
    CreateProjectCommand,
    PersistedProjectAuditRecord,
    Project,
    ProjectAuditContext,
)
from app.domains.projects.ports import ProjectRepository, ProjectUnitOfWork
from app.main import app


class FakeProjectRepository:
    def __init__(self, projects: list[Project]) -> None:
        self._projects = projects
        self.listed = False

    def list_projects(self) -> list[Project]:
        self.listed = True
        return self._projects


CREATE_COMMAND: CreateProjectCommand = {
    "name": "  Project name  ",
    "product_name": "  Product A  ",
    "description": "  Description  ",
    "manufacturer": "  Manufacturer A  ",
    "display_size_inch": 55.0,
    "creator": {"user_id": "user-a", "username": "alice", "role": "admin"},
}
AUDIT: ProjectAuditContext = {
    "user_id": "user-a",
    "username": "alice",
    "role": "admin",
    "action": "PROJECT_CREATED",
    "method": "POST",
    "path": "/api/projects",
    "status_code": 201,
    "request_id": "request-a",
    "client_ip": "127.0.0.1",
    "user_agent": "pytest",
}


class FakeProjectUnitOfWork:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.audit: PersistedProjectAuditRecord | None = None

    def add_project(self, project_id: str, command: CreateProjectCommand, created_at: datetime) -> None:
        self.events.append(f"project:{project_id}")

    def add_product_information(self, project_id: str, command: CreateProjectCommand) -> None:
        self.events.append("products")

    def add_quality_thresholds(self, project_id: str, created_at: datetime) -> None:
        self.events.append("thresholds")

    def add_workspace_layouts(self, project_id: str) -> None:
        self.events.append("layouts")

    def add_admin_membership(
        self,
        membership_id: str,
        project_id: str,
        command: CreateProjectCommand,
        created_at: datetime,
    ) -> None:
        self.events.append(f"membership:{membership_id}")

    def add_audit_event(self, record: PersistedProjectAuditRecord) -> None:
        self.events.append("audit")
        self.audit = record


@pytest.mark.unit
def test_list_projects_uses_port_and_closes_provider() -> None:
    project: Project = {
        "id": "project-a",
        "name": "A",
        "product_name": "Product A",
        "description": None,
        "created_at": datetime(2025, 1, 1),
    }
    repository = FakeProjectRepository([project])
    closed = False
    events: list[str] = []

    def authorize() -> object:
        events.append("authorize")
        return object()

    @contextmanager
    def provider() -> Iterator[ProjectRepository]:
        nonlocal closed
        events.append("open")
        try:
            yield repository
        finally:
            closed = True

    assert list_projects(authorize, provider) == [project]
    assert events == ["authorize", "open"]
    assert repository.listed
    assert closed


@pytest.mark.unit
def test_list_projects_does_not_open_repository_when_authorization_is_denied() -> None:
    provider_opened = False

    def deny() -> object:
        raise PermissionError("denied")

    @contextmanager
    def provider() -> Iterator[ProjectRepository]:
        nonlocal provider_opened
        provider_opened = True
        yield FakeProjectRepository([])

    with pytest.raises(PermissionError, match="denied"):
        list_projects(deny, provider)

    assert not provider_opened


@pytest.mark.unit
def test_create_project_authorizes_before_factories_and_unit_of_work() -> None:
    events: list[str] = []

    def deny() -> object:
        events.append("authorize")
        raise PermissionError("denied")

    def id_factory(prefix: str, length: int) -> str:
        events.append("id")
        return f"{prefix}-id"

    def clock() -> datetime:
        events.append("clock")
        return datetime(2025, 1, 1)

    @contextmanager
    def provider() -> Iterator[ProjectUnitOfWork]:
        events.append("open")
        yield FakeProjectUnitOfWork(events)

    with pytest.raises(PermissionError, match="denied"):
        create_project(CREATE_COMMAND, AUDIT, deny, id_factory, clock, provider)

    assert events == ["authorize"]


@pytest.mark.unit
def test_create_project_orders_atomic_writes_and_preserves_raw_response() -> None:
    events: list[str] = []
    project_created_at = datetime(2025, 1, 1, 10, 0)
    audit_created_at = datetime(2025, 1, 1, 10, 1)
    timestamps = iter([project_created_at, audit_created_at])
    unit_of_work = FakeProjectUnitOfWork(events)

    def authorize() -> object:
        events.append("authorize")
        return object()

    def id_factory(prefix: str, length: int) -> str:
        events.append(f"id:{prefix}:{length}")
        return f"{prefix}-fixed"

    def clock() -> datetime:
        events.append("clock")
        return next(timestamps)

    @contextmanager
    def provider() -> Iterator[ProjectUnitOfWork]:
        events.append("open")
        yield unit_of_work

    result = create_project(CREATE_COMMAND, AUDIT, authorize, id_factory, clock, provider)

    assert events == [
        "authorize",
        "id:project:12",
        "clock",
        "id:membership:20",
        "id::0",
        "open",
        "project:project-fixed",
        "products",
        "thresholds",
        "layouts",
        "membership:membership-fixed",
        "clock",
        "audit",
    ]
    assert result == {
        "id": "project-fixed",
        "name": "  Project name  ",
        "product_name": "  Product A  ",
        "description": "  Description  ",
        "manufacturer": "  Manufacturer A  ",
        "display_size_inch": 55.0,
        "created_at": project_created_at,
    }
    assert unit_of_work.audit is not None
    assert unit_of_work.audit["occurred_at"] == audit_created_at
    assert unit_of_work.audit["project_id"] == "project-fixed"


@pytest.mark.contract
def test_projects_endpoint_preserves_read_contract() -> None:
    with TestClient(app) as client:
        response = client.get("/api/projects")
    with connect() as connection:
        expected = rows(connection.execute("SELECT * FROM projects ORDER BY created_at DESC"))

    assert response.status_code == 200
    payload = response.json()
    assert payload
    assert payload == jsonable_encoder(expected)


@pytest.mark.contract
def test_create_project_http_contract_and_required_rows() -> None:
    payload = {
        "name": "  Vertical Slice Project  ",
        "product_name": "  Slice Product  ",
        "description": "  raw response whitespace  ",
        "manufacturer": "  Slice Manufacturer  ",
        "display_size_inch": 55,
    }
    with TestClient(app) as client:
        response = client.post("/api/projects", json=payload)
    assert response.status_code == 201
    created = response.json()
    assert created == {"id": created["id"], **payload, "display_size_inch": 55.0, "created_at": created["created_at"]}
    project_id = created["id"]
    with connect() as connection:
        project = connection.execute(
            "SELECT name, product_name, description FROM projects WHERE id=?", [project_id]
        ).fetchone()
        products = connection.execute(
            "SELECT category, value_text FROM product_information WHERE project_id=? ORDER BY category", [project_id]
        ).fetchall()
        threshold_count = connection.execute(
            "SELECT count(*) FROM quality_thresholds WHERE project_id=?", [project_id]
        ).fetchone()[0]
        layout_count = connection.execute(
            "SELECT count(*) FROM project_workspace_layouts WHERE project_id=?", [project_id]
        ).fetchone()[0]
        membership = connection.execute(
            "SELECT user_id, role FROM project_memberships WHERE project_id=?", [project_id]
        ).fetchone()
        audit = connection.execute(
            "SELECT action, detail_json FROM audit_events WHERE action='PROJECT_CREATED' "
            "AND json_extract_string(detail_json, '$.project_id')=?",
            [project_id],
        ).fetchone()
    assert project == ("Vertical Slice Project", "Slice Product", "raw response whitespace")
    assert products == [
        ("MANUFACTURER", "Slice Manufacturer"),
        ("MODEL", "Slice Product"),
        ("SPEC", "55 inch"),
    ]
    assert threshold_count == 2
    assert layout_count >= 2
    assert membership == ("local-admin", "admin")
    assert audit and audit[0] == "PROJECT_CREATED"


@pytest.mark.contract
def test_create_project_repairs_existing_project_defaults() -> None:
    with connect() as connection:
        connection.execute(
            "DELETE FROM quality_thresholds WHERE project_id='project-tv-001' AND criterion_key='open_cell_stress_mpa'"
        )
        connection.execute(
            "DELETE FROM project_workspace_layouts WHERE project_id='project-tv-001' AND layout_kind='portfolio'"
        )

    with TestClient(app) as client:
        response = client.post(
            "/api/projects",
            json={"name": "Repair trigger", "product_name": "Repair product"},
        )

    assert response.status_code == 201
    with connect() as connection:
        assert connection.execute(
            "SELECT count(*) FROM quality_thresholds WHERE project_id='project-tv-001' AND criterion_key='open_cell_stress_mpa'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM project_workspace_layouts WHERE project_id='project-tv-001' AND layout_kind='portfolio'"
        ).fetchone()[0] == 1


@pytest.mark.contract
def test_create_project_rolls_back_all_rows_when_failure_follows_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = "project-rollback"
    original_add_audit = project_persistence.SQLProjectUnitOfWork.add_audit_event
    generated_counts: dict[str, int] = {}

    def fail_after_audit(
        unit_of_work: project_persistence.SQLProjectUnitOfWork,
        record: PersistedProjectAuditRecord,
    ) -> None:
        original_add_audit(unit_of_work, record)
        raise RuntimeError("injected after audit")

    monkeypatch.setattr(project_persistence.SQLProjectUnitOfWork, "add_audit_event", fail_after_audit)

    def fixed_identifier(prefix: str, length: int) -> str:
        if prefix == "project":
            return project_id
        generated_counts[prefix] = generated_counts.get(prefix, 0) + 1
        return f"{prefix or 'audit'}-rollback-{generated_counts[prefix]}"

    monkeypatch.setattr(
        "app.adapters.http.routers.projects.new_identifier",
        fixed_identifier,
    )

    with TestClient(app) as client:
        with pytest.raises(RuntimeError, match="injected after audit"):
            client.post(
                "/api/projects",
                json={
                    "name": "Rollback project",
                    "product_name": "Rollback product",
                    "manufacturer": "Rollback manufacturer",
                    "display_size_inch": 55,
                },
            )

    with connect() as connection:
        counts = {
            "projects": connection.execute(
                "SELECT count(*) FROM projects WHERE id=?", [project_id]
            ).fetchone()[0],
            **{
                table: connection.execute(
                    f"SELECT count(*) FROM {table} WHERE project_id=?", [project_id]
                ).fetchone()[0]
                for table in (
                "product_information",
                "quality_thresholds",
                "project_workspace_layouts",
                "project_workspace_layout_versions",
                "project_memberships",
                )
            },
        }
        audit_count = connection.execute(
            "SELECT count(*) FROM audit_events "
            "WHERE json_extract_string(detail_json, '$.project_id')=?",
            [project_id],
        ).fetchone()[0]

    assert counts == {table: 0 for table in counts}
    assert audit_count == 0
