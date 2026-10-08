"""Per-user notifications (docs/features/notifications.md, migration 0040).

Isolated DuckDB test database and synthetic users/folders only; drive events run
against the in-memory fake adapter (``drive_fakes``) through the D2/D3 fixtures,
which also assert that no drive call happened while a DB connection was held.
"""
from __future__ import annotations

import ast
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.database_connection import connect
from app.main import app
from app.access_policy import RESULT_IMPORT
from app.security import hash_password
from app.services import dashboard_capture, notifications, project_cleanup
from app.services.drive import upload_queue

from drive_fakes import ErrorCode
from test_drive_reads import CHANGED_BYTES, CSV, OPTION, _register, _sync, scx  # noqa: F401 - fixture
from test_drive_writes import (FINAL, _complete, _confirm, _designate, _drop, _final_ctx, _job, _preview, _settle,  # noqa: F401
                               _stage, scxw)

pytestmark = pytest.mark.duckdb_integration

BACKEND = Path(__file__).resolve().parents[1]
PASSWORD = "notification-user-password"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _user(conn, name: str, *, status: str = "ACTIVE", admin: bool = False, active: bool = True) -> str:
    user_id = f"nt-{name}-{uuid.uuid4().hex[:6]}"
    conn.execute("INSERT INTO users (id, username, password_hash, display_name, legacy_role, account_status, "
                 "is_global_admin, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, 'viewer', ?, ?, ?, ?, ?)",
                 [user_id, user_id, hash_password(PASSWORD), name, status, admin, active, _now(), _now()])
    return user_id


def _member(conn, project_id: str, user_id: str, role: str) -> None:
    if not conn.execute("SELECT 1 FROM projects WHERE id=?", [project_id]).fetchone():
        conn.execute("INSERT INTO projects(id,name,product_name,description,created_at) VALUES(?,?,?,?,?)",
                     [project_id, project_id, "", "", _now()])
    conn.execute("INSERT INTO project_memberships (id, project_id, user_id, role, created_by, created_at, updated_by, "
                 "updated_at) VALUES (?, ?, ?, ?, 'test', ?, 'test', ?)",
                 [f"pm-{uuid.uuid4().hex}", project_id, user_id, role, _now(), _now()])


def _rows(user_id: str | None = None, type: str | None = None, request_id: str | None = None) -> list[dict]:
    where, params = [], []
    if request_id:
        where.append("request_id=?"); params.append(request_id)
    if user_id:
        where.append("user_id=?"); params.append(user_id)
    if type:
        where.append("type=?"); params.append(type)
    with connect() as conn:
        found = conn.execute("SELECT user_id,type,severity,title,body,link,project_id,request_id,dedupe_key,read_at "
                             "FROM notifications" + (" WHERE " + " AND ".join(where) if where else "")
                             + " ORDER BY created_at", params).fetchall()
    keys = ("user_id", "type", "severity", "title", "body", "link", "project_id", "request_id", "dedupe_key", "read_at")
    return [dict(zip(keys, row)) for row in found]


# --- recipients, dedupe, retention ----------------------------------------------------------------

def test_recipients_follow_account_state_membership_and_permission():
    project = f"project-nt-{uuid.uuid4().hex[:6]}"
    with connect() as conn:
        admin = _user(conn, "admin", admin=True)
        power = _user(conn, "power")
        general = _user(conn, "general")
        suspended = _user(conn, "suspended", status="SUSPENDED")
        pending = _user(conn, "pending", status="PENDING")
        inactive = _user(conn, "inactive", active=False)
        outsider = _user(conn, "outsider")
        other_project = _user(conn, "other")
        for user_id, role in ((power, "power"), (general, "general"), (suspended, "power"), (pending, "power"),
                              (inactive, "admin")):
            _member(conn, project, user_id, role)
        _member(conn, f"{project}-other", other_project, "admin")
        viewers = notifications.recipients(conn, project_id=project)
        importers = notifications.recipients(conn, project_id=project, permission=RESULT_IMPORT)
        named = notifications.recipients(conn, project_id=project, permission=RESULT_IMPORT, include=[admin, outsider])
        named_view = notifications.recipients(conn, project_id=project, members=False, include=[outsider, suspended])
        admins = notifications.recipients(conn, permission=None, members=False, admins=True)
    assert set(viewers) == {power, general}                      # never suspended, pending, inactive or other projects
    assert set(importers) == {power}                             # result.import needs power/admin role
    assert set(named) == {power, admin}                          # a named global admin qualifies, an outsider does not
    assert set(named_view) == {outsider}                         # company view permission covers any ACTIVE user
    assert admin in admins and power not in admins


def test_dedupe_skips_or_coalesces_unread_repeats_and_read_rows_allow_a_new_one():
    with connect() as conn:
        user = _user(conn, "dedupe")
        assert notifications.emit(conn, [user], type="DRIVE_UPLOAD", title="A", dedupe_key="k1") == 1
        assert notifications.emit(conn, [user], type="DRIVE_UPLOAD", title="B", dedupe_key="k1") == 0
        assert notifications.emit(conn, [user], type="NEW_RESULTS", title="C1", body="1", dedupe_key="k2", coalesce=True) == 1
        assert notifications.emit(conn, [user], type="NEW_RESULTS", title="C2", body="2", dedupe_key="k2", coalesce=True) == 1
    rows = _rows(user)
    assert [(row["title"], row["body"]) for row in rows] == [("A", None), ("C2", "2")]
    with connect() as conn:
        assert notifications.mark_read(conn, user, all=True) == 2
        assert notifications.emit(conn, [user], type="DRIVE_UPLOAD", title="A again", dedupe_key="k1") == 1
        assert notifications.unread_count(conn, user) == 1


def test_emit_never_raises_and_ignores_unknown_types():
    with connect() as conn:
        user = _user(conn, "safe")
        assert notifications.emit(conn, [user], type="UNKNOWN", title="x") == 0
        assert notifications.emit(conn, [user], type="FINAL", title="x", severity="LOUD") == 0
        assert notifications.emit(conn, [], type="FINAL", title="x") == 0
    assert _rows(user) == []


def test_retention_drops_rows_older_than_90_days_and_keeps_1000_per_user(monkeypatch):
    monkeypatch.setattr(notifications, "MAX_PER_USER", 5)
    with connect() as conn:
        user, other = _user(conn, "retention"), _user(conn, "neighbour")
        old = _now() - timedelta(days=notifications.RETENTION_DAYS + 1)
        conn.execute("INSERT INTO notifications (id,user_id,type,severity,title,created_at) VALUES (?,?,?,?,?,?)",
                     [f"nt-old-{user}", user, "FINAL", "INFO", "old", old])
        conn.execute("INSERT INTO notifications (id,user_id,type,severity,title,created_at) VALUES (?,?,?,?,?,?)",
                     [f"nt-neighbour-{other}", other, "FINAL", "INFO", "kept", old + timedelta(days=200)])
        assert notifications.list_for_user(conn, user)["total"] == 0          # hidden on read before any prune
        for index in range(7):
            conn.execute("INSERT INTO notifications (id,user_id,type,severity,title,created_at) VALUES (?,?,?,?,?,?)",
                         [f"nt-{index}-{user}", user, "FINAL", "INFO", f"n{index}", _now() - timedelta(minutes=10 - index)])
        notifications.emit(conn, [user], type="FINAL", title="newest")
        titles = [row[0] for row in conn.execute("SELECT title FROM notifications WHERE user_id=? ORDER BY created_at",
                                                 [user]).fetchall()]
        assert titles == ["n3", "n4", "n5", "n6", "newest"]                  # 5 newest kept, old row deleted
        assert conn.execute("SELECT count(*) FROM notifications WHERE user_id=?", [other]).fetchone()[0] == 1


# --- API ---------------------------------------------------------------------------------------------

@pytest.fixture
def two_users(monkeypatch, password_auth_bootstrap_admin):
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "notifications-isolated-test-secret-at-least-32")
    with connect() as conn:
        first, second = _user(conn, "first"), _user(conn, "second")
    clients = []
    with TestClient(app) as one, TestClient(app) as two:
        for client, user_id in ((one, first), (two, second)):
            login = client.post("/api/auth/login", json={"username": user_id, "password": PASSWORD})
            assert login.status_code == 200, login.text
            client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
            clients.append((client, user_id))
        yield clients


def test_api_lists_filters_and_marks_only_the_callers_notifications(two_users):
    (one, first), (two, second) = two_users
    with connect() as conn:
        notifications.emit(conn, [first], type="FINAL", severity="SUCCESS", title="final", link="/workspace/requests")
        notifications.emit(conn, [first], type="NEW_RESULTS", title="results")
        notifications.emit(conn, [second], type="FINAL", title="theirs")
    listed = one.get("/api/notifications").json()
    assert [item["title"] for item in listed["items"]] == ["results", "final"] and listed["unread_count"] == 2
    assert {item["type"] for item in listed["types"]} == set(notifications.TYPES)
    assert [item["title"] for item in one.get("/api/notifications", params={"type": "FINAL"}).json()["items"]] == ["final"]
    assert one.get("/api/notifications", params={"type": "NOPE"}).status_code == 422
    theirs = two.get("/api/notifications").json()["items"][0]["id"]
    mine = listed["items"][0]["id"]
    marked = one.post("/api/notifications/read", json={"ids": [mine, theirs]}).json()
    assert marked == {"updated": 1, "unread_count": 1}                   # another user's id is ignored
    assert two.get("/api/notifications/unread-count").json() == {"unread_count": 1}
    unread = one.get("/api/notifications", params={"unread_only": True}).json()
    assert [item["title"] for item in unread["items"]] == ["final"]
    assert one.post("/api/notifications/read", json={}).status_code == 422
    assert one.post("/api/notifications/read", json={"all": True}).json() == {"updated": 1, "unread_count": 0}
    assert one.get("/api/notifications/unread-count").json() == {"unread_count": 0}


def test_api_requires_an_active_account(two_users):
    (one, first), _second = two_users
    with connect() as conn:
        conn.execute("UPDATE users SET account_status='SUSPENDED' WHERE id=?", [first])
    assert one.get("/api/notifications/unread-count").status_code in {401, 403}


# --- drive events (fake gateway) -----------------------------------------------------------------------

def _admin_id(fixture) -> str:
    return fixture.client.get("/api/auth/me").json()["id"]


def test_upload_batch_outcomes_notify_the_user_who_started_it(scxw):
    project_id, request_id = _register(scxw)
    me = _admin_id(scxw)
    with connect() as conn:
        watcher = _user(conn, "watcher")
        _member(conn, project_id, watcher, "power")
    done = _drop(scxw, project_id, request_id, [("5_Face/new.csv", b"new\n")])
    _complete(scxw, done["session_id"])
    _settle()
    rows = _rows(type="DRIVE_UPLOAD", request_id=request_id)
    assert [(row["user_id"], row["severity"]) for row in rows] == [(me, "SUCCESS")]      # the starter only
    assert rows[0]["request_id"] == request_id and rows[0]["link"].startswith("/workspace/requests?")
    assert "view=case_results" in rows[0]["link"] and "1/1" in rows[0]["body"]
    clash = _drop(scxw, project_id, request_id, [("5_Face/late_a.csv", b"a\n"), ("5_Face/late_b.csv", b"b\n")])
    _complete(scxw, clash["session_id"])
    scxw.add(f"{OPTION}/5_Face/late_a.csv", b"someone else's file\n")   # appears after publish: CONFLICT
    _settle()
    severities = [(row["severity"], row["title"].split(" · ")[0]) for row in _rows(me, "DRIVE_UPLOAD")]
    assert severities == [("SUCCESS", "드라이브 업로드 완료"), ("WARNING", "드라이브 업로드 일부 완료")]
    assert _rows(watcher) == []


def test_auth_required_pause_notifies_global_admins_once_per_pause(scxw):
    project_id, request_id = _register(scxw)
    me = _admin_id(scxw)
    session = _drop(scxw, project_id, request_id, [("2_Face/auth.csv", b"auth\n")])
    scxw.drive.fail_next["upload_new"] = ErrorCode.AUTH_REQUIRED
    _complete(scxw, session["session_id"])
    _settle()
    paused = _rows(me, "DRIVE_QUEUE_PAUSED")
    assert len(paused) == 1 and paused[0]["severity"] == "WARNING" and paused[0]["link"] == notifications.ADMIN_DRIVE_LINK
    assert _rows(me, "DRIVE_UPLOAD") == []                        # a paused batch is not finished
    assert scxw.client.put("/api/admin/drive/credentials", content=__import__("test_drive_reads")._bundle()).status_code == 200
    _settle()
    assert [row["severity"] for row in _rows(me, "DRIVE_UPLOAD")] == ["SUCCESS"]
    # a second pause while the first notification is unread does not add another one
    again = _drop(scxw, project_id, request_id, [("2_Face/auth2.csv", b"auth2\n")])
    scxw.drive.fail_next["upload_new"] = ErrorCode.AUTH_REQUIRED
    _complete(scxw, again["session_id"])
    _settle()
    assert len(_rows(me, "DRIVE_QUEUE_PAUSED")) == 1


def test_final_completion_and_failure_notify_the_designating_user_and_members(scxw):
    ctx = _final_ctx(scxw)
    me = _admin_id(scxw)
    with connect() as conn:
        member = _user(conn, "member")
        _member(conn, ctx["project_id"], member, "general")
    plan = _designate(scxw, ctx)
    done = _rows(type="FINAL", request_id=ctx["request_id"])
    assert {(row["user_id"], row["severity"]) for row in done} == {(me, "SUCCESS"), (member, "SUCCESS")}
    assert all(row["dedupe_key"] == f"final:{plan['operation_id']}:COMPLETE" for row in done)
    # a Final whose source changes after confirm fails
    second = _preview(scxw, ctx)
    _stage(scxw, ctx, second["operation_id"])
    assert _confirm(scxw, ctx, second["operation_id"]).status_code == 200
    scxw.drive.modify_file(scxw.path(f"{OPTION}/3_Face/part.inc"), b"edited after confirm\n")
    _settle()
    assert _job(scxw, ctx, second["operation_id"])["state"] == "FAILED"
    failed = [row for row in _rows(type="FINAL", request_id=ctx["request_id"]) if row["severity"] == "ERROR"]
    assert {row["user_id"] for row in failed} == {me, member}
    assert "FINALIZATION_SOURCE_STALE" in failed[0]["body"]


def test_stopped_summary_file_asks_importers_for_a_repair(scxw):
    ctx = _final_ctx(scxw)
    me = _admin_id(scxw)
    with connect() as conn:
        viewer = _user(conn, "viewer")
        _member(conn, ctx["project_id"], viewer, "general")
    plan = _preview(scxw, ctx)
    # someone else's file already holds the first designation name: the summary upload stops (CONFLICT)
    scxw.add(f"{FINAL}/.finalizations/designations/00000001-{plan['operation_id']}.json", b"{}")
    _stage(scxw, ctx, plan["operation_id"])
    assert _confirm(scxw, ctx, plan["operation_id"]).status_code == 200
    _settle()
    rows = _rows(type="FINAL_SUMMARY", request_id=ctx["request_id"])
    assert [(row["user_id"], row["severity"]) for row in rows] == [(me, "WARNING")]       # general role cannot repair
    assert "CONFLICT" in rows[0]["body"]


def test_drive_source_changes_and_missing_sources_notify_importers(scx):
    project_id, request_id = _register(scx)
    me = _admin_id(scx)
    with connect() as conn:
        viewer = _user(conn, "viewer")
        _member(conn, project_id, viewer, "general")
    _sync(scx, project_id, request_id)
    scx.drive.modify_file(scx.path(f"{OPTION}/2_Face/{CSV}"), CHANGED_BYTES)
    assert _sync(scx, project_id, request_id)["drive"]["pending_changes"] == 1
    assert _sync(scx, project_id, request_id)["drive"]["pending_changes"] == 1          # unchanged: no repeat
    changed = _rows(type="DRIVE_SOURCE_CHANGED", request_id=request_id)
    assert [(row["user_id"], row["request_id"]) for row in changed] == [(me, request_id)]
    assert "1개" in changed[0]["body"]
    scx.drive.delete(scx.path(f"{OPTION}/3_Face"))
    assert _sync(scx, project_id, request_id)["drive"]["missing"] == 1
    missing = _rows(type="DRIVE_SOURCE_MISSING", request_id=request_id)
    assert [row["user_id"] for row in missing] == [me]
    assert _rows(viewer) == [] or {row["type"] for row in _rows(viewer)} == {"NEW_RESULTS"}


def test_auto_sync_new_scenes_notify_request_members_once_per_sync(scx):
    project_id, request_id = _register(scx)
    me = _admin_id(scx)
    with connect() as conn:
        viewer, outsider = _user(conn, "viewer"), _user(conn, "outsider")
        _member(conn, project_id, viewer, "general")
    _sync(scx, project_id, request_id)
    before = len(_rows(type="NEW_RESULTS", request_id=request_id))
    scx.add(f"{OPTION}/6_Corner/{CSV}")
    scx.add(f"{OPTION}/7_Edge/{CSV}")
    added = _sync(scx, project_id, request_id)
    assert added["status"] == "REFRESHED"
    rows = _rows(type="NEW_RESULTS", request_id=request_id)[before:]
    assert {row["user_id"] for row in rows} == {me, viewer} and outsider not in {row["user_id"] for row in rows}
    assert len(rows) == 2 and "Scene 2개" in rows[0]["body"]            # one per user for the sync
    assert _sync(scx, project_id, request_id)["status"] == "UNCHANGED"
    assert len(_rows(type="NEW_RESULTS", request_id=request_id)) == before + 2


def test_registration_capture_failure_notifies_the_registering_user(scx, monkeypatch):
    def failing(*_args, **_kwargs):
        raise dashboard_capture.DashboardCaptureError("CAPTURE_TEST_FAILURE", "synthetic")

    monkeypatch.setattr(dashboard_capture, "create_capture", failing)
    with pytest.raises(AssertionError):
        _register(scx)                                                    # its capture jobs FAILED
    me = _admin_id(scx)
    rows = _rows(me, "CAPTURE_FAILED")
    assert len(rows) == 1 and rows[0]["severity"] == "ERROR" and rows[0]["request_id"]
    assert rows[0]["dedupe_key"].startswith("capture-failed:")


# --- project cleanup and static contracts -----------------------------------------------------------

def test_project_cleanup_deletes_the_projects_notifications():
    handled = project_cleanup.handled_columns()
    assert {("notifications", "project_id"), ("notifications", "request_id")} <= handled
    suffix = uuid.uuid4().hex[:6]
    with connect() as conn:
        conn.execute("INSERT INTO projects(id,name,product_name,description,created_at) VALUES(?,?,?,?,?)",
                     [f"project-nt-{suffix}", "알림 정리", "", "", _now()])
        conn.execute("INSERT INTO analysis_requests(id,project_id,title,status,owner,requested_at) VALUES(?,?,?,?,?,?)",
                     [f"request-nt-{suffix}", f"project-nt-{suffix}", "의뢰", "IN_PROGRESS", "담당", _now()])
        user = _user(conn, "cleanup")
        notifications.emit(conn, [user], type="FINAL", title="gone", project_id=f"project-nt-{suffix}",
                           request_id=f"request-nt-{suffix}")
        notifications.emit(conn, [user], type="FINAL", title="request only", request_id=f"request-nt-{suffix}")
        notifications.emit(conn, [user], type="FINAL", title="kept", project_id="project-other")
        plan = project_cleanup.preview(conn, [f"project-nt-{suffix}"])
        assert plan["totals"].get("notifications") == 2
        project_cleanup.delete(conn, [f"project-nt-{suffix}"], plan["confirm_token"])
    assert [row["title"] for row in _rows(user)] == ["kept"]


def test_emitters_never_call_the_drive():
    source = (BACKEND / "app" / "services" / "notifications.py").read_text(encoding="utf-8")
    imported = {node.module for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom) and node.module}
    assert not any("drive" in module or "storage" in module for module in imported), imported


def test_migration_0040_is_additive_and_grants_the_app_role():
    text = (BACKEND / "migrations" / "versions" / "0040_notifications.py").read_text(encoding="utf-8")
    upgrade = text.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
    assert "CREATE TABLE IF NOT EXISTS notifications" in upgrade
    assert not re.search(r"DROP\s|ALTER\s+TABLE|DELETE\s+FROM|UPDATE\s+\w+\s+SET|TRUNCATE", upgrade, re.IGNORECASE)
    assert 'GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO "{role}"' in upgrade


def test_migration_0041_is_additive_with_grants_and_a_guarded_unique_unread_index():
    text = (BACKEND / "migrations" / "versions" / "0041_folder_link_reservations.py").read_text(encoding="utf-8")
    upgrade = text.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
    assert "CREATE TABLE IF NOT EXISTS folder_link_reservations" in upgrade
    assert "PRIMARY KEY (root_key, path_key)" in upgrade
    assert not re.search(r"DROP\s|ALTER\s+TABLE|DELETE\s+FROM|UPDATE\s+\w+\s+SET|TRUNCATE", upgrade, re.IGNORECASE)
    assert 'GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO "{role}"' in upgrade
    # The unique unread index is created only when no unread duplicates exist (no row is changed).
    assert "HAVING count(*) > 1" in upgrade and "WHERE read_at IS NULL AND dedupe_key IS NOT NULL" in upgrade


def test_helper_query_failures_stay_inside_the_notification_savepoint(monkeypatch):
    statements: list[str] = []

    class FakeConn:
        backend = "postgresql"

        def execute(self, sql, params=None):
            statements.append(sql.split()[0] + (" " + sql.split()[1] if sql.startswith(("ROLLBACK", "RELEASE")) else ""))
            if sql.startswith("SELECT title FROM analysis_requests"):
                raise RuntimeError("helper query failed")
            return self

        def fetchall(self):
            return []

        def fetchone(self):
            return None

    monkeypatch.setattr(notifications, "recipients", lambda *args, **kwargs: (statements.append("RECIPIENTS"), ["u1"])[1])
    summary = {"state": "FAILED", "files_total": 2, "files_done": 1, "project_id": "p", "request_id": "r", "batch_id": "b"}
    assert notifications.drive_upload_finished(FakeConn(), summary) == 0
    assert statements[0] == "SAVEPOINT" and statements[-2:] == ["ROLLBACK TO", "RELEASE SAVEPOINT"]
    statements.clear()
    monkeypatch.setattr(notifications, "recipients", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("x")))
    assert notifications.new_results(FakeConn(), "p", "r", "USAGE", cases=1, scenes=0, snapshot_id="s") == 0
    assert notifications.capture_failed(FakeConn(), "reg") == 0
    assert statements.count("SAVEPOINT") == 2 and statements.count("ROLLBACK TO") == 1  # capture_failed: no row, no error


def test_helper_failure_on_duckdb_returns_zero_and_keeps_the_connection_usable(monkeypatch):
    def broken(conn, request_id):
        conn.execute("SELECT title FROM no_such_table")
    monkeypatch.setattr(notifications, "_request_title", broken)
    with connect() as conn:
        user = _user(conn, "helper")
        assert notifications.final_finished(conn, {"project_id": None, "request_id": "r", "created_by": user,
                                                   "operation_id": "op"}, completed=True) == 0
        assert conn.execute("SELECT count(*) FROM notifications WHERE user_id=?", [user]).fetchone()[0] == 0


def test_retention_runs_once_per_emit_and_inserts_are_batched(monkeypatch):
    calls: list[list[str]] = []
    original = notifications._prune
    monkeypatch.setattr(notifications, "_prune", lambda conn, users, now: (calls.append(list(users)), original(conn, users, now)))
    with connect() as conn:
        users = [_user(conn, f"batch{index}") for index in range(3)]
        assert notifications.emit(conn, users, type="NEW_RESULTS", title="x", dedupe_key="batch") == 3
        assert notifications.emit(conn, users, type="NEW_RESULTS", title="x", dedupe_key="batch") == 0
    assert calls == [sorted(users)]
    assert all(len(_rows(user)) == 1 for user in users)


def test_long_dedupe_keys_are_looked_up_truncated_and_cut_recipients_are_logged(monkeypatch, caplog):
    key = "k" * 400
    with connect() as conn:
        user, second = _user(conn, "long"), _user(conn, "long2")
        assert notifications.emit(conn, [user], type="DRIVE_UPLOAD", title="A", dedupe_key=key) == 1
        assert notifications.emit(conn, [user], type="DRIVE_UPLOAD", title="B", dedupe_key=key) == 0
        assert notifications.emit(conn, [user], type="DRIVE_UPLOAD", title="C", dedupe_key=key + "x", coalesce=True) == 1
        monkeypatch.setattr(notifications, "MAX_RECIPIENTS", 1)
        with caplog.at_level("WARNING", logger="app.services.notifications"):
            cut = notifications.recipients(conn, permission=None, members=False, include=[user, second])
    assert [(row["title"], len(row["dedupe_key"])) for row in _rows(user)] == [("C", 300)]
    assert cut == sorted([user, second])[:1] and "recipients cut from 2 to 1" in caplog.text


def test_duckdb_bootstrap_creates_the_notification_indexes_and_reservation_table():
    from app import database
    with connect() as conn:
        database.ensure_notifications_schema(conn)
        database.ensure_folder_link_reservations_schema(conn)
        indexes = {row[0] for row in conn.execute(
            "SELECT index_name FROM duckdb_indexes() WHERE table_name='notifications'").fetchall()}
        columns = {row[0] for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='folder_link_reservations'").fetchall()}
    assert {"notifications_user_created", "notifications_user_dedupe", "notifications_scope"} <= indexes
    assert columns == {"root_key", "path_key", "project_id", "request_id", "environment", "created_by", "created_at",
                       "expires_at"}


def test_duckdb_bootstrap_adds_the_notifications_table_to_an_existing_database():
    from app import database
    with connect() as conn:
        conn.execute("DROP TABLE notifications")
        database.ensure_notifications_schema(conn)
        columns = {row[0] for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='notifications'").fetchall()}
    assert columns == {"id", "user_id", "type", "severity", "title", "body", "link", "project_id", "request_id",
                       "dedupe_key", "created_at", "read_at"}
    assert upload_queue is not None
