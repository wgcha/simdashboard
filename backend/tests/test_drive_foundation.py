"""Stage D0 SCX drive foundation (docs/features/scx-drive.md).

All drive behaviour runs against the in-memory fake adapter in ``drive_fakes``;
no real SDK, drive, user database or running service is touched.
"""
from __future__ import annotations

import ast
import json
import logging
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.database import initialize_database
from app.database_connection import connect
from app.main import app
from app.security import hash_password
from app.services.drive import check as drive_check
from app.services.drive import gateway as drive_gateway
from app.services.drive.config import DriveConfigError, REQUIRED_SCX, drive_settings
from app.services.drive.token_store import DbTokenStore, LocalTokenBundle, key_id

from drive_fakes import MemoryDrive, make_fake_adapter_module

pytestmark = pytest.mark.duckdb_integration

BACKEND = Path(__file__).resolve().parents[1]
PASSWORD = "correct-horse-battery-staple"
ACCESS = "eyJhbGciOiJSUzI1NiJ9.access-SECRET-aaaa"
REFRESH = "refresh-SECRET-bbbb-cccc"
SERVER = "https://scx.example.test/"


@pytest.fixture(autouse=True)
def _reset_drive_state():
    drive_gateway.shutdown()
    with connect() as conn:   # an explicitly enabled PostgreSQL test DB persists between tests
        conn.execute("DELETE FROM drive_credentials")
    yield
    drive_gateway.shutdown()


def _users() -> None:
    stamp = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        for user_id, admin in (("drive-admin", True), ("drive-viewer", False)):
            conn.execute(
                "INSERT OR IGNORE INTO users(id,username,password_hash,display_name,legacy_role,account_status,is_global_admin,"
                "is_active,created_at,updated_at) VALUES(?,?,?,?,?,'ACTIVE',?,TRUE,?,?)",
                [user_id, user_id, hash_password(PASSWORD), user_id, "admin" if admin else "viewer", admin, stamp, stamp])


def _login(client: TestClient, username: str) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def password_auth(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "drive-foundation-test-secret-key-at-least-32")
    initialize_database()
    _users()


@pytest.fixture
def scx_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SIMDASH_DRIVE_GATEWAY", "scx")
    monkeypatch.setenv("SIMDASH_SCX_WORKER_PYTHON", sys.executable)
    monkeypatch.setenv("SIMDASH_SCX_SERVER_URL", SERVER)
    monkeypatch.setenv("SIMDASH_SCX_CLIENT_NAME", "test-client")
    monkeypatch.setenv("SIMDASH_SECRET_ENC_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("SIMDASH_SCX_WORK_DIR", str(tmp_path / "scx-work"))
    monkeypatch.setenv("SIMDASH_SCX_DRIVE_ROOT", "SPDM/Projects")
    return tmp_path / "scx-work"


@pytest.fixture
def fake_drive(monkeypatch, scx_env):
    drive = MemoryDrive()
    drive.add_dir("SPDM/Projects/check")
    drive.add_file("SPDM/Projects/check/a-small.txt", b"hello drive")
    drive.add_file("SPDM/Projects/check/b-other.csv", b"x,y\n1,2\n")
    drive.add_file("SPDM/Projects/check/c-big.bin", b"0" * (drive_check.DOWNLOAD_MAX_BYTES + 1))
    drive.add_file("SPDM/Projects/check/d.txt", b"d")
    module = make_fake_adapter_module(drive)
    monkeypatch.setitem(sys.modules, "scx_drive_adapter", module)
    return drive


def _bundle_json(**overrides) -> str:
    body = {"server_url": SERVER, "access_token": ACCESS, "refresh_token": REFRESH,
            "obtained_at": "2026-10-07T01:02:03.123456Z", "account_hint": "spdm-shared"}
    body.update(overrides)
    return json.dumps(body)


def _no_secret(text: str) -> None:
    for secret in ("SECRET", ACCESS, REFRESH):
        assert secret not in text


# --- none mode -------------------------------------------------------------------------------

def test_none_mode_is_unchanged_and_never_imports_the_adapter(password_auth, monkeypatch):
    monkeypatch.delenv("SIMDASH_DRIVE_GATEWAY", raising=False)
    monkeypatch.delitem(sys.modules, "scx_drive_adapter", raising=False)
    assert drive_gateway.startup().mode == "none"
    assert drive_gateway.get_drive_gateway() is None
    with TestClient(app) as client:
        admin, viewer = _login(client, "drive-admin"), _login(client, "drive-viewer")
        assert client.get("/api/admin/drive/status", headers=admin).json()["mode"] == "none"
        assert client.get("/api/drive/status", headers=viewer).json() == {"mode": "none", "state": None,
                                                                          "writes_enabled": False, "writes_available": True}
        for method, path, kwargs in (("PUT", "/api/admin/drive/credentials", {"content": _bundle_json()}),
                                     ("DELETE", "/api/admin/drive/credentials", {}),
                                     ("POST", "/api/admin/drive/test", {}),
                                     ("POST", "/api/admin/drive/check", {"json": {"test_folder": "check"}})):
            response = client.request(method, path, headers=admin, **kwargs)
            assert response.status_code == 409, (path, response.text)
            assert response.json()["detail"]["code"] == "DRIVE_MODE_DISABLED"
    assert "scx_drive_adapter" not in sys.modules


def test_invalid_mode_value_is_refused(monkeypatch):
    monkeypatch.setenv("SIMDASH_DRIVE_GATEWAY", "sharepoint")
    with pytest.raises(drive_gateway.DriveStartupError, match="SIMDASH_DRIVE_GATEWAY"):
        drive_gateway.startup()


# --- scx startup refusal ------------------------------------------------------------------

@pytest.mark.parametrize("variable", REQUIRED_SCX)
def test_scx_mode_missing_required_value_names_the_variable(scx_env, monkeypatch, variable):
    monkeypatch.delenv(variable)
    with pytest.raises(DriveConfigError, match=variable):
        drive_settings()
    with pytest.raises(drive_gateway.DriveStartupError, match=variable):
        drive_gateway.startup()


def test_scx_mode_rejects_bad_values(scx_env, monkeypatch, tmp_path):
    monkeypatch.setenv("SIMDASH_SECRET_ENC_KEY", "not-a-fernet-key")
    with pytest.raises(DriveConfigError, match="SIMDASH_SECRET_ENC_KEY") as caught:
        drive_settings()
    assert "not-a-fernet-key" not in str(caught.value)
    monkeypatch.setenv("SIMDASH_SECRET_ENC_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("SIMDASH_SCX_WORKER_PYTHON", str(tmp_path / "missing" / "python.exe"))
    with pytest.raises(DriveConfigError, match="SIMDASH_SCX_WORKER_PYTHON"):
        drive_settings()
    monkeypatch.setenv("SIMDASH_SCX_WORKER_PYTHON", sys.executable)
    monkeypatch.setenv("SIMDASH_SCX_SERVER_URL", "http://scx.example.test/")
    with pytest.raises(DriveConfigError, match="SIMDASH_SCX_SERVER_URL"):
        drive_settings()
    monkeypatch.setenv("SIMDASH_SCX_SERVER_URL", SERVER)
    monkeypatch.setenv("SIMDASH_SCX_DRIVE_ROOT", "SPDM/../etc")
    with pytest.raises(DriveConfigError, match="SIMDASH_SCX_DRIVE_ROOT"):
        drive_settings()


def test_scx_mode_refuses_startup_without_the_adapter_package(scx_env, monkeypatch, password_auth):
    monkeypatch.setitem(sys.modules, "scx_drive_adapter", None)   # import raises ImportError
    with pytest.raises(drive_gateway.DriveStartupError) as caught:
        drive_gateway.startup()
    message = str(caught.value)
    assert "어댑터 패키지(scx_drive_adapter)가 설치되지 않았습니다" in message
    assert "external-wheels" in message and "update.bat" in message and "pip install" not in message
    assert "not installed" in message
    with pytest.raises(drive_gateway.DriveStartupError):
        with TestClient(app):
            pass


def test_single_process_lock_refuses_a_second_process(fake_drive, scx_env):
    scx_env.mkdir(parents=True)
    holder = subprocess.Popen(
        [sys.executable, "-c", (
            "import sys,time; sys.path.insert(0, sys.argv[1]);"
            "from pathlib import Path; from app.services.drive.gateway import SingleProcessLock;"
            "lock = SingleProcessLock(Path(sys.argv[2])); assert lock.acquire(); print('held', flush=True); time.sleep(30)"),
         str(BACKEND), str(scx_env / "dashboard.lock")],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(drive_gateway.DriveStartupError, match="단일 프로세스"):
            drive_gateway.startup()
    finally:
        holder.kill()
        holder.wait()
    assert drive_gateway.startup().enabled            # released by the dead process
    second = drive_gateway.SingleProcessLock(scx_env / "dashboard.lock")
    assert second.acquire() is False                  # held by this process now
    drive_gateway.shutdown()
    assert second.acquire() is True
    second.release()


def test_gateway_is_one_lazy_worker_gateway_with_home_drive_root(fake_drive, scx_env):
    drive_gateway.startup()
    assert fake_drive.calls == []
    first = drive_gateway.get_drive_gateway()
    assert first is drive_gateway.get_drive_gateway()
    assert first.config.adapter.drive_root == "~"
    assert first.config.adapter.server_url == SERVER
    assert first.config.python_exe == Path(sys.executable)
    assert first.config.work_dir == scx_env
    drive_gateway.shutdown()
    assert first.closed


# --- token store ------------------------------------------------------------------------------

def _bundle(**overrides) -> LocalTokenBundle:
    values = dict(server_url=SERVER, access_token=ACCESS, refresh_token=REFRESH,
                  obtained_at=datetime(2026, 10, 7, 1, 2, 3, 123456, tzinfo=timezone.utc), account_hint="spdm-shared")
    values.update(overrides)
    return LocalTokenBundle(**values)


def test_db_token_store_encrypts_and_round_trips_with_microseconds():
    key = Fernet.generate_key().decode()
    store = DbTokenStore(key)
    assert store.load() is None and store.last_load_problem == "MISSING"
    store.write(_bundle(), updated_by="drive-admin")
    with connect() as conn:
        ciphertext, stored_key = conn.execute("SELECT ciphertext, key_id FROM drive_credentials WHERE id='scx'").fetchone()
    assert stored_key == key_id(key) and len(stored_key) == 8
    _no_secret(bytes(ciphertext).decode("latin-1"))
    loaded = store.load()
    assert loaded == _bundle()
    assert loaded.obtained_at.microsecond == 123456
    _no_secret(repr(loaded))
    meta = store.metadata()
    assert meta["present"] and meta["key_matches"] and meta["account_hint"] == "spdm-shared"
    assert meta["obtained_at"].startswith("2026-10-07T01:02:03.123456")
    _no_secret(json.dumps(meta))


def test_db_token_store_key_change_or_corruption_loads_none():
    store = DbTokenStore(Fernet.generate_key().decode())
    store.write(_bundle(), updated_by="drive-admin")
    other = DbTokenStore(Fernet.generate_key().decode())
    assert other.load() is None and other.last_load_problem == "KEY_CHANGED"
    assert other.metadata()["key_matches"] is False
    with connect() as conn:
        conn.execute("UPDATE drive_credentials SET ciphertext=? WHERE id='scx'", [b"garbage"])
    assert store.load() is None and store.last_load_problem == "DECRYPT_FAILED"
    assert store.clear() is True and store.load() is None


def test_db_token_store_save_from_worker_thread_commits_before_returning():
    key = Fernet.generate_key().decode()
    DbTokenStore(key).write(_bundle(), updated_by="drive-admin")
    store = DbTokenStore(key).for_gateway()
    assert store.load() is not None
    rotated = _bundle(refresh_token="rotated-SECRET", obtained_at=datetime(2026, 10, 7, 2, 0, 0, 1, tzinfo=timezone.utc))
    thread = threading.Thread(target=store.save, args=(rotated,))
    thread.start()
    thread.join(10)
    fresh = DbTokenStore(key)                          # a separate reader sees the committed row
    assert fresh.load().refresh_token == "rotated-SECRET"
    assert fresh.metadata()["updated_by"] == "worker"


def test_db_token_store_save_failure_is_logged_without_tokens(caplog):
    def broken():
        raise RuntimeError("db down")
    store = DbTokenStore(Fernet.generate_key().decode(), connect_fn=broken)
    store._loaded_generation = 1          # as if loaded before the database went away
    with caplog.at_level(logging.DEBUG):
        store.save(_bundle())
    assert store.last_save_error_at is not None
    assert "SCX token save failed" in caplog.text
    _no_secret(caplog.text)


# --- admin API ------------------------------------------------------------------------------

def test_admin_drive_api_requires_global_admin(fake_drive, password_auth):
    with TestClient(app) as client:
        viewer = _login(client, "drive-viewer")
        for method, path, kwargs in (("GET", "/api/admin/drive/status", {}),
                                     ("PUT", "/api/admin/drive/credentials", {"content": _bundle_json()}),
                                     ("DELETE", "/api/admin/drive/credentials", {}),
                                     ("POST", "/api/admin/drive/test", {}),
                                     ("POST", "/api/admin/drive/check", {"json": {"test_folder": "check"}})):
            response = client.request(method, path, headers=viewer, **kwargs)
            assert response.status_code == 403, (path, response.text)
            assert response.json()["detail"]["code"] == "GLOBAL_ADMIN_REQUIRED"
        client.cookies.clear()
        assert client.get("/api/admin/drive/status").status_code == 401
    assert drive_gateway.token_store().load() is None


def test_credentials_register_validate_test_delete_without_echoing_tokens(fake_drive, password_auth, caplog):
    caplog.set_level(logging.DEBUG)
    with TestClient(app) as client:
        admin, viewer = _login(client, "drive-admin"), _login(client, "drive-viewer")
        status = client.get("/api/admin/drive/status", headers=admin).json()
        assert status["mode"] == "scx" and status["state"] == "AUTH_REQUIRED"
        assert status["credentials"]["present"] is False and status["drive_root_locked"] is True
        assert client.get("/api/drive/status", headers=viewer).json() == {"mode": "scx", "state": "AUTH_REQUIRED",
                                                                          "writes_enabled": False, "writes_available": False}
        bodies = []
        for body, fragment in ((_bundle_json(server_url="https://evil.example.test/"), "호스트"),
                               (_bundle_json(access_token=""), "access_token"),
                               (_bundle_json(refresh_token="  "), "refresh_token"),
                               (_bundle_json(obtained_at="yesterday"), "obtained_at"),
                               ("{not json " + ACCESS, "JSON"),
                               (json.dumps([ACCESS]), "객체")):
            response = client.put("/api/admin/drive/credentials", headers=admin, content=body)
            assert response.status_code == 400, response.text
            assert response.json()["detail"]["code"] == "DRIVE_CREDENTIALS_INVALID"
            assert fragment in response.json()["detail"]["message"]
            bodies.append(response.text)
        saved = client.put("/api/admin/drive/credentials", headers=admin, content=_bundle_json())
        assert saved.status_code == 200, saved.text
        assert saved.json()["account_hint"] == "spdm-shared"
        assert saved.json()["test"]["ok"] is True and saved.json()["test"]["root_identity"].startswith("scx:")
        bodies.append(saved.text)
        # session creation rotated the refresh token and the store saved it synchronously
        assert fake_drive.sessions == 1
        assert drive_gateway.token_store().load().refresh_token == f"{REFRESH}-r1"
        tested = client.post("/api/admin/drive/test", headers=admin)
        assert tested.json()["ok"] is True and fake_drive.sessions == 1     # microsecond obtained_at round-trips
        status = client.get("/api/admin/drive/status", headers=admin)
        assert status.json()["state"] == "OK" and status.json()["credentials"]["updated_by"] == "worker"
        assert status.json()["worker_version"] == "0.2.0-fake"
        bodies.append(status.text)
        user = client.get("/api/drive/status", headers=viewer)
        assert user.json() == {"mode": "scx", "state": "OK", "writes_enabled": False, "writes_available": False}
        deleted = client.delete("/api/admin/drive/credentials", headers=admin)
        assert deleted.status_code == 204
        failed = client.post("/api/admin/drive/test", headers=admin).json()
        assert failed["ok"] is False and failed["error"]["code"] == "DRIVE_AUTH_REQUIRED"
        assert client.get("/api/drive/status", headers=viewer).json()["state"] == "AUTH_REQUIRED"
    for text in bodies:
        _no_secret(text)
    _no_secret(caplog.text)
    with connect() as conn:
        audit = conn.execute("SELECT action, detail_json FROM audit_events WHERE action LIKE 'DRIVE_%' ORDER BY occurred_at").fetchall()
    actions = [row[0] for row in audit]
    assert "DRIVE_CREDENTIALS_REGISTERED" in actions and "DRIVE_CREDENTIALS_DELETED" in actions
    for _action, detail in audit:
        _no_secret(str(detail))


def test_user_status_hides_banner_before_first_contact(fake_drive, password_auth):
    drive_gateway.startup()
    drive_gateway.token_store().write(_bundle(), updated_by="drive-admin")
    with TestClient(app) as client:
        viewer = _login(client, "drive-viewer")
        assert client.get("/api/drive/status", headers=viewer).json()["state"] == "UNKNOWN"


# --- drive check ----------------------------------------------------------------------------

def _registered(client: TestClient, admin: dict[str, str]) -> None:
    assert client.put("/api/admin/drive/credentials", headers=admin, content=_bundle_json()).status_code == 200


def test_drive_check_is_read_only_and_reports_each_step(fake_drive, password_auth, scx_env):
    with TestClient(app) as client:
        admin = _login(client, "drive-admin")
        _registered(client, admin)
        before = {path: (node.content, getattr(node, "modified_at", None)) for path, node in fake_drive.nodes.items()}
        fake_drive.calls.clear()
        response = client.post("/api/admin/drive/check", headers=admin, json={"test_folder": "check"})
        assert response.status_code == 200, response.text
        report = response.json()
    steps = {step["step"]: step for step in report["steps"]}
    assert report["ok"] is True, report
    assert list(steps) == ["stat_folder", "list_dir", "stat_file", "download_to", "health"]
    assert "leftovers" not in report
    assert "항목 4개(파일 4, 폴더 0)" in steps["list_dir"]["observations"]
    assert any("sha1: 파일 4개 중 4개" in item for item in steps["list_dir"]["observations"])
    assert any("modified_at" in item for item in steps["list_dir"]["observations"])
    assert all(step["latency_ms"] is not None for step in report["steps"])
    assert fake_drive.page_calls >= 2                            # 4 entries over page_size 3
    # read-only: the drive tree is exactly what it was (no new, changed or removed node)
    after = {path: (node.content, getattr(node, "modified_at", None)) for path, node in fake_drive.nodes.items()}
    assert after == before
    assert {op for op, _rel in fake_drive.calls} <= {"stat", "list_dir", "download_to"}
    assert all(rel == "" or rel.startswith("SPDM/Projects/check") for _op, rel in fake_drive.calls)
    assert not any((scx_env / "staging").iterdir())               # local staging copy removed


def test_drive_check_reports_missing_folder_busy_file_and_bad_paths(fake_drive, password_auth):
    with TestClient(app) as client:
        admin = _login(client, "drive-admin")
        _registered(client, admin)
        missing = client.post("/api/admin/drive/check", headers=admin, json={"test_folder": "nope"}).json()
        assert missing["ok"] is False and missing["steps"][0]["code"] == "NOT_FOUND" and len(missing["steps"]) == 1
        fake_drive.busy_paths.add("SPDM/Projects/check/a-small.txt")
        busy = client.post("/api/admin/drive/check", headers=admin, json={"test_folder": "check"}).json()
        download = next(step for step in busy["steps"] if step["step"] == "download_to")
        assert download["ok"] is False and download["code"] == "BUSY" and busy["ok"] is False
        for bad in ("../outside", "a//b", "~/x", "a\\b"):
            response = client.post("/api/admin/drive/check", headers=admin, json={"test_folder": bad})
            assert response.status_code == 400 and response.json()["detail"]["code"] == "DRIVE_CHECK_FOLDER_INVALID"


def test_drive_check_runs_one_at_a_time(fake_drive, password_auth):
    with TestClient(app) as client:
        admin = _login(client, "drive-admin")
        _registered(client, admin)
        assert drive_check._run_lock.acquire(blocking=False)
        try:
            response = client.post("/api/admin/drive/check", headers=admin, json={"test_folder": "check"})
        finally:
            drive_check._run_lock.release()
        assert response.status_code == 409 and response.json()["detail"]["code"] == "DRIVE_CHECK_RUNNING"


def test_drive_check_requires_spdm_root(fake_drive, password_auth, monkeypatch):
    monkeypatch.delenv("SIMDASH_SCX_DRIVE_ROOT")
    with TestClient(app) as client:
        admin = _login(client, "drive-admin")
        response = client.post("/api/admin/drive/check", headers=admin, json={"test_folder": "check"})
        assert response.status_code == 409 and response.json()["detail"]["code"] == "DRIVE_ROOT_NOT_SET"


# --- error mapping and static guards -----------------------------------------------------------

def test_drive_error_mapping_matches_integration_06(fake_drive):
    module = sys.modules["scx_drive_adapter"]
    expected = {"NOT_FOUND": ("SPDM_NOT_FOUND", 404), "BUSY": ("SPDM_FILE_BUSY", 409),
                "AUTH_REQUIRED": ("DRIVE_AUTH_REQUIRED", 503), "OVERLOADED": ("DRIVE_BUSY", 503),
                "TIMEOUT": ("DRIVE_TIMEOUT", 504), "UNAVAILABLE": ("DRIVE_UNAVAILABLE", 503),
                "CONFLICT": ("SPDM_CONFLICT", 409), "INTERNAL": ("DRIVE_INTERNAL", 500)}
    for code, (storage_code, status) in expected.items():
        error = module.DriveError(module.ErrorCode[code], "msg")
        mapping = drive_gateway.map_drive_error(error)
        assert (mapping.storage_code, mapping.http_status) == (storage_code, status)
        assert drive_gateway.to_storage_error(error).code == storage_code
    assert drive_gateway.map_drive_error(module.DriveError(module.ErrorCode.OVERLOADED, "x")).retry_after == 5
    assert set(drive_gateway.DRIVE_ERROR_MAP) == {member.value for member in module.ErrorCode}


FORBIDDEN_CALLS = {"rm", "mv", "mv_to_trash", "move", "rename", "replace", "remove", "delete", "touch", "update_type",
                   "unlink", "rmdir", "set_metadata", "unset_metadata", "mount", "unmount", "create_account_dir"}


def test_drive_code_never_calls_delete_move_or_overwrite_operations():
    sources = sorted((BACKEND / "app" / "services" / "drive").glob("*.py")) + [BACKEND / "app" / "routers" / "drive.py"]
    violations = []
    for source in sources:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
                harmless = (
                    # datetime.replace(tzinfo=...) and the HTTP route decorator are not drive operations
                    (name == "replace" and [keyword.arg for keyword in node.keywords] == ["tzinfo"])
                    or (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                        and func.value.id in {"admin_router", "status_router"})
                )
                if name in FORBIDDEN_CALLS and not harmless:
                    violations.append(f"{source.name}:{node.lineno} {name}()")
                for keyword in node.keywords:
                    if keyword.arg == "force" and not (isinstance(keyword.value, ast.Constant) and keyword.value.value is False):
                        violations.append(f"{source.name}:{node.lineno} force=")
            if isinstance(node, ast.Attribute) and node.attr in {"REPLACE", "KEEP_BOTH"}:
                violations.append(f"{source.name}:{node.lineno} {node.attr}")
    assert violations == []


DRIVE_WRITE_METHODS = {"upload_new", "mkdirs", "copy_within"}
CHECK_READ_METHODS = {"stat", "list_dir", "download_to", "health"}


def test_drive_check_calls_no_drive_write_methods():
    """User decision 2026-10-07: the admin drive check only reads (no upload/mkdirs/copy, no option)."""
    tree = ast.parse((BACKEND / "app" / "services" / "drive" / "check.py").read_text(encoding="utf-8"))
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr not in DRIVE_WRITE_METHODS | FORBIDDEN_CALLS, f"check.py:{node.lineno} {node.attr}"
            if isinstance(node.value, ast.Name) and node.value.id == "gateway":
                used.add(node.attr)
        if isinstance(node, ast.Name):
            assert node.id not in DRIVE_WRITE_METHODS, f"check.py:{node.lineno} {node.id}"
    assert used and used <= CHECK_READ_METHODS, used


def test_fake_gateway_and_tests_are_not_importable_from_app():
    for source in (BACKEND / "app").rglob("*.py"):
        text = source.read_text(encoding="utf-8")
        assert "drive_fakes" not in text and "MemoryDrive" not in text, source
    sources = sorted((BACKEND / "app" / "services" / "drive").glob("*.py"))
    for source in sources:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
        assert not any("scx_drive_adapter" in ast.unparse(node) or "siemens" in ast.unparse(node) for node in imports), source


# --- schema --------------------------------------------------------------------------------------

def test_duckdb_bootstrap_creates_drive_credentials_on_existing_database():
    with connect() as conn:
        conn.execute("DROP TABLE drive_credentials")
        conn.execute("INSERT INTO projects(id,name,product_name,description,created_at) VALUES "
                     "('drive-upgrade-project','keep','p','d',CURRENT_TIMESTAMP)")
    initialize_database()
    with connect() as conn:
        columns = {row[0] for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='drive_credentials'").fetchall()}
        kept = conn.execute("SELECT name FROM projects WHERE id='drive-upgrade-project'").fetchone()
    assert columns == {"id", "ciphertext", "key_id", "account_hint", "obtained_at", "updated_by", "updated_at", "generation"}
    assert kept == ("keep",)


def test_migration_0036_is_additive_and_grants_the_app_role():
    text = (BACKEND / "migrations" / "versions" / "0036_drive_credentials.py").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS drive_credentials" in text
    assert "generation BIGINT NOT NULL" in text
    for forbidden in ("DROP TABLE", "DELETE FROM", "TRUNCATE", "ALTER TABLE"):
        assert forbidden not in text
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON drive_credentials" in text


def test_postgres_startup_requires_drive_credentials_table():
    source = (BACKEND / "app" / "database.py").read_text(encoding="utf-8")
    assert '"drive_credentials",' in source
    assert '"drive_credentials": {"id", "ciphertext", "key_id"' in source


def test_drive_status_poll_is_cheap(fake_drive, password_auth):
    with TestClient(app) as client:
        viewer = _login(client, "drive-viewer")
        started = time.perf_counter()
        for _ in range(5):
            assert client.get("/api/drive/status", headers=viewer).status_code == 200
        assert time.perf_counter() - started < 5
        assert fake_drive.calls == []                 # banner polling never calls the drive


# --- review fixes: generation fencing (M1) ----------------------------------------------------

def test_worker_save_is_fenced_by_generation_and_never_recreates_a_deleted_row(caplog):
    caplog.set_level(logging.DEBUG)
    key = Fernet.generate_key().decode()
    admin = DbTokenStore(key)
    first_generation = admin.write(_bundle(), updated_by="drive-admin")
    worker = admin.for_gateway()
    assert worker.load() == _bundle() and worker.loaded_generation == first_generation
    worker.save(_bundle(refresh_token="rot1-SECRET"))
    assert admin.load_local().refresh_token == "rot1-SECRET" and admin.metadata()["updated_by"] == "worker"

    assert admin.clear() is True
    worker.save(_bundle(refresh_token="stale-after-delete-SECRET"))      # late rotation from the old chain
    assert admin.metadata()["present"] is False                         # not resurrected
    assert worker.last_stale_save_at is not None and admin.last_stale_save_at is not None

    second_generation = admin.write(_bundle(access_token="new-acc-SECRET", refresh_token="new-ref-SECRET"),
                                    updated_by="drive-admin")
    assert second_generation > first_generation                         # monotonic across DELETE
    worker.save(_bundle(refresh_token="stale-after-reregister-SECRET"))
    assert admin.load_local().refresh_token == "new-ref-SECRET"         # new account not overwritten
    assert "SCX rotated token not saved" in caplog.text
    _no_secret(caplog.text)

    fresh = admin.for_gateway()
    assert fresh.load().refresh_token == "new-ref-SECRET"
    fresh.save(_bundle(refresh_token="new-rot-SECRET"))
    assert admin.load_local().refresh_token == "new-rot-SECRET"
    assert admin.for_gateway().save(_bundle()) is None                  # never loaded → dropped, no exception
    assert admin.load_local().refresh_token == "new-rot-SECRET"


def test_delete_ends_the_in_memory_session_and_late_rotation_cannot_resurrect(fake_drive, password_auth):
    with TestClient(app) as client:
        admin = _login(client, "drive-admin")
        _registered(client, admin)
        old = drive_gateway.existing_gateway()
        assert old is not None and old._bundle is not None
        assert client.delete("/api/admin/drive/credentials", headers=admin).status_code == 204
        assert old.closed and drive_gateway.existing_gateway() is None
        failed = client.post("/api/admin/drive/test", headers=admin).json()
        assert failed["ok"] is False and failed["error"]["code"] == "DRIVE_AUTH_REQUIRED"
        old.rotate()                                                    # late notification from the old worker
        assert drive_gateway.token_store().metadata()["present"] is False
        assert client.get("/api/admin/drive/status", headers=admin).json()["state"] == "AUTH_REQUIRED"


def test_fake_worker_keeps_cached_tokens_without_a_reset(fake_drive, password_auth):
    """Documents why DELETE must reset the gateway: the worker caches tokens in memory."""
    with TestClient(app) as client:
        admin = _login(client, "drive-admin")
        _registered(client, admin)
        gateway = drive_gateway.existing_gateway()
        drive_gateway.token_store().clear()                            # row gone, no reset
        assert gateway.stat("") is not None


def test_reregister_is_not_overwritten_by_the_old_chain(fake_drive, password_auth):
    with TestClient(app) as client:
        admin = _login(client, "drive-admin")
        _registered(client, admin)
        old = drive_gateway.existing_gateway()
        renewed = client.put("/api/admin/drive/credentials", headers=admin,
                             content=_bundle_json(access_token="eyJnew.access-SECRET", refresh_token="new-refresh-SECRET",
                                                  obtained_at="2026-10-07T05:00:00Z", account_hint="other"))
        assert renewed.status_code == 200 and renewed.json()["test"]["ok"] is True
        assert old.closed and drive_gateway.existing_gateway() is not old
        current = drive_gateway.token_store().load_local().refresh_token
        assert current.startswith("new-refresh-SECRET-r")
        old.rotate()                                                    # stale rotation from the first account
        assert drive_gateway.token_store().load_local().refresh_token == current
        assert drive_gateway.token_store().metadata()["account_hint"] == "other"


# --- review fixes: status never 500 / never builds the gateway (L1) -----------------------------

def test_status_endpoints_do_not_build_the_gateway(fake_drive, password_auth):
    drive_gateway.startup()
    drive_gateway.token_store().write(_bundle(), updated_by="drive-admin")
    with TestClient(app) as client:
        admin, viewer = _login(client, "drive-admin"), _login(client, "drive-viewer")
        for _ in range(3):
            assert client.get("/api/drive/status", headers=viewer).json()["state"] == "UNKNOWN"
            assert client.get("/api/admin/drive/status", headers=admin).json()["state"] == "UNKNOWN"
        assert drive_gateway.existing_gateway() is None and fake_drive.calls == []


def test_status_endpoints_report_unavailable_instead_of_500(fake_drive, password_auth, monkeypatch):
    with TestClient(app) as client:
        admin, viewer = _login(client, "drive-admin"), _login(client, "drive-viewer")

        def broken_store():
            raise drive_gateway.DriveStartupError("adapter missing")
        monkeypatch.setattr(drive_gateway, "token_store", broken_store)
        monkeypatch.setattr(drive_gateway, "get_drive_gateway", broken_store)
        user = client.get("/api/drive/status", headers=viewer)
        assert user.status_code == 200 and user.json() == {"mode": "scx", "state": "UNAVAILABLE",
                                                                "writes_enabled": False, "writes_available": False}
        status = client.get("/api/admin/drive/status", headers=admin)
        assert status.status_code == 200 and status.json()["state"] == "UNAVAILABLE"
        tested = client.post("/api/admin/drive/test", headers=admin)
        assert tested.status_code == 200 and tested.json()["ok"] is False


# --- review fixes: request body hardening (L2, L3) ----------------------------------------------

def test_credentials_body_rejects_deep_nesting_and_oversize(fake_drive, password_auth):
    with TestClient(app) as client:
        admin = _login(client, "drive-admin")
        deep = client.put("/api/admin/drive/credentials", headers=admin, content="[" * 60_000)
        assert deep.status_code == 400 and deep.json()["detail"]["code"] == "DRIVE_CREDENTIALS_INVALID"
        assert "JSON을 해석할 수 없습니다" in deep.json()["detail"]["message"]
        big = _bundle_json(account_hint="x" * (70 * 1024))
        declared = client.put("/api/admin/drive/credentials", headers=admin, content=big)
        assert declared.status_code == 400 and "너무 큽니다" in declared.json()["detail"]["message"]

        def chunked():
            for _ in range(80):
                yield b"x" * 1024
        streamed = client.put("/api/admin/drive/credentials", headers=admin, content=chunked())
        assert streamed.status_code == 400 and "너무 큽니다" in streamed.json()["detail"]["message"]
        assert drive_gateway.token_store().metadata()["present"] is False


# --- review fixes: adapter TokenBundle only at the boundary, message masking (L4) ---------------

def test_adapter_bundle_type_is_used_only_at_the_adapter_boundary(fake_drive):
    module = sys.modules["scx_drive_adapter"]
    admin = DbTokenStore(Fernet.generate_key().decode())
    admin.write(module.TokenBundle(**{field: getattr(_bundle(), field) for field in
                                      ("server_url", "access_token", "refresh_token", "obtained_at", "account_hint")}),
                updated_by="drive-admin")
    assert type(admin.load()) is LocalTokenBundle                       # admin side never sees the adapter type
    worker = admin.for_gateway(module.TokenBundle)
    loaded = worker.load()
    assert type(loaded) is module.TokenBundle                           # the adapter gets its own type
    worker.save(module.TokenBundle(server_url=SERVER, access_token="a2-SECRET", refresh_token="r2-SECRET",
                                   obtained_at=datetime(2026, 10, 7, 3, tzinfo=timezone.utc), account_hint=None))
    stored = worker.load_local()
    assert type(stored) is LocalTokenBundle and stored.refresh_token == "r2-SECRET"
    _no_secret(repr(stored))


def test_safe_error_message_masks_bearer_and_jwt_like_values():
    error = RuntimeError(f"401 for Authorization: Bearer {REFRESH} token {ACCESS} done")
    message = drive_gateway.safe_error_message(error)
    _no_secret(message)
    assert "Bearer ***" in message and "done" in message
    assert drive_gateway.safe_error_message(RuntimeError("x" * 500 + ACCESS)).count("SECRET") == 0
