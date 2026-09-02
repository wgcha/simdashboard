from __future__ import annotations

import ast
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.adapters.http.routers import project_memberships as membership_router
from app.adapters.persistence import project_memberships as membership_persistence
from app.adapters.persistence.project_memberships import SQLProjectMembershipUnitOfWorkProvider
from app.application.project_memberships.memberships import (
    change_member_role,
    create_member,
    list_members,
    remove_member,
)
from app.database import initialize_database
from app.database_connection import connect
from app.domains.project_memberships.models import (
    AlreadyProjectMemberError,
    InactiveProjectMemberError,
    LastProjectAdminProtectedError,
    PersistedProjectMembership,
    ProjectMemberListItem,
    ProjectMemberNotFoundError,
    ProjectMembershipAuditContext,
    ProjectMembershipError,
    ProjectNotFoundError,
    StaleUserVersionError,
    UserHasOpenWorkItemsError,
    UserNotFoundError,
)
from app.domains.project_memberships.ports import ProjectMembershipReader, ProjectMembershipUnitOfWork
from app.main import app
from scripts.check_openapi_contract import check_contract


AUDIT: ProjectMembershipAuditContext = {
    "user_id": "actor-1",
    "username": "actor",
    "role": "admin",
    "method": "PATCH",
    "path": "/api/projects/project-1/members/user-1",
    "request_id": "request-1",
    "client_ip": "127.0.0.1",
    "user_agent": "pytest",
}
NOW = datetime(2026, 1, 2, 3, 4, 5)


def _membership(*, role: str = "power", updated_at: datetime = NOW) -> PersistedProjectMembership:
    return {
        "id": "membership-1",
        "project_id": "project-1",
        "user_id": "user-1",
        "role": role,  # type: ignore[typeddict-item]
        "created_by": "actor-1",
        "created_at": NOW,
        "updated_by": "actor-1",
        "updated_at": updated_at,
    }


class FakeReader:
    def __init__(self, events: list[str], *, exists: bool = True) -> None:
        self.exists = exists
        self.events = events

    def project_exists(self, project_id: str) -> bool:
        self.events.append(f"exists:{project_id}")
        return self.exists

    def authorize_manage(self, project_id: str) -> None:
        self.events.append(f"authorize:{project_id}")

    def list_members(self, project_id: str) -> list[ProjectMemberListItem]:
        self.events.append(f"list:{project_id}")
        return []


@pytest.mark.unit
def test_list_members_checks_project_then_authorizes_and_closes_reader() -> None:
    events: list[str] = []
    reader = FakeReader(events)

    @contextmanager
    def provider() -> Iterator[ProjectMembershipReader]:
        events.append("open")
        try:
            yield reader
        finally:
            events.append("close")

    assert list_members("project-1", provider) == []
    assert events == ["open", "exists:project-1", "authorize:project-1", "list:project-1", "close"]


@pytest.mark.unit
def test_list_members_missing_project_short_circuits_before_authorization_or_list() -> None:
    events: list[str] = []
    reader = FakeReader(events, exists=False)

    @contextmanager
    def provider() -> Iterator[ProjectMembershipReader]:
        try:
            yield reader
        finally:
            events.append("close")

    with pytest.raises(ProjectNotFoundError) as error:
        list_members("missing", provider)

    assert error.value.project_id == "missing"
    assert events == ["exists:missing", "close"]


class FakeUnitOfWork:
    def __init__(
        self,
        *,
        active: bool | None = True,
        duplicate: bool = False,
        current: PersistedProjectMembership | None = None,
        global_admin: bool = False,
        admin_count: int = 2,
        open_work_items: int = 0,
    ) -> None:
        self.active = active
        self.duplicate = duplicate
        self.current = current
        self.global_admin = global_admin
        self.admin_count = admin_count
        self.open_work_items = open_work_items
        self.events: list[str] = []
        self.audit: dict[str, object] | None = None

    def authorize_manage(self, project_id: str) -> bool:
        self.events.append(f"authorize:{project_id}")
        return self.global_admin

    def find_active_user(self, user_id: str) -> bool | None:
        self.events.append(f"active:{user_id}")
        return self.active

    def membership_exists(self, project_id: str, user_id: str) -> bool:
        self.events.append(f"duplicate:{project_id}:{user_id}")
        return self.duplicate

    def add_membership(self, membership_id: str, project_id: str, user_id: str, role: str, actor_id: str, occurred_at: datetime) -> None:
        self.events.append(f"insert:{membership_id}:{project_id}:{user_id}:{role}:{actor_id}")
        self.current = _membership(role=role, updated_at=occurred_at)

    def find_membership(self, project_id: str, user_id: str) -> PersistedProjectMembership | None:
        self.events.append(f"reread:{project_id}:{user_id}")
        return self.current

    def count_admins(self, project_id: str) -> int:
        self.events.append(f"admins:{project_id}")
        return self.admin_count

    def update_role(self, project_id: str, user_id: str, role: str, actor_id: str, occurred_at: datetime) -> None:
        self.events.append(f"update:{project_id}:{user_id}:{role}:{actor_id}")
        assert self.current is not None
        self.current = {**self.current, "role": role, "updated_by": actor_id, "updated_at": occurred_at}  # type: ignore[typeddict-item]

    def count_open_work_items(self, project_id: str, user_id: str) -> int:
        self.events.append(f"open-work:{project_id}:{user_id}")
        return self.open_work_items

    def delete(self, project_id: str, user_id: str) -> None:
        self.events.append(f"delete:{project_id}:{user_id}")

    def add_audit(self, audit: dict[str, object]) -> None:
        self.events.append("audit")
        self.audit = audit


def _uow_provider(uow: FakeUnitOfWork, events: list[str] | None = None):
    @contextmanager
    def provider(_project_id: str) -> Iterator[ProjectMembershipUnitOfWork]:
        if events is not None:
            events.append("open")
        try:
            yield uow
        finally:
            if events is not None:
                events.append("close")

    return provider


@pytest.mark.unit
def test_create_member_orders_authorize_active_duplicate_insert_audit_and_reread() -> None:
    uow = FakeUnitOfWork()
    events: list[str] = []

    result = create_member(
        "project-1",
        "user-1",
        "power",
        "actor-1",
        AUDIT,
        _uow_provider(uow, events),
        id_factory=lambda prefix, length: f"{prefix}-fixed-{length}",
        clock=lambda: NOW,
    )

    assert result["role"] == "power"
    assert events == ["open", "close"]
    assert uow.events == [
        "authorize:project-1",
        "active:user-1",
        "duplicate:project-1:user-1",
        "insert:membership-fixed-16:project-1:user-1:power:actor-1",
        "audit",
        "reread:project-1:user-1",
    ]
    assert uow.audit == {
        **AUDIT,
        "action": "PROJECT_MEMBERSHIP_CREATED",
        "status_code": 201,
        "detail": {"target_user_id": "user-1", "project_id": "project-1", "new_role": "power"},
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("active", "duplicate", "error"),
    [
        (None, False, UserNotFoundError),
        (False, False, InactiveProjectMemberError),
        (True, True, AlreadyProjectMemberError),
    ],
)
def test_create_member_preserves_typed_validation_errors(
    active: bool | None,
    duplicate: bool,
    error: type[Exception],
) -> None:
    uow = FakeUnitOfWork(active=active, duplicate=duplicate)
    with pytest.raises(error):
        create_member("project-1", "user-1", "general", "actor-1", AUDIT, _uow_provider(uow))

    expected = ["authorize:project-1", "active:user-1"]
    if active is True:
        expected.append("duplicate:project-1:user-1")
    assert uow.events == expected


@pytest.mark.unit
def test_change_member_role_rejects_stale_timestamp_and_last_admin_before_mutation() -> None:
    stale_uow = FakeUnitOfWork(current=_membership())
    with pytest.raises(StaleUserVersionError):
        change_member_role(
            "project-1", "user-1", "admin", "actor-1", AUDIT, _uow_provider(stale_uow),
            expected_updated_at=datetime(2026, 1, 2, 3, 4, 6),
        )
    assert stale_uow.events == ["authorize:project-1", "reread:project-1:user-1"]

    last_admin_uow = FakeUnitOfWork(current=_membership(role="admin"), admin_count=1)
    with pytest.raises(LastProjectAdminProtectedError):
        change_member_role(
            "project-1", "user-1", "power", "actor-1", AUDIT, _uow_provider(last_admin_uow),
            expected_updated_at=NOW,
        )
    assert last_admin_uow.events == [
        "authorize:project-1", "reread:project-1:user-1", "admins:project-1"
    ]


@pytest.mark.unit
def test_change_member_role_allows_global_admin_and_records_exact_old_new_audit() -> None:
    uow = FakeUnitOfWork(current=_membership(role="admin"), global_admin=True, admin_count=1)
    result = change_member_role(
        "project-1", "user-1", "power", "actor-1", AUDIT, _uow_provider(uow),
        expected_updated_at=NOW, clock=lambda: datetime(2026, 1, 2, 4, 0, 0),
    )

    assert result["role"] == "power"
    assert uow.events == [
        "authorize:project-1",
        "reread:project-1:user-1",
        "update:project-1:user-1:power:actor-1",
        "audit",
        "reread:project-1:user-1",
    ]
    assert uow.audit == {
        **AUDIT,
        "action": "PROJECT_MEMBERSHIP_ROLE_CHANGED",
        "status_code": 200,
        "detail": {
            "target_user_id": "user-1",
            "project_id": "project-1",
            "old_role": "admin",
            "new_role": "power",
        },
    }


@pytest.mark.unit
def test_remove_member_rejects_last_admin_and_open_work_with_count() -> None:
    last_admin = FakeUnitOfWork(current=_membership(role="admin"), admin_count=1)
    with pytest.raises(LastProjectAdminProtectedError):
        remove_member("project-1", "user-1", "actor-1", AUDIT, _uow_provider(last_admin))
    assert last_admin.events == ["authorize:project-1", "reread:project-1:user-1", "admins:project-1"]

    open_work = FakeUnitOfWork(current=_membership(), open_work_items=3)
    with pytest.raises(UserHasOpenWorkItemsError) as error:
        remove_member("project-1", "user-1", "actor-1", AUDIT, _uow_provider(open_work))
    assert error.value.count == 3
    assert open_work.events == [
        "authorize:project-1", "reread:project-1:user-1", "open-work:project-1:user-1"
    ]


@pytest.mark.unit
def test_remove_member_returns_exact_result_and_audit() -> None:
    uow = FakeUnitOfWork(current=_membership(role="general"))
    assert remove_member("project-1", "user-1", "actor-1", AUDIT, _uow_provider(uow)) == {
        "status": "removed",
        "project_id": "project-1",
        "user_id": "user-1",
    }
    assert uow.events == [
        "authorize:project-1",
        "reread:project-1:user-1",
        "open-work:project-1:user-1",
        "delete:project-1:user-1",
        "audit",
    ]
    assert uow.audit == {
        **AUDIT,
        "action": "PROJECT_MEMBERSHIP_REMOVED",
        "status_code": 200,
        "detail": {"target_user_id": "user-1", "project_id": "project-1", "old_role": "general"},
    }


class TrackingConnection:
    def __init__(self, *, project_exists: bool = True) -> None:
        self.events: list[tuple[str, str]] = []
        self.project_exists = project_exists

    def execute(self, statement: str, _parameters: object = None):
        normalized = " ".join(statement.split())
        self.events.append(("execute", normalized))
        if statement.startswith("SELECT 1 FROM projects"):
            return SimpleNamespace(fetchone=lambda: (1,) if self.project_exists else None)
        return SimpleNamespace(fetchone=lambda: None)


@contextmanager
def _connection_provider(connection: TrackingConnection) -> Iterator[TrackingConnection]:
    yield connection


@pytest.mark.unit
def test_sql_uow_provider_preflights_project_before_begin_and_locks_only_whitelisted_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(membership_persistence, "database_settings", lambda: SimpleNamespace(backend="postgresql"))
    connection = TrackingConnection()
    provider = SQLProjectMembershipUnitOfWorkProvider(
        lambda _project_id, _connection: False,
        ("users", "project_memberships"),
        connection_provider=lambda: _connection_provider(connection),
    )

    with provider("project-1"):
        connection.events.append(("body", "inside"))

    statements = [statement for kind, statement in connection.events if kind == "execute"]
    assert statements[0].startswith("SELECT 1 FROM projects")
    assert statements[1] == "BEGIN TRANSACTION"
    assert statements[2] == "LOCK TABLE users, project_memberships IN SHARE ROW EXCLUSIVE MODE"
    assert statements[-1] == "COMMIT"
    assert statements.index("BEGIN TRANSACTION") < statements.index("COMMIT")

    with pytest.raises(RuntimeError, match="Unexpected table lock requested"):
        SQLProjectMembershipUnitOfWorkProvider(
            lambda _project_id, _connection: False,
            ("projects",),
            connection_provider=lambda: _connection_provider(TrackingConnection()),
        )


@pytest.mark.unit
def test_sql_uow_provider_missing_project_never_begins_and_rolls_back_body_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(membership_persistence, "database_settings", lambda: SimpleNamespace(backend="duckdb"))
    missing = TrackingConnection(project_exists=False)
    provider = SQLProjectMembershipUnitOfWorkProvider(
        lambda _project_id, _connection: False,
        ("project_memberships",),
        connection_provider=lambda: _connection_provider(missing),
    )
    with pytest.raises(Exception) as missing_error:
        with provider("missing"):
            raise AssertionError("not entered")
    assert isinstance(missing_error.value, ProjectNotFoundError)
    assert [statement for _kind, statement in missing.events] == [
        "SELECT 1 FROM projects WHERE id=?"
    ]

    connection = TrackingConnection()
    provider = SQLProjectMembershipUnitOfWorkProvider(
        lambda _project_id, _connection: False,
        ("project_memberships",),
        connection_provider=lambda: _connection_provider(connection),
    )
    with pytest.raises(ValueError, match="boom"):
        with provider("project-1"):
            raise ValueError("boom")
    statements = [statement for kind, statement in connection.events if kind == "execute"]
    assert statements == ["SELECT 1 FROM projects WHERE id=?", "BEGIN TRANSACTION", "ROLLBACK"]


@pytest.mark.unit
def test_project_membership_http_router_is_thin_and_preserves_route_contract() -> None:
    source = Path(membership_router.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
        for node in ast.walk(tree)
    )
    assert "write_audit_event" not in source
    assert "connect(" not in source

    routes = [route for route in app.routes if "/members" in getattr(route, "path", "")]
    assert [(route.path, tuple(sorted(route.methods or ()))) for route in routes] == [
        ("/api/projects/{project_id}/members", ("GET",)),
        ("/api/projects/{project_id}/members", ("POST",)),
        ("/api/projects/{project_id}/members/{user_id}", ("PATCH",)),
        ("/api/projects/{project_id}/members/{user_id}", ("DELETE",)),
    ]
    assert all(route.endpoint.__module__ == membership_router.__name__ for route in routes)
    paths = app.openapi()["paths"]
    assert paths["/api/projects/{project_id}/members"]["get"]["operationId"] == "list_project_members_api_projects__project_id__members_get"
    assert paths["/api/projects/{project_id}/members"]["post"]["operationId"] == "create_project_member_api_projects__project_id__members_post"
    assert paths["/api/projects/{project_id}/members/{user_id}"]["patch"]["operationId"] == "update_project_member_api_projects__project_id__members__user_id__patch"
    assert paths["/api/projects/{project_id}/members/{user_id}"]["delete"]["operationId"] == "delete_project_member_api_projects__project_id__members__user_id__delete"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("error", "status_code", "detail"),
    [
        (ProjectNotFoundError("missing"), 404, "프로젝트를 찾을 수 없습니다."),
        (
            ProjectMemberNotFoundError("project-1", "missing"),
            404,
            "프로젝트 멤버를 찾을 수 없습니다.",
        ),
        (UserNotFoundError("missing"), 404, "사용자를 찾을 수 없습니다."),
        (InactiveProjectMemberError(), 409, {"code": "PROJECT_MEMBER_MUST_BE_ACTIVE"}),
        (AlreadyProjectMemberError(), 409, {"code": "ALREADY_PROJECT_MEMBER"}),
        (
            StaleUserVersionError(),
            409,
            {
                "code": "STALE_USER_VERSION",
                "message": "사용자 정보가 다른 관리자에 의해 변경되었습니다.",
            },
        ),
        (LastProjectAdminProtectedError(), 409, {"code": "LAST_PROJECT_ADMIN_PROTECTED"}),
        (
            UserHasOpenWorkItemsError(3),
            409,
            {"code": "USER_HAS_OPEN_WORK_ITEMS", "count": 3},
        ),
    ],
)
def test_membership_http_error_mapping_preserves_status_and_detail(
    error: ProjectMembershipError,
    status_code: int,
    detail: object,
) -> None:
    with pytest.raises(HTTPException) as raised:
        membership_router._raise_mapped(error)

    assert raised.value.status_code == status_code
    assert raised.value.detail == detail


def _insert_http_user(suffix: str, display_name: str) -> str:
    user_id = f"membership-http-{suffix}-{uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO users
                (id, username, password_hash, display_name, legacy_role,
                 account_status, is_global_admin, is_active, created_at, updated_at)
            VALUES (?, ?, NULL, ?, 'viewer', 'ACTIVE', false, true, ?, ?)
            """,
            [user_id, user_id, display_name, now, now],
        )
    return user_id


def _audit_detail(action: str, target_user_id: str) -> dict[str, object]:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT detail_json FROM audit_events
            WHERE action=? AND json_extract_string(detail_json, '$.target_user_id')=?
            ORDER BY occurred_at DESC LIMIT 1
            """,
            [action, target_user_id],
        ).fetchone()
    assert row is not None
    return json.loads(row[0])


@pytest.mark.contract
def test_http_membership_crud_preserves_list_order_response_and_specific_audits() -> None:
    initialize_database()
    suffix = uuid4().hex[:8]
    first_user = _insert_http_user(suffix, f"AAA Membership {suffix}")
    second_user = _insert_http_user(suffix, f"ZZZ Membership {suffix}")

    with TestClient(app) as client:
        created_first = client.post(
            "/api/projects/project-tv-001/members",
            json={"user_id": first_user, "role": "power"},
        )
        created_second = client.post(
            "/api/projects/project-tv-001/members",
            json={"user_id": second_user, "role": "general"},
        )
        assert created_first.status_code == 201
        assert created_second.status_code == 201
        assert created_first.json()["user_id"] == first_user
        assert created_first.json()["role"] == "power"
        assert created_first.json()["created_by"] == "local-admin"

        listed = client.get("/api/projects/project-tv-001/members")
        assert listed.status_code == 200
        listed_ids = [item["user_id"] for item in listed.json() if item["user_id"] in {first_user, second_user}]
        assert listed_ids == [first_user, second_user]

        updated = client.patch(
            f"/api/projects/project-tv-001/members/{first_user}",
            json={"role": "admin"},
        )
        removed = client.delete(f"/api/projects/project-tv-001/members/{second_user}")
        assert updated.status_code == 200
        assert updated.json()["role"] == "admin"
        assert removed.status_code == 200
        assert removed.json() == {
            "status": "removed",
            "project_id": "project-tv-001",
            "user_id": second_user,
        }

    assert _audit_detail("PROJECT_MEMBERSHIP_CREATED", first_user) == {
        "target_user_id": first_user,
        "project_id": "project-tv-001",
        "new_role": "power",
    }
    assert _audit_detail("PROJECT_MEMBERSHIP_ROLE_CHANGED", first_user) == {
        "target_user_id": first_user,
        "project_id": "project-tv-001",
        "old_role": "power",
        "new_role": "admin",
    }
    assert _audit_detail("PROJECT_MEMBERSHIP_REMOVED", second_user) == {
        "target_user_id": second_user,
        "project_id": "project-tv-001",
        "old_role": "general",
    }


@pytest.mark.contract
def test_http_membership_rolls_back_mutation_and_specific_audit_after_audit_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initialize_database()
    suffix = uuid4().hex[:8]
    target_user = _insert_http_user(suffix, f"Rollback Membership {suffix}")
    original_add_audit = membership_persistence.SQLProjectMembershipUnitOfWork.add_audit

    def fail_after_audit(unit_of_work, audit):
        original_add_audit(unit_of_work, audit)
        raise RuntimeError("injected membership audit failure")

    monkeypatch.setattr(membership_persistence.SQLProjectMembershipUnitOfWork, "add_audit", fail_after_audit)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/projects/project-tv-001/members",
            json={"user_id": target_user, "role": "general"},
        )
    assert response.status_code == 500

    with connect() as connection:
        membership_count = connection.execute(
            "SELECT count(*) FROM project_memberships WHERE project_id=? AND user_id=?",
            ["project-tv-001", target_user],
        ).fetchone()[0]
        audit_count = connection.execute(
            """
            SELECT count(*) FROM audit_events
            WHERE action='PROJECT_MEMBERSHIP_CREATED'
              AND json_extract_string(detail_json, '$.target_user_id')=?
            """,
            [target_user],
        ).fetchone()[0]
    assert membership_count == 0
    assert audit_count == 0


@pytest.mark.contract
def test_project_membership_openapi_snapshot_remains_unchanged() -> None:
    snapshot = Path(__file__).parents[2] / "frontend" / "openapi.json"
    assert check_contract(snapshot, app.openapi()) == []
