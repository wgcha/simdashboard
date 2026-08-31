from __future__ import annotations

import ast
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.adapters.http.routers import project_invitations as invitation_router
from app.adapters.persistence import project_invitations as invitation_persistence
from app.adapters.persistence.project_invitations import (
    SQLInvitationCancelUnitOfWorkProvider,
    SQLInvitationCompleteUnitOfWorkProvider,
    SQLInvitationCreateUnitOfWorkProvider,
)
from app.application.project_invitations.invitations import (
    cancel_invitation,
    complete_invitation,
    create_invitation,
    search_directory,
)
from app.database import initialize_database
from app.database_connection import connect
from app.domains.project_invitations.models import (
    AlreadyProjectMemberError,
    DirectoryEmployee,
    DirectoryEmployeeNotFoundError,
    DirectoryQueryTooShortError,
    DirectoryUnavailableError,
    InactiveEmployeeError,
    InvitationAccountNotReadyError,
    InvitationAlreadyExistsError,
    InvitationAlreadyFinalError,
    InvitationNotFoundError,
    InvitationStatus,
    PersistedProjectInvitation,
    ProjectInvitationAuditContext,
    ProjectNotFoundError,
)
from app.domains.project_invitations.ports import (
    DirectoryGateway,
    InvitationAuditWriter,
    InvitationCancelUnitOfWork,
    InvitationCompleteUnitOfWork,
    InvitationCreateUnitOfWork,
    ProjectInvitationReader,
)
from app.main import app
from scripts.check_openapi_contract import check_contract


AUDIT: ProjectInvitationAuditContext = {
    "user_id": "actor-1",
    "username": "actor",
    "role": "admin",
    "method": "POST",
    "path": "/api/projects/project-1/invitations",
    "request_id": "request-1",
    "client_ip": "127.0.0.1",
    "user_agent": "pytest",
}
NOW = datetime(2026, 1, 2, 3, 4, 5)
EMPLOYEE: DirectoryEmployee = {
    "employee_id": "E90210",
    "display_name": "초대 대상",
    "department": "해석팀",
    "job_title": "연구원",
    "email": "invitee@example.test",
    "employment_status": "ACTIVE",
}


def _invitation(*, status: InvitationStatus = "READY") -> PersistedProjectInvitation:
    return {
        "id": "invitation-1",
        "project_id": "project-1",
        "employee_id": EMPLOYEE["employee_id"],
        "display_name_snapshot": EMPLOYEE["display_name"],
        "department_snapshot": EMPLOYEE["department"],
        "desired_role": "power",
        "status": status,
        "resolved_user_id": "user-1",
        "invited_by": "actor-1",
        "invited_at": NOW,
        "resolved_by": None,
        "resolved_at": None,
        "cancelled_by": None,
        "cancelled_at": None,
    }


class FakeReader:
    def __init__(self, events: list[str], *, exists: bool = True) -> None:
        self.events = events
        self.exists = exists

    def project_exists(self, project_id: str) -> bool:
        self.events.append(f"project:{project_id}")
        return self.exists

    def authorize_create(self, project_id: str) -> None:
        self.events.append(f"authorize:{project_id}")

    def list_invitations(self, project_id: str) -> list[PersistedProjectInvitation]:
        self.events.append(f"list:{project_id}")
        return []


class FakeDirectory:
    def __init__(self, events: list[str], item: DirectoryEmployee | None = EMPLOYEE) -> None:
        self.events = events
        self.item = item

    def search(self, query: str, limit: int) -> list[DirectoryEmployee]:
        self.events.append(f"directory.search:{query}:{limit}")
        return [EMPLOYEE]

    def get_by_employee_id(self, employee_id: str) -> DirectoryEmployee | None:
        self.events.append(f"directory.get:{employee_id}")
        return self.item


class FakeAuditWriter:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.record: dict[str, object] | None = None

    def add_audit(self, audit: dict[str, object]) -> None:
        self.events.append("audit")
        self.record = audit


class FakeInvitationUow:
    def __init__(
        self,
        events: list[str],
        *,
        user: tuple[str, str] | None = ("user-1", "ACTIVE"),
        membership: bool = False,
        open_invitation: bool = False,
        invitation: PersistedProjectInvitation | None = None,
    ) -> None:
        self.events = events
        self.user = user
        self.membership = membership
        self.open_invitation = open_invitation
        self.invitation = invitation
        self.audit: dict[str, object] | None = None

    def authorize_create(self, project_id: str) -> None:
        self.events.append(f"authorize-create:{project_id}")

    def authorize_manage(self, project_id: str) -> None:
        self.events.append(f"authorize-manage:{project_id}")

    def user_for_employee(self, employee_id: str) -> tuple[str, str] | None:
        self.events.append(f"user:{employee_id}")
        return self.user

    def membership_exists(self, project_id: str, user_id: str) -> bool:
        self.events.append(f"member:{project_id}:{user_id}")
        return self.membership

    def open_invitation_exists(self, project_id: str, employee_id: str) -> bool:
        self.events.append(f"open:{project_id}:{employee_id}")
        return self.open_invitation

    def add_invitation(
        self,
        invitation_id: str,
        project_id: str,
        employee: DirectoryEmployee,
        desired_role: str,
        status: str,
        resolved_user_id: str | None,
        actor_id: str,
        occurred_at: datetime,
    ) -> None:
        self.events.append(f"insert:{invitation_id}:{status}")
        self.invitation = {
            **_invitation(status=status),
            "id": invitation_id,
            "project_id": project_id,
            "employee_id": employee["employee_id"],
            "desired_role": desired_role,  # type: ignore[typeddict-item]
            "resolved_user_id": resolved_user_id,
            "invited_by": actor_id,
            "invited_at": occurred_at,
        }

    def find_invitation(
        self, invitation_id: str, project_id: str | None = None
    ) -> PersistedProjectInvitation | None:
        self.events.append(f"reread:{invitation_id}:{project_id}")
        return self.invitation

    def add_membership(
        self,
        membership_id: str,
        project_id: str,
        user_id: str,
        role: str,
        actor_id: str,
        occurred_at: datetime,
    ) -> None:
        self.events.append(f"membership:{membership_id}:{user_id}:{role}")

    def complete_invitation(
        self,
        invitation_id: str,
        project_id: str,
        user_id: str,
        actor_id: str,
        occurred_at: datetime,
    ) -> None:
        self.events.append(f"complete:{invitation_id}:{user_id}")
        assert self.invitation is not None
        self.invitation = {**self.invitation, "status": "COMPLETED", "resolved_user_id": user_id}

    def cancel_invitation(self, invitation_id: str, actor_id: str, occurred_at: datetime) -> None:
        self.events.append(f"cancel:{invitation_id}")
        assert self.invitation is not None
        self.invitation = {**self.invitation, "status": "CANCELLED"}

    def add_audit(self, audit: dict[str, object]) -> None:
        self.events.append("audit")
        self.audit = audit


def _reader_provider(reader: FakeReader, events: list[str]):
    @contextmanager
    def provider() -> Iterator[ProjectInvitationReader]:
        events.append("reader.open")
        try:
            yield reader
        finally:
            events.append("reader.close")

    return provider


def _audit_provider(writer: FakeAuditWriter, events: list[str]):
    @contextmanager
    def provider() -> Iterator[InvitationAuditWriter]:
        events.append("audit.open")
        try:
            yield writer
        finally:
            events.append("audit.close")

    return provider


def _uow_provider(uow: FakeInvitationUow, events: list[str], label: str = "uow"):
    @contextmanager
    def provider(_project_id: str) -> Iterator[Any]:
        events.append(f"{label}.open")
        try:
            yield uow
        finally:
            events.append(f"{label}.close")

    return provider


@pytest.mark.unit
def test_search_directory_orders_project_auth_external_then_separate_audit() -> None:
    events: list[str] = []
    reader = FakeReader(events)
    writer = FakeAuditWriter(events)
    result = search_directory(
        "project-1", "  E9  ", 20, FakeDirectory(events), AUDIT,
        _reader_provider(reader, events), _audit_provider(writer, events),
    )

    assert result == [EMPLOYEE]
    assert events == [
        "reader.open", "project:project-1", "authorize:project-1", "reader.close",
        "directory.search:E9:20", "audit.open", "audit", "audit.close",
    ]
    assert writer.record == {
        **AUDIT,
        "action": "DIRECTORY_SEARCHED",
        "status_code": 200,
        "detail": {"project_id": "project-1", "result_count": 1},
    }


@pytest.mark.unit
def test_search_directory_short_query_is_typed_and_does_not_open_reader() -> None:
    events: list[str] = []
    with pytest.raises(DirectoryQueryTooShortError):
        search_directory(
            "project-1", "  ", 20, FakeDirectory(events), AUDIT,
            _reader_provider(FakeReader(events), events),
            _audit_provider(FakeAuditWriter(events), events),
        )
    assert events == []


@pytest.mark.unit
def test_create_invitation_orders_preflight_external_fresh_authorize_state_checks_audit_reread() -> None:
    events: list[str] = []
    reader = FakeReader(events)
    uow = FakeInvitationUow(events)
    result = create_invitation(
        "project-1", " E90210 ", "power", "actor-1", AUDIT, FakeDirectory(events),
        _reader_provider(reader, events), _uow_provider(uow, events),
        id_factory=lambda prefix, length: f"{prefix}-fixed-{length}", clock=lambda: NOW,
    )

    assert result["status"] == "READY"
    assert events == [
        "reader.open", "project:project-1", "authorize:project-1", "reader.close",
        "directory.get:E90210", "uow.open", "authorize-create:project-1",
        "user:E90210", "member:project-1:user-1", "open:project-1:E90210",
        "insert:invitation-fixed-16:READY", "audit", "reread:invitation-fixed-16:None",
        "uow.close",
    ]
    assert uow.audit == {
        **AUDIT,
        "action": "PROJECT_INVITATION_CREATED",
        "status_code": 201,
        "detail": {"project_id": "project-1", "target_employee_id": "E9***0"},
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("directory_item", "uow", "error"),
    [
        (None, FakeInvitationUow([]), DirectoryEmployeeNotFoundError),
        ({**EMPLOYEE, "employment_status": "INACTIVE"}, FakeInvitationUow([]), InactiveEmployeeError),
        (EMPLOYEE, FakeInvitationUow([], membership=True), AlreadyProjectMemberError),
        (EMPLOYEE, FakeInvitationUow([], open_invitation=True), InvitationAlreadyExistsError),
    ],
)
def test_create_invitation_preserves_typed_guards(
    directory_item: DirectoryEmployee | None,
    uow: FakeInvitationUow,
    error: type[Exception],
) -> None:
    events: list[str] = []
    uow.events = events
    with pytest.raises(error):
        create_invitation(
            "project-1", EMPLOYEE["employee_id"], "general", "actor-1", AUDIT,
            FakeDirectory(events, directory_item), _reader_provider(FakeReader(events), events),
            _uow_provider(uow, events), clock=lambda: NOW,
        )
    if directory_item is None or directory_item["employment_status"] == "INACTIVE":
        assert "uow.open" not in events


@pytest.mark.unit
def test_complete_invitation_orders_ready_account_membership_status_audit_reread() -> None:
    events: list[str] = []
    uow = FakeInvitationUow(events, invitation=_invitation())
    result = complete_invitation(
        "project-1", "invitation-1", "actor-1", AUDIT, _uow_provider(uow, events, "complete"),
        id_factory=lambda prefix, length: f"{prefix}-fixed-{length}", clock=lambda: NOW,
    )

    assert result["status"] == "COMPLETED"
    assert events == [
        "complete.open", "authorize-manage:project-1", "reread:invitation-1:project-1",
        "user:E90210", "member:project-1:user-1", "membership:membership-fixed-16:user-1:power",
        "complete:invitation-1:user-1", "audit", "reread:invitation-1:project-1", "complete.close",
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("invitation", "uow_kwargs", "error"),
    [
        (None, {}, InvitationNotFoundError),
        (_invitation(status="PENDING_ACCOUNT"), {}, InvitationAccountNotReadyError),
        (_invitation(status="COMPLETED"), {}, InvitationAlreadyFinalError),
        (_invitation(), {"user": None}, InvitationAccountNotReadyError),
        (_invitation(), {"membership": True}, AlreadyProjectMemberError),
    ],
)
def test_complete_invitation_preserves_typed_guards(
    invitation: PersistedProjectInvitation | None,
    uow_kwargs: dict[str, object],
    error: type[Exception],
) -> None:
    events: list[str] = []
    uow = FakeInvitationUow(events, invitation=invitation, **uow_kwargs)
    with pytest.raises(error):
        complete_invitation("project-1", "invitation-1", "actor-1", AUDIT, _uow_provider(uow, events))
    assert "audit" not in events


@pytest.mark.unit
def test_cancel_invitation_success_and_final_guard() -> None:
    events: list[str] = []
    uow = FakeInvitationUow(events, invitation=_invitation(status="PENDING_ACCOUNT"))
    assert cancel_invitation(
        "project-1", "invitation-1", "actor-1", AUDIT, _uow_provider(uow, events, "cancel"), clock=lambda: NOW
    ) == {"status": "CANCELLED", "id": "invitation-1"}
    assert events == [
        "cancel.open", "authorize-create:project-1", "reread:invitation-1:project-1",
        "cancel:invitation-1", "audit", "cancel.close",
    ]

    final_events: list[str] = []
    final = FakeInvitationUow(final_events, invitation=_invitation(status="CANCELLED"))
    with pytest.raises(InvitationAlreadyFinalError):
        cancel_invitation("project-1", "invitation-1", "actor-1", AUDIT, _uow_provider(final, final_events))
    assert final_events == [
        "uow.open", "authorize-create:project-1", "reread:invitation-1:project-1", "uow.close"
    ]


class TrackingConnection:
    def __init__(self, *, project_exists: bool = True) -> None:
        self.project_exists = project_exists
        self.events: list[str] = []

    def execute(self, statement: str, _parameters: object = None):
        normalized = " ".join(statement.split())
        self.events.append(normalized)
        if normalized.startswith("SELECT 1 FROM projects"):
            return SimpleNamespace(fetchone=lambda: (1,) if self.project_exists else None)
        return SimpleNamespace(fetchone=lambda: None)


@contextmanager
def _connection_provider(connection: TrackingConnection) -> Iterator[TrackingConnection]:
    yield connection


@pytest.mark.unit
def test_sql_create_has_no_second_project_preflight_but_complete_cancel_preflight_before_begin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(invitation_persistence, "database_settings", lambda: SimpleNamespace(backend="duckdb"))

    create_connection = TrackingConnection()
    with SQLInvitationCreateUnitOfWorkProvider(
        lambda _project, _connection: None,
        connection_provider=lambda: _connection_provider(create_connection),
    )("project-1"):
        pass
    assert create_connection.events == ["BEGIN TRANSACTION", "COMMIT"]

    for provider_type in (SQLInvitationCompleteUnitOfWorkProvider, SQLInvitationCancelUnitOfWorkProvider):
        connection = TrackingConnection()
        with provider_type(
            lambda _project, _connection: None,
            connection_provider=lambda: _connection_provider(connection),
        )("project-1"):
            pass
        assert connection.events[0].startswith("SELECT 1 FROM projects")
        assert connection.events[1:] == ["BEGIN TRANSACTION", "COMMIT"]


@pytest.mark.unit
def test_sql_invitation_providers_use_postgres_locks_whitelist_commit_and_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(invitation_persistence, "database_settings", lambda: SimpleNamespace(backend="postgresql"))
    expected_locks = {
        SQLInvitationCreateUnitOfWorkProvider: "LOCK TABLE users, project_invitations, project_memberships IN SHARE ROW EXCLUSIVE MODE",
        SQLInvitationCompleteUnitOfWorkProvider: "LOCK TABLE users, project_invitations, project_memberships IN SHARE ROW EXCLUSIVE MODE",
        SQLInvitationCancelUnitOfWorkProvider: "LOCK TABLE project_invitations IN SHARE ROW EXCLUSIVE MODE",
    }
    for provider_type, lock in expected_locks.items():
        connection = TrackingConnection()
        with provider_type(
            lambda _project, _connection: None,
            connection_provider=lambda: _connection_provider(connection),
        )("project-1"):
            pass
        assert lock in connection.events

    with pytest.raises(RuntimeError, match="Unexpected table lock requested"):
        invitation_persistence._SQLProjectInvitationUnitOfWorkProvider(
            lambda _project, _connection: None,
            ("projects",),
            preflight_project=False,
            connection_provider=lambda: _connection_provider(TrackingConnection()),
        )

    connection = TrackingConnection()
    provider = SQLInvitationCancelUnitOfWorkProvider(
        lambda _project, _connection: None,
        connection_provider=lambda: _connection_provider(connection),
    )
    with pytest.raises(ValueError, match="boom"):
        with provider("project-1"):
            raise ValueError("boom")
    assert connection.events[-1] == "ROLLBACK"


@pytest.mark.unit
def test_project_invitation_http_router_is_thin_and_preserves_exact_route_contract() -> None:
    source = Path(invitation_router.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert "write_audit_event" not in source
    assert not any(
        isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Attribute) and node.func.attr == "execute")
            or (isinstance(node.func, ast.Name) and node.func.id in {"connect", "rows"})
        )
        for node in ast.walk(tree)
    )

    target_paths = {
        "/api/projects/{project_id}/directory/employees",
        "/api/projects/{project_id}/invitations",
        "/api/projects/{project_id}/invitations/{invitation_id}/complete",
        "/api/projects/{project_id}/invitations/{invitation_id}",
    }
    routes = [route for route in app.routes if getattr(route, "path", "") in target_paths]
    assert [(route.path, tuple(sorted(route.methods or ()))) for route in routes] == [
        ("/api/projects/{project_id}/directory/employees", ("GET",)),
        ("/api/projects/{project_id}/invitations", ("GET",)),
        ("/api/projects/{project_id}/invitations", ("POST",)),
        ("/api/projects/{project_id}/invitations/{invitation_id}/complete", ("POST",)),
        ("/api/projects/{project_id}/invitations/{invitation_id}", ("DELETE",)),
    ]
    assert [route.endpoint.__name__ for route in routes] == [
        "search_directory_employees", "list_project_invitations", "create_project_invitation",
        "complete_project_invitation", "cancel_project_invitation",
    ]
    assert all(route.endpoint.__module__ == invitation_router.__name__ for route in routes)

    paths = app.openapi()["paths"]
    expected_operation_ids = {
        ("/api/projects/{project_id}/directory/employees", "get"): "search_directory_employees_api_projects__project_id__directory_employees_get",
        ("/api/projects/{project_id}/invitations", "get"): "list_project_invitations_api_projects__project_id__invitations_get",
        ("/api/projects/{project_id}/invitations", "post"): "create_project_invitation_api_projects__project_id__invitations_post",
        ("/api/projects/{project_id}/invitations/{invitation_id}/complete", "post"): "complete_project_invitation_api_projects__project_id__invitations__invitation_id__complete_post",
        ("/api/projects/{project_id}/invitations/{invitation_id}", "delete"): "cancel_project_invitation_api_projects__project_id__invitations__invitation_id__delete",
    }
    for (path, method), operation_id in expected_operation_ids.items():
        assert paths[path][method]["operationId"] == operation_id


@pytest.mark.unit
def test_invitation_extraction_removed_legacy_handlers_and_keeps_include_before_remaining_access_routes() -> None:
    source = Path(__file__).parents[1].joinpath("app", "routers", "access_control.py").read_text(encoding="utf-8")
    for name in (
        "search_directory_employees", "list_project_invitations", "create_project_invitation",
        "complete_project_invitation", "cancel_project_invitation",
    ):
        assert f"def {name}(" not in source
    include = "router.include_router(project_invitations_router)"
    members_include = "router.include_router(project_memberships_router)"
    assignee_include = "router.include_router(project_assignees_router)"
    assert include in source
    assert members_include in source and assignee_include in source
    assert source.index(members_include) < source.index(include) < source.index(assignee_include)
    menu_include = "router.include_router(menu_policy_router)"
    assert menu_include in source
    assert (
        source.index(members_include)
        < source.index(include)
        < source.index(assignee_include)
        < source.index(menu_include)
    )


def _insert_http_user(employee_id: str, display_name: str) -> str:
    user_id = f"invitation-http-{uuid4().hex[:10]}"
    with connect() as connection:
        now = NOW
        connection.execute(
            """
            INSERT INTO users
                (id, username, password_hash, display_name, employee_id, legacy_role,
                 account_status, is_global_admin, is_active, created_at, updated_at)
            VALUES (?, ?, NULL, ?, ?, 'viewer', 'ACTIVE', false, true, ?, ?)
            """,
            [user_id, user_id, display_name, employee_id, now, now],
        )
    return user_id


def _service_employee(employee: DirectoryEmployee):
    from app.services.directory_service import DirectoryEmployee as ServiceDirectoryEmployee

    return ServiceDirectoryEmployee(**employee)


class _HttpDirectory:
    def __init__(self, employee: DirectoryEmployee):
        self.employee = employee

    def search(self, _query: str, _limit: int) -> list[Any]:
        return [_service_employee(self.employee)]

    def get_by_employee_id(self, value: str) -> Any:
        return _service_employee(self.employee) if value == self.employee["employee_id"] else None


def _audit_detail(action: str, key: str, value: str) -> dict[str, object]:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT detail_json FROM audit_events
            WHERE action=? AND json_extract_string(detail_json, ?) = ?
            ORDER BY occurred_at DESC LIMIT 1
            """,
            [action, f"$.{key}", value],
        ).fetchone()
    assert row is not None
    return json.loads(row[0])


@pytest.mark.contract
def test_duckdb_http_create_list_complete_cancel_and_exact_masked_audit() -> None:
    initialize_database()
    employee_id = f"E{uuid4().hex[:8]}"
    user_id = _insert_http_user(employee_id, "초대 통합 사용자")
    employee = {**EMPLOYEE, "employee_id": employee_id, "display_name": "초대 통합 사용자"}
    from app.services.directory_service import set_employee_directory_for_tests

    set_employee_directory_for_tests(_HttpDirectory(employee))
    try:
        with TestClient(app) as client:
            created = client.post(
                "/api/projects/project-tv-001/invitations",
                json={"employee_id": employee_id, "desired_role": "power"},
            )
            assert created.status_code == 201, created.text
            invitation_id = created.json()["id"]
            assert created.json()["status"] == "READY"
            listed = client.get("/api/projects/project-tv-001/invitations")
            assert listed.status_code == 200
            assert next(item["id"] for item in listed.json() if item["id"] == invitation_id) == invitation_id
            completed = client.post(f"/api/projects/project-tv-001/invitations/{invitation_id}/complete")
            assert completed.status_code == 200, completed.text
            assert completed.json()["status"] == "COMPLETED"

        assert _audit_detail("PROJECT_INVITATION_CREATED", "project_id", "project-tv-001") == {
            "project_id": "project-tv-001",
            "target_employee_id": employee_id[:2] + "*" * (len(employee_id) - 3) + employee_id[-1],
        }
        assert _audit_detail("PROJECT_INVITATION_STATUS_CHANGED", "target_user_id", user_id)["new_status"] == "COMPLETED"
    finally:
        set_employee_directory_for_tests(None)


@pytest.mark.contract
def test_duckdb_http_cancel_and_final_guard() -> None:
    initialize_database()
    employee_id = f"E{uuid4().hex[:8]}"
    employee = {**EMPLOYEE, "employee_id": employee_id}
    from app.services.directory_service import set_employee_directory_for_tests

    set_employee_directory_for_tests(_HttpDirectory(employee))
    try:
        with TestClient(app) as client:
            created = client.post(
                "/api/projects/project-tv-001/invitations",
                json={"employee_id": employee_id, "desired_role": "general"},
            )
            invitation_id = created.json()["id"]
            cancelled = client.delete(f"/api/projects/project-tv-001/invitations/{invitation_id}")
            assert cancelled.status_code == 200
            assert cancelled.json() == {"status": "CANCELLED", "id": invitation_id}
            replay = client.delete(f"/api/projects/project-tv-001/invitations/{invitation_id}")
            assert replay.status_code == 409
            assert replay.json()["detail"] == {"code": "INVITATION_ALREADY_FINAL"}
    finally:
        set_employee_directory_for_tests(None)


@pytest.mark.contract
def test_duckdb_http_post_audit_failure_rolls_back_invitation_and_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    initialize_database()
    employee_id = f"E{uuid4().hex[:8]}"
    employee = {**EMPLOYEE, "employee_id": employee_id}
    from app.services.directory_service import set_employee_directory_for_tests

    set_employee_directory_for_tests(_HttpDirectory(employee))
    original = invitation_persistence.SQLProjectInvitationUnitOfWork.add_audit

    def fail_after_audit(unit_of_work: Any, audit: Any) -> None:
        original(unit_of_work, audit)
        raise RuntimeError("injected invitation audit failure")

    monkeypatch.setattr(invitation_persistence.SQLProjectInvitationUnitOfWork, "add_audit", fail_after_audit)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                "/api/projects/project-tv-001/invitations",
                json={"employee_id": employee_id, "desired_role": "general"},
            )
        assert response.status_code == 500
        with connect() as connection:
            assert connection.execute(
                "SELECT count(*) FROM project_invitations WHERE project_id=? AND employee_id=?",
                ["project-tv-001", employee_id],
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT count(*) FROM audit_events WHERE action='PROJECT_INVITATION_CREATED' "
                "AND json_extract_string(detail_json, '$.project_id')=?",
                ["project-tv-001"],
            ).fetchone()[0] == 0
    finally:
        set_employee_directory_for_tests(None)


@pytest.mark.contract
def test_duckdb_http_complete_post_audit_failure_is_atomic(monkeypatch: pytest.MonkeyPatch) -> None:
    initialize_database()
    employee_id = f"E{uuid4().hex[:8]}"
    user_id = _insert_http_user(employee_id, "완료 롤백 사용자")
    employee = {**EMPLOYEE, "employee_id": employee_id, "display_name": "완료 롤백 사용자"}
    from app.services.directory_service import set_employee_directory_for_tests

    set_employee_directory_for_tests(_HttpDirectory(employee))
    try:
        with TestClient(app) as client:
            created = client.post(
                "/api/projects/project-tv-001/invitations",
                json={"employee_id": employee_id, "desired_role": "power"},
            )
            assert created.status_code == 201, created.text
            invitation_id = created.json()["id"]

        original = invitation_persistence.SQLProjectInvitationUnitOfWork.add_audit

        def fail_after_audit(unit_of_work: Any, audit: Any) -> None:
            original(unit_of_work, audit)
            raise RuntimeError("injected completion audit failure")

        monkeypatch.setattr(invitation_persistence.SQLProjectInvitationUnitOfWork, "add_audit", fail_after_audit)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(f"/api/projects/project-tv-001/invitations/{invitation_id}/complete")
        assert response.status_code == 500

        with connect() as connection:
            assert connection.execute(
                "SELECT count(*) FROM project_memberships WHERE project_id=? AND user_id=?",
                ["project-tv-001", user_id],
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT status FROM project_invitations WHERE id=?", [invitation_id]
            ).fetchone() == ("READY",)
            assert connection.execute(
                "SELECT count(*) FROM audit_events WHERE action='PROJECT_INVITATION_STATUS_CHANGED' "
                "AND json_extract_string(detail_json, '$.project_id')=? "
                "AND json_extract_string(detail_json, '$.new_status')='COMPLETED'",
                ["project-tv-001"],
            ).fetchone()[0] == 0
    finally:
        set_employee_directory_for_tests(None)


@pytest.mark.contract
def test_duckdb_http_directory_search_success_and_exact_audit() -> None:
    initialize_database()
    from app.services.directory_service import set_employee_directory_for_tests

    set_employee_directory_for_tests(_HttpDirectory(EMPLOYEE))
    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/projects/project-tv-001/directory/employees",
                params={"q": " E9 ", "limit": 20},
            )
        assert response.status_code == 200
        assert response.json() == [EMPLOYEE]
        assert _audit_detail("DIRECTORY_SEARCHED", "project_id", "project-tv-001") == {
            "project_id": "project-tv-001",
            "result_count": 1,
        }
    finally:
        set_employee_directory_for_tests(None)


@pytest.mark.contract
def test_duckdb_http_directory_search_unavailable_maps_to_503(monkeypatch: pytest.MonkeyPatch) -> None:
    initialize_database()
    from app.adapters.directory.project_invitations import EmployeeDirectoryGateway
    from app.services.directory_service import set_employee_directory_for_tests

    set_employee_directory_for_tests(None)
    monkeypatch.setattr(
        EmployeeDirectoryGateway,
        "search",
        lambda _self, _query, _limit: (_ for _ in ()).throw(DirectoryUnavailableError("offline")),
    )
    with TestClient(app) as client:
        response = client.get(
            "/api/projects/project-tv-001/directory/employees", params={"q": "E9"}
        )
    assert response.status_code == 503
    assert response.json()["detail"] == {"code": "DIRECTORY_UNAVAILABLE", "message": "offline"}


@pytest.mark.contract
def test_duckdb_http_complete_error_details_preserve_status_only_when_available() -> None:
    initialize_database()
    from app.services.directory_service import set_employee_directory_for_tests

    # An employee absent from local users creates a PENDING_ACCOUNT invitation.
    pending_employee_id = f"E{uuid4().hex[:8]}"
    pending_employee = {**EMPLOYEE, "employee_id": pending_employee_id}
    set_employee_directory_for_tests(_HttpDirectory(pending_employee))
    try:
        with TestClient(app) as client:
            pending = client.post(
                "/api/projects/project-tv-001/invitations",
                json={"employee_id": pending_employee_id, "desired_role": "general"},
            )
            pending_complete = client.post(
                f"/api/projects/project-tv-001/invitations/{pending.json()['id']}/complete"
            )
        assert pending_complete.status_code == 409
        assert pending_complete.json()["detail"] == {
            "code": "INVITATION_ACCOUNT_NOT_READY", "status": "PENDING_ACCOUNT"
        }
    finally:
        set_employee_directory_for_tests(None)

    # Completed replay includes the final status in its detail.
    initialize_database()
    employee_id = f"E{uuid4().hex[:8]}"
    _insert_http_user(employee_id, "완료 재호출 사용자")
    employee = {**EMPLOYEE, "employee_id": employee_id, "display_name": "완료 재호출 사용자"}
    set_employee_directory_for_tests(_HttpDirectory(employee))
    try:
        with TestClient(app) as client:
            created = client.post(
                "/api/projects/project-tv-001/invitations",
                json={"employee_id": employee_id, "desired_role": "general"},
            )
            invitation_id = created.json()["id"]
            assert client.post(f"/api/projects/project-tv-001/invitations/{invitation_id}/complete").status_code == 200
            replay = client.post(f"/api/projects/project-tv-001/invitations/{invitation_id}/complete")
        assert replay.status_code == 409
        assert replay.json()["detail"] == {
            "code": "INVITATION_ALREADY_FINAL", "status": "COMPLETED"
        }
    finally:
        set_employee_directory_for_tests(None)

    # READY with an account that became inactive returns only the code.
    initialize_database()
    inactive_id = f"E{uuid4().hex[:8]}"
    user_id = _insert_http_user(inactive_id, "비활성 완료 사용자")
    employee = {**EMPLOYEE, "employee_id": inactive_id, "display_name": "비활성 완료 사용자"}
    set_employee_directory_for_tests(_HttpDirectory(employee))
    try:
        with TestClient(app) as client:
            created = client.post(
                "/api/projects/project-tv-001/invitations",
                json={"employee_id": inactive_id, "desired_role": "general"},
            )
            assert created.json()["status"] == "READY"
            with connect() as connection:
                connection.execute("UPDATE users SET account_status='SUSPENDED' WHERE id=?", [user_id])
            inactive_complete = client.post(
                f"/api/projects/project-tv-001/invitations/{created.json()['id']}/complete"
            )
        assert inactive_complete.status_code == 409
        assert inactive_complete.json()["detail"] == {"code": "INVITATION_ACCOUNT_NOT_READY"}
    finally:
        set_employee_directory_for_tests(None)


@pytest.mark.contract
def test_duckdb_http_directory_unavailable_happens_before_transaction_and_leaves_no_invitation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initialize_database()
    from app.adapters.directory.project_invitations import EmployeeDirectoryGateway

    monkeypatch.setattr(
        EmployeeDirectoryGateway,
        "get_by_employee_id",
        lambda _self, _employee_id: (_ for _ in ()).throw(DirectoryUnavailableError("offline")),
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/projects/project-tv-001/invitations",
            json={"employee_id": "E-OFFLINE", "desired_role": "general"},
        )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "DIRECTORY_UNAVAILABLE"
    with connect() as connection:
        assert connection.execute(
            "SELECT count(*) FROM project_invitations WHERE project_id=? AND employee_id=?",
            ["project-tv-001", "E-OFFLINE"],
        ).fetchone()[0] == 0


@pytest.mark.contract
def test_project_invitation_openapi_snapshot_remains_unchanged() -> None:
    snapshot = Path(__file__).parents[2] / "frontend" / "openapi.json"
    assert check_contract(snapshot, app.openapi()) == []
