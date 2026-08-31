"""Characterization tests for the user-administration command slice.

The two account-admin mutations deliberately have a small, explicit port.  These
tests keep the transaction boundary and the HTTP adapter honest while allowing
the SQL implementation to be replaced (DuckDB and PostgreSQL use the same
application contract).
"""
from __future__ import annotations

import ast
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from app.application.user_administration.commands import (
    update_account_status,
    update_global_admin,
)
from app.domains.user_administration.models import (
    GlobalAdminMustBeActiveError,
    LastGlobalAdminProtectedError,
    StaleUserVersionError,
    UserNotFoundError,
)
from app.main import app


NOW = datetime(2026, 1, 2, 3, 4, 5)
USER = {
    "id": "user-1",
    "account_status": "PENDING",
    "is_global_admin": False,
    "employee_id": "E-1",
    "updated_at": NOW,
}


class FakeUow:
    def __init__(
        self,
        events: list[str],
        *,
        user: dict[str, Any] | None = None,
        active_admins: int = 2,
        audit_error: Exception | None = None,
    ) -> None:
        self.events = events
        self.user = USER.copy() if user is None else user
        self.active_admins = active_admins
        self.audit_error = audit_error

    def authorize_user_approval(self) -> None:
        self.events.append("authorize")

    def find_user_for_status(self, user_id: str) -> tuple[str, bool, str | None, datetime] | None:
        self.events.append(f"read:{user_id}")
        if self.user is None:
            return None
        return (
            self.user["account_status"], self.user["is_global_admin"],
            self.user["employee_id"], self.user["updated_at"],
        )

    def find_user_for_global_admin(self, user_id: str) -> tuple[bool, str, datetime] | None:
        self.events.append(f"read:{user_id}")
        if self.user is None:
            return None
        return self.user["is_global_admin"], self.user["account_status"], self.user["updated_at"]

    def count_active_global_admins(self) -> int:
        self.events.append("count-active-admins")
        return self.active_admins

    def update_account_status(self, user_id: str, status: str, actor_id: str, occurred_at: datetime) -> None:
        self.events.append(f"update-status:{user_id}:{status}")
        if self.user:
            self.user["account_status"] = status

    def ready_pending_invitations(self, employee_id: str, user_id: str, actor_id: str, occurred_at: datetime) -> None:
        self.events.append(f"invitations:{employee_id}")

    def update_global_admin(self, user_id: str, is_global_admin: bool, occurred_at: datetime) -> None:
        self.events.append(f"update-admin:{user_id}:{is_global_admin}")
        if self.user:
            self.user["is_global_admin"] = is_global_admin

    def add_audit(self, record: dict[str, Any]) -> None:
        self.events.append("audit")
        if self.audit_error:
            raise self.audit_error

    def reread_user(self, user_id: str) -> dict[str, Any] | None:
        self.events.append(f"reread:{user_id}")
        return self.user

    def find_user(self, user_id: str) -> dict[str, Any] | None:
        self.events.append(f"reread:{user_id}")
        return self.user


@contextmanager
def _context(uow: FakeUow) -> Iterator[FakeUow]:
    yield uow


def provider(uow: FakeUow):
    return lambda: _context(uow)


def audit() -> dict[str, Any]:
    return {
        "user_id": "admin",
        "username": "admin",
        "role": "admin",
        "method": "PATCH",
        "path": "/api/admin/users/user-1/status",
        "request_id": "req-1",
        "client_ip": None,
        "user_agent": "pytest",
    }


@pytest.mark.unit
def test_status_command_orders_authorize_read_version_guard_mutation_invitation_audit_reread() -> None:
    events: list[str] = []
    result = update_account_status(
        "user-1", "ACTIVE", NOW, "reason", "admin", audit(), provider(FakeUow(events)), clock=lambda: NOW
    )
    assert events == [
        "authorize", "read:user-1", "update-status:user-1:ACTIVE",
        "invitations:E-1", "audit", "reread:user-1",
    ]
    assert result["account_status"] == "ACTIVE"


@pytest.mark.unit
def test_global_admin_command_orders_authorize_read_mutation_audit_reread() -> None:
    events: list[str] = []
    update_global_admin(
        "user-1", True, NOW, "reason", audit(),
        provider(FakeUow(events, user={**USER, "account_status": "ACTIVE"})),
        clock=lambda: NOW,
    )
    assert events == [
        "authorize", "read:user-1", "update-admin:user-1:True", "audit", "reread:user-1",
    ]


@pytest.mark.unit
def test_commands_map_missing_stale_last_admin_and_inactive_admin_errors_before_mutation() -> None:
    missing = FakeUow([])
    missing.user = None
    with pytest.raises(UserNotFoundError):
        update_account_status("missing", "ACTIVE", NOW, "reason", "admin", audit(), provider(missing))
    events: list[str] = []
    with pytest.raises(StaleUserVersionError):
        update_account_status(
            "user-1", "ACTIVE", NOW.replace(second=6), "reason", "admin", audit(), provider(FakeUow(events))
        )
    assert events == ["authorize", "read:user-1"]
    events = []
    uow = FakeUow(events, user={**USER, "account_status": "ACTIVE", "is_global_admin": True}, active_admins=1)
    with pytest.raises(LastGlobalAdminProtectedError):
        update_account_status("user-1", "SUSPENDED", NOW, "reason", "admin", audit(), provider(uow))
    assert events == ["authorize", "read:user-1", "count-active-admins"]
    events = []
    with pytest.raises(GlobalAdminMustBeActiveError):
        update_global_admin(
            "user-1", True, NOW, "reason", audit(),
            provider(FakeUow(events, user={**USER, "account_status": "PENDING"})),
        )
    assert events == ["authorize", "read:user-1"]


@pytest.mark.unit
def test_aware_and_naive_versions_compare_as_utc_instants() -> None:
    aware = NOW.replace(tzinfo=timezone.utc)
    events: list[str] = []
    update_account_status(
        "user-1", "ACTIVE", aware, "reason", "admin", audit(),
        provider(FakeUow(events)), clock=lambda: NOW,
    )
    assert "update-status:user-1:ACTIVE" in events


@pytest.mark.unit
def test_post_mutation_and_audit_failures_rollback_and_never_commit() -> None:
    events: list[str] = []
    with pytest.raises(RuntimeError):
        update_account_status("user-1", "ACTIVE", NOW, "reason", "admin", audit(),
                              provider(FakeUow(events, audit_error=RuntimeError("audit failed"))))
    assert events[-1] == "audit"


@pytest.mark.unit
def test_sql_uow_starts_locks_and_commits_in_exact_order(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.adapters.persistence import user_administration as persistence

    class Connection:
        def __init__(self) -> None:
            self.events: list[str] = []

        def execute(self, sql: str, _params: Any = None) -> Any:
            self.events.append(sql)
            return SimpleNamespace(fetchone=lambda: None, fetchall=lambda: [])

    providers = (
        (persistence.SQLAccountStatusUnitOfWorkProvider,
         "LOCK TABLE users, project_invitations IN SHARE ROW EXCLUSIVE MODE"),
        (persistence.SQLGlobalAdminUnitOfWorkProvider,
         "LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE"),
    )
    for provider_type, lock in providers:
        connection = Connection()
        monkeypatch.setattr(persistence, "database_settings", lambda: SimpleNamespace(backend="postgresql"))
        with provider_type(lambda _connection: None, lambda: _context(connection))():
            pass
        assert connection.events == ["BEGIN TRANSACTION", lock, "COMMIT"]

    connection = Connection()
    with pytest.raises(RuntimeError, match="boom"):
        provider = persistence._SQLUserAdministrationUnitOfWorkProvider(
            lambda _connection: None, ("users",), lambda: _context(connection)
        )
        with provider():
            raise RuntimeError("boom")
    assert connection.events == ["BEGIN TRANSACTION", "LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE", "ROLLBACK"]
    with pytest.raises(RuntimeError, match="Unexpected table lock requested"):
        persistence._SQLUserAdministrationUnitOfWorkProvider(
            lambda _connection: None, ("audit_events",), lambda: _context(Connection())
        )


@pytest.mark.unit
def test_status_and_global_admin_commands_keep_no_mutation_on_all_guards() -> None:
    for status in ("PENDING", "SUSPENDED"):
        events: list[str] = []
        uow = FakeUow(events, user={**USER, "account_status": "ACTIVE", "is_global_admin": True}, active_admins=1)
        with pytest.raises(LastGlobalAdminProtectedError):
            update_account_status("user-1", status, NOW, "reason", "admin", audit(), provider(uow))
        assert not any(item.startswith("update-") for item in events)
    events = []
    with pytest.raises(StaleUserVersionError):
        update_global_admin(
            "user-1", True, NOW.replace(second=9), "reason", audit(),
            provider(FakeUow(events, user={**USER, "account_status": "ACTIVE"})),
        )
    assert events == ["authorize", "read:user-1"]


@pytest.mark.contract
def test_duckdb_http_status_activation_updates_user_invitations_audit_and_safe_response() -> None:
    from uuid import uuid4
    from app.database import initialize_database
    from app.database_connection import connect

    initialize_database()
    user_id, employee_id = f"admin-slice-{uuid4().hex[:10]}", f"E{uuid4().hex[:8]}"
    now = NOW
    with connect() as connection:
        connection.execute(
            """INSERT INTO users (id, username, password_hash, display_name, employee_id, legacy_role,
               account_status, is_global_admin, is_active, created_at, updated_at)
               VALUES (?, ?, NULL, ?, ?, 'viewer', 'PENDING', false, true, ?, ?)""",
            [user_id, user_id, "슬라이스 사용자", employee_id, now, now],
        )
        invitations = (
            ("project-tv-001", "PENDING_ACCOUNT"),
            ("project-tv-002", "PENDING_APPROVAL"),
            ("project-tv-003", "COMPLETED"),
        )
        for project_id, status in invitations:
            connection.execute(
                """INSERT INTO project_invitations
                   (id, project_id, employee_id, display_name_snapshot, desired_role, status, invited_by, invited_at)
                   VALUES (?, ?, ?, ?, 'general', ?, 'local-admin', ?)""",
                [f"inv-{uuid4().hex[:10]}", project_id, employee_id, "슬라이스 사용자", status, now],
            )
    with TestClient(app) as client:
        response = client.patch(
            f"/api/admin/users/{user_id}/status",
            json={"account_status": "ACTIVE", "expected_updated_at": now.isoformat(), "reason": "승인"},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "id", "username", "display_name", "employee_id", "email", "department",
        "job_title", "account_status", "is_global_admin", "approved_by", "approved_at",
        "last_login_at", "created_at", "updated_at",
    }
    assert body["account_status"] == "ACTIVE" and body["approved_by"] == "local-admin"
    with connect() as connection:
        rows = connection.execute(
            "SELECT status FROM project_invitations WHERE employee_id=? ORDER BY project_id",
            [employee_id],
        ).fetchall()
        assert [row[0] for row in rows] == ["READY", "READY", "COMPLETED"]
        audit_row = connection.execute(
            "SELECT action, detail_json FROM audit_events "
            "WHERE action='ACCOUNT_STATUS_CHANGED' "
            "AND json_extract_string(detail_json, '$.target_user_id')=?",
            [user_id],
        ).fetchone()
    assert audit_row is not None and audit_row[0] == "ACCOUNT_STATUS_CHANGED"
    assert json.loads(audit_row[1]) == {
        "target_user_id": user_id, "old_status": "PENDING",
        "new_status": "ACTIVE", "reason": "승인",
    }


@pytest.mark.contract
def test_duckdb_global_admin_promotion_stale_inactive_and_last_admin_guards() -> None:
    from uuid import uuid4
    from app.database import initialize_database
    from app.database_connection import connect

    initialize_database()
    active_id, inactive_id = f"ga-{uuid4().hex[:8]}", f"ga-inactive-{uuid4().hex[:8]}"
    with connect() as connection:
        for user_id, status in ((active_id, "ACTIVE"), (inactive_id, "PENDING")):
            connection.execute(
                "INSERT INTO users (id, username, password_hash, display_name, legacy_role, "
                "account_status, is_global_admin, is_active, created_at, updated_at) "
                "VALUES (?, ?, NULL, ?, 'viewer', ?, false, true, ?, ?)",
                [user_id, user_id, user_id, status, NOW, NOW],
            )
    with TestClient(app) as client:
        promoted = client.patch(
            f"/api/admin/users/{active_id}/global-admin",
            json={"is_global_admin": True, "expected_updated_at": NOW.isoformat(), "reason": "승격"},
        )
        assert promoted.status_code == 200, promoted.text
        stale = client.patch(
            f"/api/admin/users/{active_id}/global-admin",
            json={"is_global_admin": False, "expected_updated_at": NOW.isoformat(), "reason": "stale"},
        )
        assert stale.status_code == 409
        assert stale.json()["detail"] == {
            "code": "STALE_USER_VERSION", "message": "사용자 정보가 다른 관리자에 의해 변경되었습니다."
        }
        inactive = client.patch(
            f"/api/admin/users/{inactive_id}/global-admin",
            json={"is_global_admin": True, "expected_updated_at": NOW.isoformat(), "reason": "승격"},
        )
        assert inactive.status_code == 422 and inactive.json()["detail"] == {"code": "GLOBAL_ADMIN_MUST_BE_ACTIVE"}
        with connect() as connection:
            connection.execute("UPDATE users SET is_global_admin=false WHERE id <> ?", [active_id])
        last = client.patch(
            f"/api/admin/users/{active_id}/global-admin",
            json={"is_global_admin": False, "expected_updated_at": promoted.json()["updated_at"], "reason": "해제"},
        )
        assert last.status_code == 409
        assert last.json()["detail"] == {
            "code": "LAST_GLOBAL_ADMIN_PROTECTED", "message": "마지막 전역 관리자 권한은 해제할 수 없습니다."
        }
    with connect() as connection:
        assert connection.execute("SELECT is_global_admin FROM users WHERE id=?", [active_id]).fetchone()[0] is True


@pytest.mark.contract
def test_duckdb_status_pending_and_suspended_preserve_approval_metadata_and_is_active() -> None:
    from uuid import uuid4
    from app.database import initialize_database
    from app.database_connection import connect

    initialize_database()
    user_id = f"status-semantics-{uuid4().hex[:8]}"
    approved_at = NOW.replace(hour=1)
    with connect() as connection:
        connection.execute(
            "INSERT INTO users (id, username, password_hash, display_name, legacy_role, "
            "account_status, is_global_admin, is_active, approved_by, approved_at, "
            "created_at, updated_at) VALUES (?, ?, NULL, 'Semantics', 'viewer', 'ACTIVE', "
            "false, true, 'approver', ?, ?, ?)",
            [user_id, user_id, approved_at, NOW, NOW],
        )
    with TestClient(app) as client:
        pending = client.patch(
            f"/api/admin/users/{user_id}/status",
            json={"account_status": "PENDING", "expected_updated_at": NOW.isoformat(), "reason": "대기"},
        )
        assert pending.status_code == 200
        assert pending.json()["account_status"] == "PENDING" and pending.json()["approved_by"] == "approver"
        with connect() as connection:
            assert connection.execute("SELECT is_active FROM users WHERE id=?", [user_id]).fetchone()[0] is True
        suspended = client.patch(
            f"/api/admin/users/{user_id}/status",
            json={"account_status": "SUSPENDED", "expected_updated_at": pending.json()["updated_at"], "reason": "중지"},
        )
        assert suspended.status_code == 200
        assert suspended.json()["account_status"] == "SUSPENDED" and suspended.json()["approved_by"] == "approver"
        with connect() as connection:
            assert connection.execute("SELECT is_active FROM users WHERE id=?", [user_id]).fetchone()[0] is False


@pytest.mark.contract
def test_password_project_admin_without_global_admin_is_denied_system_user_approval() -> None:
    from uuid import uuid4
    from app.database import initialize_database
    from app.database_connection import connect
    from app.security import hash_password

    initialize_database()
    actor, target = f"actor-{uuid4().hex[:8]}", f"target-{uuid4().hex[:8]}"
    with connect() as connection:
        for user_id, status in ((actor, "ACTIVE"), (target, "PENDING")):
            connection.execute(
                "INSERT INTO users (id, username, password_hash, display_name, legacy_role, "
                "account_status, is_global_admin, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'viewer', ?, ?, true, ?, ?)",
                [user_id, user_id, hash_password("password-for-tests"), user_id,
                 status, user_id == actor, NOW, NOW],
            )
        connection.execute(
            "INSERT INTO project_memberships (id, project_id, user_id, role, created_by, "
            "created_at, updated_by, updated_at) VALUES (?, 'project-tv-001', ?, 'admin', "
            "'local-admin', ?, 'local-admin', ?)",
            [f"membership-{uuid4().hex[:8]}", actor, NOW, NOW],
        )
    import os
    previous = os.environ.get("AUTH_MODE")
    previous_secret = os.environ.get("AUTH_SECRET_KEY")
    os.environ["AUTH_MODE"] = "password"
    os.environ["AUTH_SECRET_KEY"] = "test-secret-key-for-user-admin-slice-0123456789"
    try:
        with TestClient(app) as client:
            login = client.post(
                "/api/auth/login", json={"username": actor, "password": "password-for-tests"}
            )
            assert login.status_code == 200, login.text
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            with connect() as connection:
                connection.execute("UPDATE users SET is_global_admin=false WHERE id=?", [actor])
            cases = (
                ("status", {"account_status": "ACTIVE", "expected_updated_at": NOW.isoformat(), "reason": "deny"}),
                ("global-admin", {"is_global_admin": True, "expected_updated_at": NOW.isoformat(), "reason": "deny"}),
            )
            for path, payload in cases:
                response = client.patch(f"/api/admin/users/{target}/{path}", json=payload, headers=headers)
                assert response.status_code == 403, response.text
                assert response.json()["detail"] == {
                    "code": "PERMISSION_DENIED", "message": "이 작업을 수행할 권한이 없습니다.",
                    "required_permission": "system.user.approve", "project_id": None,
                }
    finally:
        if previous is None:
            os.environ.pop("AUTH_MODE", None)
        else:
            os.environ["AUTH_MODE"] = previous
        if previous_secret is None:
            os.environ.pop("AUTH_SECRET_KEY", None)
        else:
            os.environ["AUTH_SECRET_KEY"] = previous_secret
    with connect() as connection:
        result = connection.execute(
            "SELECT account_status, is_global_admin FROM users WHERE id=?", [target]
        ).fetchone()
        assert result == ("PENDING", False)


@pytest.mark.contract
def test_duckdb_http_user_admin_audit_failure_rolls_back_user_and_invitations(monkeypatch: pytest.MonkeyPatch) -> None:
    from uuid import uuid4
    from app.database import initialize_database
    from app.database_connection import connect
    from app.adapters.persistence import user_administration as persistence

    initialize_database()
    user_id, employee_id = f"rollback-{uuid4().hex[:10]}", f"E{uuid4().hex[:8]}"
    with connect() as connection:
        connection.execute(
            "INSERT INTO users (id, username, password_hash, display_name, employee_id, "
            "legacy_role, account_status, is_global_admin, is_active, created_at, updated_at) "
            "VALUES (?, ?, NULL, ?, ?, 'viewer', 'PENDING', false, true, ?, ?)",
            [user_id, user_id, "Rollback", employee_id, NOW, NOW],
        )
        connection.execute(
            "INSERT INTO project_invitations (id, project_id, employee_id, "
            "display_name_snapshot, desired_role, status, invited_by, invited_at) "
            "VALUES (?, 'project-tv-001', ?, 'Rollback', 'general', 'PENDING_ACCOUNT', "
            "'local-admin', ?)",
            [f"inv-{uuid4().hex[:10]}", employee_id, NOW],
        )
    original = persistence.SQLUserAdministrationUnitOfWork.add_audit

    def fail_after_write(unit: Any, record: Any) -> None:
        original(unit, record)
        raise RuntimeError("injected audit failure")
    monkeypatch.setattr(persistence.SQLUserAdministrationUnitOfWork, "add_audit", fail_after_write)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.patch(
            f"/api/admin/users/{user_id}/status",
            json={"account_status": "ACTIVE", "expected_updated_at": NOW.isoformat(), "reason": "rollback"},
        )
    assert response.status_code == 500
    with connect() as connection:
        assert connection.execute("SELECT account_status FROM users WHERE id=?", [user_id]).fetchone()[0] == "PENDING"
        assert connection.execute(
            "SELECT status FROM project_invitations WHERE employee_id=?", [employee_id]
        ).fetchone()[0] == "PENDING_ACCOUNT"
        assert connection.execute(
            "SELECT count(*) FROM audit_events WHERE action='ACCOUNT_STATUS_CHANGED' "
            "AND json_extract_string(detail_json, '$.target_user_id')=?", [user_id]
        ).fetchone()[0] == 0


@pytest.mark.unit
def test_http_adapter_is_thin_and_routes_keep_legacy_contract() -> None:
    from app.adapters.http.routers import user_administration
    source = Path(user_administration.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert "write_audit_event" not in source
    assert not any(
        isinstance(n, ast.Call)
        and (
            (isinstance(n.func, ast.Attribute) and n.func.attr in {"execute", "connect"})
            or (isinstance(n.func, ast.Name) and n.func.id in {"rows", "connect"})
        )
        for n in ast.walk(tree)
    )
    paths = app.openapi()["paths"]
    expected_operation_ids = {
        ("/api/admin/users/{user_id}/status", "patch"): "update_account_status_api_admin_users__user_id__status_patch",
        ("/api/admin/users/{user_id}/global-admin", "patch"):
            "update_global_admin_api_admin_users__user_id__global_admin_patch",
    }
    for (path, method), operation_id in expected_operation_ids.items():
        assert path in paths and method in paths[path]
        assert paths[path][method]["operationId"] == operation_id
    routes = [
        r for r in app.routes
        if r.path in {"/api/admin/users/{user_id}/status", "/api/admin/users/{user_id}/global-admin"}
    ]
    assert all(r.endpoint.__module__ == user_administration.__name__ for r in routes)


@pytest.mark.unit
def test_legacy_router_no_longer_defines_admin_mutation_handlers_and_includes_new_router() -> None:
    source = Path(__file__).parents[1].joinpath("app", "routers", "access_control.py").read_text(encoding="utf-8")
    assert "def update_account_status(" not in source
    assert "def update_global_admin(" not in source
    assert "router.include_router(user_administration_router)" in source
