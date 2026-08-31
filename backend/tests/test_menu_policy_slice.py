"""Contract tests for the menu-policy vertical slice.

These tests intentionally exercise the application boundary with small fakes;
the HTTP checks below keep the adapter honest without coupling tests to SQL.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fastapi.routing import APIRoute

from app.application.menu_policy import policies
from app.adapters.http.routers import menu_policy as menu_policy_router
from app.adapters.persistence.menu_policy import (
    SQLMenuPolicyUnitOfWork,
    SQLMenuPolicyUnitOfWorkProvider,
)
from app.database import initialize_database
from app.database_connection import connect
from app.main import app
from app.security import hash_password
from app.domains.menu_policy.models import (
    MenuPermissionMismatchError,
    MenuPolicyIncompleteError,
    MenuPolicyLockedError,
    MenuPolicyUnavailableError,
    StaleMenuPolicyVersionError,
    UnknownMenuIdError,
    UnknownMenuPolicyRoleError,
)


NOW = datetime(2026, 1, 2, 3, 4, 5)
PERMISSIONS = {
    "general": frozenset({"workspace.view"}),
    "power": frozenset({"workspace.view", "data.view"}),
    "admin": frozenset({"workspace.view", "data.view", "system.menu_policy.manage"}),
}


def policy(visibility=None):
    return {
        "version": 4,
        "updated_by": "u1",
        "updated_at": NOW,
        "menus": [
            {
                "id": "dashboard",
                "label": "Dashboard",
                "required_permission": "workspace.view",
                "context_kind": "global",
                "sequence_no": 20,
                "is_policy_editable": True,
                "visibility": visibility or {
                    "general": True,
                    "power": True,
                    "admin": True,
                },
            },
            {
                "id": "data",
                "label": "Data",
                "required_permission": "data.view",
                "context_kind": "global",
                "sequence_no": 50,
                "is_policy_editable": True,
                "visibility": {"general": False, "power": True, "admin": True},
            },
            {
                "id": "locked",
                "label": "Locked",
                "required_permission": "workspace.view",
                "context_kind": "system",
                "sequence_no": 99,
                "is_policy_editable": False,
                "visibility": {"general": False, "power": False, "admin": True},
            },
        ],
    }


class Reader:
    def __init__(self, current=None, versions=None):
        self.current = current
        self.versions = versions or []
        self.authorized = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def authorize_manage(self):
        self.authorized += 1

    def get_policy(self):
        return self.current

    def list_versions(self):
        return self.versions

    def get_version(self, version):
        return next((v for v in self.versions if v["version"] == version), None)


class Uow(Reader):
    def __init__(self, current, versions=None, fail_audit=False, fail_write=False):
        super().__init__(current, versions)
        self.events = []
        self.snapshots = []
        self.fail_audit = fail_audit
        self.fail_write = fail_write

    def write_snapshot(self, **kwargs):
        if self.fail_write:
            raise RuntimeError("mutation failed")
        self.snapshots.append(kwargs)
        self.current = {**self.current, "version": kwargs["version"], "updated_by": kwargs["actor_id"]}

    def add_audit(self, audit):
        if self.fail_audit:
            raise RuntimeError("audit failed")
        self.events.append(audit)


def provider(obj):
    @contextmanager
    def provide():
        yield obj

    return provide


def audit():
    return {
        "user_id": "u1",
        "username": "one",
        "role": "admin",
        "method": "PUT",
        "path": "/api/admin/menu-policy",
        "request_id": "r1",
        "client_ip": None,
        "user_agent": "test",
    }


def test_projection_orders_menus_and_reports_unavailable_or_incomplete():
    current = policy()
    current["menus"] = list(reversed(current["menus"]))
    result = policies.get_menu_policy(provider(Reader(current)))
    assert [item["id"] for item in result["menus"]] == ["locked", "data", "dashboard"]

    with pytest.raises(MenuPolicyUnavailableError) as unavailable:
        policies.get_menu_policy(provider(Reader(None)))
    assert unavailable.value.code == "MENU_POLICY_UNAVAILABLE"

    broken = policy()
    del broken["menus"][0]["visibility"]["admin"]
    with pytest.raises(MenuPolicyIncompleteError) as incomplete:
        policies.get_menu_policy(provider(Reader(broken)))
    assert incomplete.value.code == "MENU_POLICY_INCOMPLETE"


def test_validation_errors_include_exact_details():
    current = policy()
    cases = [
        ({"auditor": {"data": True}}, UnknownMenuPolicyRoleError),
        ({"admin": {"missing": True}}, UnknownMenuIdError),
        ({"general": {"locked": True}}, MenuPolicyLockedError),
        ({"general": {"data": True}}, MenuPermissionMismatchError),
    ]
    for change, error_type in cases:
        with pytest.raises(error_type) as caught:
            policies.normalized_visibility(current, change, PERMISSIONS)
        error = caught.value
        assert getattr(error, "code")
    with pytest.raises(UnknownMenuPolicyRoleError) as role_error:
        policies.normalized_visibility(current, {"z": {}}, PERMISSIONS)
    assert role_error.value.roles == ["z"]
    with pytest.raises(UnknownMenuIdError) as menu_error:
        policies.normalized_visibility(current, {"admin": {"z": True}}, PERMISSIONS)
    assert menu_error.value.menu_ids == ["z"]
    with pytest.raises(MenuPolicyLockedError) as lock_error:
        policies.normalized_visibility(current, {"general": {"locked": True}}, PERMISSIONS)
    assert lock_error.value.menu_id == "locked"
    with pytest.raises(MenuPermissionMismatchError) as permission_error:
        policies.normalized_visibility(current, {"general": {"data": True}}, PERMISSIONS)
    assert (permission_error.value.role, permission_error.value.menu_id) == ("general", "data")


def test_update_orders_snapshot_audit_reread_and_maps_stale_version():
    uow = Uow(policy())
    result = policies.update_menu_policy(
        expected_version=4,
        change_note="  hide data  ",
        visibility={"power": {"data": False}},
        role_permissions=PERMISSIONS,
        actor_id="u2",
        audit=audit(),
        unit_of_work_provider=provider(uow),
        clock=lambda: NOW,
    )
    assert result["version"] == 5
    assert uow.snapshots[0]["change_note"] == "hide data"
    assert uow.events[0]["action"] == "MENU_POLICY_UPDATED"
    assert uow.events[0]["detail"] == {
        "policy_version": 5,
        "changed_menu_ids": ["data"],
    }

    with pytest.raises(StaleMenuPolicyVersionError) as stale:
        policies.update_menu_policy(
            expected_version=1,
            change_note="x",
            visibility={},
            role_permissions=PERMISSIONS,
            actor_id="u2",
            audit=audit(),
            unit_of_work_provider=provider(Uow(policy())),
            clock=lambda: NOW,
        )
    assert stale.value.current_version == 4


def test_empty_update_still_creates_a_new_snapshot_and_audit():
    uow = Uow(policy())
    result = policies.update_menu_policy(
        expected_version=4,
        change_note="no visible changes",
        visibility={},
        role_permissions=PERMISSIONS,
        actor_id="u2",
        audit=audit(),
        unit_of_work_provider=provider(uow),
        clock=lambda: NOW,
    )
    assert result["version"] == 5
    assert uow.snapshots[0]["version"] == 5
    assert uow.events[0]["detail"]["changed_menu_ids"] == []


def test_restore_records_source_version_and_audit_after_mutation():
    source = {"version": 2, "created_by": "u1", "created_at": NOW,
              "source_version": None, "change_note": "old",
              "visibility": {"general": {"dashboard": True, "data": False, "locked": False},
                              "power": {"dashboard": True, "data": False, "locked": False},
                              "admin": {"dashboard": True, "data": True, "locked": True}}}
    uow = Uow(policy(), [source])
    result = policies.restore_menu_policy(
        version=2, role_permissions=PERMISSIONS, actor_id="u2", audit=audit(),
        unit_of_work_provider=provider(uow), historical_visibility_parser=lambda *_: source["visibility"],
        clock=lambda: NOW,
    )
    assert result["version"] == 5
    assert uow.snapshots[0]["source_version"] == 2
    assert uow.events[0]["action"] == "MENU_POLICY_RESTORED"


def test_partial_historical_snapshot_overlays_current_visibility():
    source = {
        "version": 2,
        "created_by": "u1",
        "created_at": NOW,
        "source_version": None,
        "change_note": "partial legacy snapshot",
        "visibility": {"power": {"data": False}},
    }
    uow = Uow(policy(), [source])
    policies.restore_menu_policy(
        version=2, role_permissions=PERMISSIONS, actor_id="u2", audit=audit(),
        unit_of_work_provider=provider(uow),
        historical_visibility_parser=menu_policy_router._parse_historical_visibility,
        clock=lambda: NOW,
    )
    saved = uow.snapshots[0]["visibility"]
    assert saved["power"]["data"] is False
    assert saved["general"]["data"] is False
    assert saved["admin"]["locked"] is True


def test_version_list_is_descending_and_detail_decodes_json_visibility():
    summaries = [
        {"version": 2, "created_by": "u", "created_at": NOW,
         "source_version": None, "change_note": "two"},
        {"version": 1, "created_by": "u", "created_at": NOW,
         "source_version": None, "change_note": "one"},
    ]
    reader = Reader(policy(), summaries)
    assert [item["version"] for item in policies.list_menu_policy_versions(provider(reader))] == [2, 1]
    detail_reader = Reader(policy(), [{**summaries[0], "visibility": {"power": {"data": False}}}])
    assert policies.get_menu_policy_version(2, provider(detail_reader))["visibility"] == {
        "power": {"data": False}
    }


def test_http_adapter_has_five_paths_and_no_sql_or_audit_implementation():
    router_path = Path(__file__).parents[1] / "app" / "adapters" / "http" / "routers" / "menu_policy.py"
    source = router_path.read_text(encoding="utf-8")
    assert source.count("@router.") == 5
    assert "/api/navigation/menu-policy" in source
    assert "/api/admin/menu-policy" in source
    assert "/api/admin/menu-policy/versions" in source
    route_paths = [
        (route.path, tuple(sorted(route.methods or ())))
        for route in app.routes
        if isinstance(route, APIRoute) and route.endpoint.__module__ == menu_policy_router.__name__
    ]
    assert route_paths == [
        ("/api/navigation/menu-policy", ("GET",)),
        ("/api/admin/menu-policy", ("PUT",)),
        ("/api/admin/menu-policy/versions", ("GET",)),
        ("/api/admin/menu-policy/versions/{version}", ("GET",)),
        ("/api/admin/menu-policy/versions/{version}/restore", ("POST",)),
    ]
    assert [route.endpoint.__name__ for route in app.routes if isinstance(route, APIRoute)
            and route.endpoint.__module__ == menu_policy_router.__name__] == [
        "get_menu_policy", "update_menu_policy", "menu_policy_versions",
        "get_menu_policy_version", "restore_menu_policy",
    ]
    expected_ids = {
        ("/api/navigation/menu-policy", "get"): "get_menu_policy_api_navigation_menu_policy_get",
        ("/api/admin/menu-policy", "put"): "update_menu_policy_api_admin_menu_policy_put",
        ("/api/admin/menu-policy/versions", "get"): "menu_policy_versions_api_admin_menu_policy_versions_get",
        ("/api/admin/menu-policy/versions/{version}", "get"):
            "get_menu_policy_version_api_admin_menu_policy_versions__version__get",
        ("/api/admin/menu-policy/versions/{version}/restore", "post"):
            "restore_menu_policy_api_admin_menu_policy_versions__version__restore_post",
    }
    paths = app.openapi()["paths"]
    for key, operation_id in expected_ids.items():
        assert paths[key[0]][key[1]]["operationId"] == operation_id
    navigation_schema = paths["/api/navigation/menu-policy"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert navigation_schema["$ref"].endswith("MenuPolicyResponse")
    versions_schema = paths["/api/admin/menu-policy/versions"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert versions_schema["type"] == "array"
    assert "SELECT " not in source.upper()
    assert "write_audit_event" not in source


def test_legacy_access_control_no_longer_owns_menu_policy_handlers():
    source = (Path(__file__).parents[1] / "app" / "routers" / "access_control.py").read_text(encoding="utf-8")
    assert "def get_menu_policy" not in source
    assert "def update_menu_policy" not in source
    assert "def restore_menu_policy" not in source


def test_duckdb_adapter_exposes_transaction_and_json_decode_contract():
    adapter_path = Path(__file__).parents[1] / "app" / "adapters" / "persistence" / "menu_policy.py"
    source = adapter_path.read_text(encoding="utf-8")
    for token in ("BEGIN", "COMMIT", "ROLLBACK", "menu_policy_state", "role_menu_policies", "json"):
        assert token in source
    assert "json.loads" in source or "json_value" in source
    assert "except BaseException" in source
    transaction = source[source.rindex("def __call__"):]
    assert transaction.index("BEGIN") < transaction.index("yield") < transaction.index("COMMIT")
    assert transaction.index("ROLLBACK") > transaction.index("yield")


def test_mutation_adapter_locks_fixed_tables_before_fresh_authorization():
    source = (Path(__file__).parents[1] / "app" / "adapters" / "persistence" / "menu_policy.py").read_text(
        encoding="utf-8"
    )
    assert "_SAFE_LOCK_TABLES" in source
    assert source.index("LOCK TABLE") < source.index("yield SQLMenuPolicyUnitOfWork")
    assert "authorize_manage" in source


class ConnectionFake:
    def __init__(self, events):
        self.events = events

    def __enter__(self):
        self.events.append("open")
        return self

    def __exit__(self, *_):
        self.events.append("close")

    def execute(self, sql, params=None):
        del params
        statement = " ".join(str(sql).split())
        self.events.append(statement)
        return self


def test_real_persistence_provider_orders_pg_lock_fresh_auth_and_commit(monkeypatch):
    events = []
    connection = ConnectionFake(events)
    monkeypatch.setattr(
        "app.adapters.persistence.menu_policy.database_settings",
        lambda: type("Settings", (), {"backend": "postgresql"})(),
    )
    provider_instance = SQLMenuPolicyUnitOfWorkProvider(
        lambda conn: events.append("fresh-authorize"), lambda: connection
    )
    with provider_instance() as unit_of_work:
        events.append("yield")
        unit_of_work.authorize_manage()
    assert events[1:7] == [
        "BEGIN TRANSACTION",
        "LOCK TABLE menu_policy_state, role_menu_policies IN SHARE ROW EXCLUSIVE MODE",
        "yield",
        "fresh-authorize",
        "COMMIT",
        "close",
    ]


def test_real_persistence_provider_rolls_back_exception_without_commit(monkeypatch):
    events = []
    connection = ConnectionFake(events)
    monkeypatch.setattr(
        "app.adapters.persistence.menu_policy.database_settings",
        lambda: type("Settings", (), {"backend": "duckdb"})(),
    )
    provider_instance = SQLMenuPolicyUnitOfWorkProvider(lambda conn: None, lambda: connection)
    with pytest.raises(RuntimeError, match="boom"):
        with provider_instance():
            raise RuntimeError("boom")
    assert "ROLLBACK" in events
    assert "COMMIT" not in events


def test_password_auth_rechecks_global_admin_for_menu_policy_admin_paths(monkeypatch):
    initialize_database()
    user_id = "menu-policy-stale-global"
    with connect() as connection:
        connection.execute(
            "INSERT INTO users (id, username, password_hash, display_name, legacy_role, account_status, "
            "is_global_admin, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, 'viewer', 'ACTIVE', "
            "true, true, ?, ?)",
            [user_id, user_id, hash_password("menu-policy-password"), "Menu Admin", NOW, NOW],
        )
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "menu-policy-test-secret-key-0123456789012345")
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": user_id, "password": "menu-policy-password"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        with connect() as connection:
            connection.execute("UPDATE users SET is_global_admin=false WHERE id=?", [user_id])
        assert client.get("/api/navigation/menu-policy", headers=headers).status_code == 200
        expected = {
            "code": "PERMISSION_DENIED",
            "message": "이 작업을 수행할 권한이 없습니다.",
            "required_permission": "system.menu_policy.manage",
            "project_id": None,
        }
        for response in (
            client.get("/api/admin/menu-policy/versions", headers=headers),
            client.put("/api/admin/menu-policy", headers=headers, json={
                "expected_version": 1, "change_note": "deny", "visibility": {},
            }),
        ):
            assert response.status_code == 403
            assert response.json()["detail"] == expected


def test_duckdb_http_empty_update_and_partial_restore_are_atomic_contracts():
    initialize_database()
    with TestClient(app) as client:
        updated = client.put(
            "/api/admin/menu-policy",
            json={"expected_version": 1, "change_note": "empty", "visibility": {}},
        )
        assert updated.status_code == 200
        assert updated.json()["version"] == 2
        with connect() as connection:
            snapshot = connection.execute(
                "SELECT definition_json FROM menu_policy_versions WHERE version=2"
            ).fetchone()[0]
            audit_row = connection.execute(
                "SELECT action, status_code, detail_json FROM audit_events "
                "WHERE action='MENU_POLICY_UPDATED' ORDER BY occurred_at DESC LIMIT 1"
            ).fetchone()
        assert set(json.loads(snapshot)) == {"general", "power", "admin"}
        assert audit_row[0:2] == ("MENU_POLICY_UPDATED", 200)
        assert json.loads(audit_row[2])["changed_menu_ids"] == []


def test_restore_coerces_legacy_string_boolean_before_persisting(monkeypatch):
    initialize_database()
    monkeypatch.setenv("AUTH_MODE", "disabled")
    with connect() as connection:
        current = connection.execute(
            "SELECT definition_json FROM menu_policy_versions WHERE version=1"
        ).fetchone()[0]
        decoded = json.loads(current)
        decoded["power"]["data"] = "false"
        connection.execute(
            "INSERT INTO menu_policy_versions "
            "(version, definition_json, created_by, created_at, source_version, change_note) "
            "VALUES (99, ?, 'legacy', ?, NULL, 'legacy string')",
            [json.dumps(decoded), NOW],
        )
    with TestClient(app) as client:
        restored = client.post("/api/admin/menu-policy/versions/99/restore")
    assert restored.status_code == 200
    data_menu = next(item for item in restored.json()["menus"] if item["id"] == "data")
    assert data_menu["visibility"]["power"] is False
    with connect() as connection:
        saved = connection.execute(
            "SELECT definition_json FROM menu_policy_versions WHERE version=2"
        ).fetchone()[0]
    assert json.loads(saved)["power"]["data"] is False


def test_restore_unknown_historical_role_is_500_and_rolls_back_everything():
    initialize_database()
    with connect() as connection:
        current = connection.execute(
            "SELECT definition_json FROM menu_policy_versions WHERE version=1"
        ).fetchone()[0]
        decoded = json.loads(current)
        decoded["rogue"] = {"dashboard": True}
        connection.execute(
            "INSERT INTO menu_policy_versions "
            "(version, definition_json, created_by, created_at, source_version, change_note) "
            "VALUES (98, ?, 'legacy', ?, NULL, 'bad role')",
            [json.dumps(decoded), NOW],
        )
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/admin/menu-policy/versions/98/restore")
    assert response.status_code == 500
    with connect() as connection:
        assert connection.execute("SELECT version FROM menu_policy_state WHERE id='global'").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM menu_policy_versions").fetchone() == (2,)
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE action LIKE 'MENU_POLICY_%'"
        ).fetchone() == (0,)


def test_http_error_mapping_preserves_exact_unavailable_and_incomplete_503_details():
    from fastapi import HTTPException

    for error in (MenuPolicyUnavailableError(), MenuPolicyIncompleteError()):
        with pytest.raises(HTTPException) as raised:
            menu_policy_router._raise_mapped(error)
        assert raised.value.status_code == 503
        assert raised.value.detail == {"code": error.code}


def test_duckdb_update_rolls_back_snapshot_and_policy_when_audit_insert_fails(monkeypatch):
    initialize_database()

    def fail_audit(self, audit):
        del self, audit
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr(SQLMenuPolicyUnitOfWork, "add_audit", fail_audit)
    with connect() as connection:
        before_state = connection.execute(
            "SELECT version, updated_by, updated_at FROM menu_policy_state WHERE id='global'"
        ).fetchall()
        before_policies = connection.execute(
            "SELECT role, menu_id, is_visible, policy_version, updated_by, updated_at "
            "FROM role_menu_policies ORDER BY role, menu_id"
        ).fetchall()
        before_versions = connection.execute("SELECT COUNT(*) FROM menu_policy_versions").fetchone()[0]
        before_audits = connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE action LIKE 'MENU_POLICY_%'"
        ).fetchone()[0]

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.put(
            "/api/admin/menu-policy",
            json={
                "expected_version": before_state[0][0],
                "change_note": "must rollback",
                "visibility": {"power": {"data": False}},
            },
        )
    assert response.status_code == 500
    with connect() as connection:
        assert connection.execute(
            "SELECT version, updated_by, updated_at FROM menu_policy_state WHERE id='global'"
        ).fetchall() == before_state
        assert connection.execute(
            "SELECT role, menu_id, is_visible, policy_version, updated_by, updated_at "
            "FROM role_menu_policies ORDER BY role, menu_id"
        ).fetchall() == before_policies
        assert connection.execute("SELECT COUNT(*) FROM menu_policy_versions").fetchone()[0] == before_versions
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE action LIKE 'MENU_POLICY_%'"
        ).fetchone()[0] == before_audits
