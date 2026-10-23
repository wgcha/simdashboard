"""Administrator DB backup (app/services/db_backup.py, routers/db_backups.py).

DuckDB path for real; ``pg_dump`` / ``pg_restore`` are replaced by a recording fake of ``subprocess.run`` so the
command line, environment, failure reporting and retention are checked without a PostgreSQL server.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import test_drive_foundation as foundation
from app.database_connection import connect
from app.main import app
from app.services import db_backup
from test_drive_foundation import _login

password_auth = foundation.password_auth
pytestmark = pytest.mark.duckdb_integration

SECRET = "Pg-Secret-NEVER-LOG-4411"
URL = f"postgresql+psycopg://simdash_app:{SECRET}@127.0.0.1:5434/simulation_dashboard"
OWNER_URL = f"postgresql+psycopg://simdash_owner:{SECRET}-owner@127.0.0.1:5434/simulation_dashboard"


@pytest.fixture
def backup_dir(monkeypatch, tmp_path):
    directory = tmp_path / "db-backups"
    monkeypatch.setenv("SIMDASH_DB_BACKUP_DIR", str(directory))
    monkeypatch.delenv("SIMDASH_DB_BACKUP_KEEP", raising=False)
    monkeypatch.delenv("POSTGRES_OWNER_URL", raising=False)
    return directory


@pytest.fixture
def admin_client(password_auth, backup_dir):
    client = TestClient(app)
    client.headers.update(_login(client, "drive-admin"))
    return client


def _wait(client, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get("/api/admin/db-backups").json()["job"]
        if job and job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("backup did not finish")


def _fake_postgres(monkeypatch, *, dump_stderr=None, restore_fail=False, server=18, client=18):
    """A PostgreSQL deployment whose pg_dump/pg_restore are fakes recording (argv, env)."""
    calls = []
    monkeypatch.setattr(db_backup, "database_settings", lambda: SimpleNamespace(
        backend="postgresql", database_url=URL, duckdb_path=Path("unused")))
    monkeypatch.setattr(db_backup, "_server_major", lambda: server)
    cli = db_backup._postgres_cli()
    monkeypatch.setattr(cli, "executable", lambda name: f"/opt/pg/bin/{name}")

    def run(command, **kwargs):
        calls.append((list(command), kwargs.get("env") or {}))
        tool = Path(command[0]).name
        if "--version" in command:
            return SimpleNamespace(stdout=f"{tool} (PostgreSQL) {client}.1\n", returncode=0)
        if tool == "pg_dump":
            if dump_stderr is not None:
                raise subprocess.CalledProcessError(1, command, stderr=dump_stderr)
            Path(command[command.index("--file") + 1]).write_bytes(b"PGDMP-fake-archive")
        elif restore_fail:
            raise subprocess.CalledProcessError(1, command, stderr="pg_restore: error: not a valid archive")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(db_backup.subprocess, "run", run)
    return calls


# ---------------------------------------------------------------- permissions
def test_non_admin_and_anonymous_cannot_use_backups(password_auth, backup_dir):
    anonymous = TestClient(app)
    assert anonymous.get("/api/admin/db-backups").status_code in (401, 403)
    viewer = TestClient(app)
    viewer.headers.update(_login(viewer, "drive-viewer"))
    assert viewer.get("/api/admin/db-backups").status_code == 403
    assert viewer.post("/api/admin/db-backups", json={}).status_code == 403
    assert viewer.get("/api/admin/db-backups/20260101T000000Z_manual/download").status_code == 403
    assert viewer.delete("/api/admin/db-backups/20260101T000000Z_manual").status_code == 403
    assert not backup_dir.exists()


# ------------------------------------------------------- DuckDB copy + API
def test_duckdb_backup_job_list_download_delete_and_audit(admin_client, backup_dir, monkeypatch):
    monkeypatch.setenv("ANALYSIS_DB_BACKEND", "duckdb")
    started = admin_client.post("/api/admin/db-backups", json={"memo": "배포 전"})
    assert started.status_code == 202 and started.json()["status"] in {"running", "succeeded"}
    job = _wait(admin_client)
    assert job["status"] == "succeeded", job
    created = job["backup"]
    assert Path(created["path"]).parent == backup_dir and created["size"] > 0

    overview = admin_client.get("/api/admin/db-backups").json()
    assert overview["directory"] == str(backup_dir) and overview["keep_automatic"] == 20
    (item,) = overview["backups"]
    assert item["id"] == created["id"] and item["status"] == "ok" and item["kind"] == "manual"
    assert item["memo"] == "배포 전" and item["created_by"] == "drive-admin" and item["app_version"]
    manifest = json.loads((backup_dir / f"{item['id']}.manifest.json").read_text(encoding="utf-8"))
    assert manifest["sha256"] == item["sha256"] and manifest["size"] == created["size"] and manifest["format"] == "duckdb-file"

    download = admin_client.get(f"/api/admin/db-backups/{item['id']}/download")
    assert download.status_code == 200 and len(download.content) == created["size"]
    assert download.headers["content-type"] == "application/octet-stream"
    assert item["file"] in download.headers["content-disposition"]

    assert admin_client.delete(f"/api/admin/db-backups/{item['id']}").json() == {"id": item["id"], "deleted": True}
    assert admin_client.get("/api/admin/db-backups").json()["backups"] == []
    assert not list(backup_dir.glob("*.manifest.json"))
    assert admin_client.delete(f"/api/admin/db-backups/{item['id']}").status_code == 404

    with connect() as conn:
        actions = {row[0] for row in conn.execute(
            "SELECT action FROM audit_events WHERE action IN ('BACKUP_CREATED','BACKUP_DOWNLOADED','BACKUP_DELETED')").fetchall()}
    assert actions == {"BACKUP_CREATED", "BACKUP_DOWNLOADED", "BACKUP_DELETED"}


def test_ids_are_never_paths(admin_client, backup_dir):
    (backup_dir.parent / "secret.dump").write_bytes(b"x")
    for bad in ("..%2Fsecret", "secret", "20260101T000000Z_manual.dump", "%2e%2e"):
        assert admin_client.get(f"/api/admin/db-backups/{bad}/download").status_code in (404, 405)
        assert admin_client.delete(f"/api/admin/db-backups/{bad}").status_code in (404, 405)


def test_only_one_backup_at_a_time(admin_client, backup_dir, monkeypatch):
    monkeypatch.setenv("ANALYSIS_DB_BACKEND", "duckdb")
    gate = threading.Event()
    real = db_backup._dump_duckdb
    monkeypatch.setattr(db_backup, "_dump_duckdb", lambda partial: (gate.wait(10), real(partial))[1])
    assert admin_client.post("/api/admin/db-backups", json={}).status_code == 202
    second = admin_client.post("/api/admin/db-backups", json={})
    assert second.status_code == 409 and second.json()["detail"]["code"] == "DB_BACKUP_RUNNING"
    assert admin_client.get("/api/admin/db-backups").json()["job"]["status"] == "running"
    gate.set()
    assert _wait(admin_client)["status"] == "succeeded"


# ----------------------------------------------------- pg_dump invocation
def test_pg_dump_command_env_and_manifest(backup_dir, monkeypatch):
    calls = _fake_postgres(monkeypatch)
    monkeypatch.setenv("POSTGRES_OWNER_URL", OWNER_URL)
    result = db_backup.create_backup("pre-delete", "tester", memo="x")
    argv, env = next(c for c in calls if Path(c[0][0]).name == "pg_dump" and "--file" in c[0])
    assert argv[0] == "/opt/pg/bin/pg_dump"
    for flag in ("--format=custom", "--no-owner", "--no-privileges"):
        assert flag in argv
    assert argv[argv.index("--host") + 1] == "127.0.0.1" and argv[argv.index("--port") + 1] == "5434"
    assert argv[argv.index("--username") + 1] == "simdash_owner"      # owner account preferred
    assert argv[argv.index("--dbname") + 1] == "simulation_dashboard"
    assert SECRET not in " ".join(argv) and env["PGPASSWORD"] == f"{SECRET}-owner"
    assert env["LC_ALL"] == "C"
    restore_argv, _ = next(c for c in calls if Path(c[0][0]).name == "pg_restore")
    assert restore_argv[1] == "--list"
    assert Path(result["path"]).name.endswith("_pre-delete.dump") and Path(result["path"]).read_bytes() == b"PGDMP-fake-archive"
    assert not list(backup_dir.glob(".*.partial"))
    manifest = json.loads(next(backup_dir.glob("*.manifest.json")).read_text(encoding="utf-8"))
    assert manifest["kind"] == "automatic" and manifest["database"] == "simulation_dashboard"
    assert manifest["account"] == "owner" and manifest["created_by"] == "tester" and manifest["app_version"]
    assert len(manifest["sha256"]) == 64 and SECRET not in json.dumps(manifest)
    assert set(result) == {"id", "path", "size", "created_at"}


def test_app_account_used_without_owner_url(backup_dir, monkeypatch):
    calls = _fake_postgres(monkeypatch)
    monkeypatch.setattr(db_backup, "_owner_url", lambda: None)
    db_backup.create_backup("auto", None)
    argv, env = next(c for c in calls if "--file" in c[0])
    assert argv[argv.index("--username") + 1] == "simdash_app" and env["PGPASSWORD"] == SECRET


def test_owner_url_for_another_database_is_ignored(backup_dir, monkeypatch):
    calls = _fake_postgres(monkeypatch)
    monkeypatch.setattr(db_backup, "_owner_url", lambda: OWNER_URL.replace("simulation_dashboard", "other"))
    db_backup.create_backup("auto", None)
    argv, _ = next(c for c in calls if "--file" in c[0])
    assert argv[argv.index("--username") + 1] == "simdash_app"


# --------------------------------------------------------------- failures
def _fails(code):
    with pytest.raises(db_backup.BackupError) as caught:
        db_backup.create_backup("manual", "a")
    assert caught.value.code == code
    return caught.value


def test_missing_pg_dump_is_reported_clearly(backup_dir, monkeypatch):
    _fake_postgres(monkeypatch)
    cli = db_backup._postgres_cli()

    def missing(name):
        raise cli.PostgresToolNotFound(f"{name} 없음")
    monkeypatch.setattr(cli, "executable", missing)
    error = _fails("DB_BACKUP_TOOL_MISSING")
    assert "POSTGRES_BIN" in error.message and error.stage == "pg_dump_resolve"
    assert not [p for p in backup_dir.iterdir()]


def test_version_mismatch_precheck_and_stderr(backup_dir, monkeypatch):
    _fake_postgres(monkeypatch, server=18, client=17)
    error = _fails("DB_BACKUP_VERSION_MISMATCH")
    assert error.details == {"client_major": 17, "server_major": 18} and "POSTGRES_BIN" in error.message
    _fake_postgres(monkeypatch, server=None, dump_stderr="pg_dump: error: aborting because of server version mismatch\n"
                                                         "pg_dump: detail: server version: 18.0; pg_dump version: 17.2")
    assert _fails("DB_BACKUP_VERSION_MISMATCH").stage == "pg_dump"


def test_failure_leaves_no_files_and_never_leaks_the_password(backup_dir, monkeypatch, caplog):
    _fake_postgres(monkeypatch, dump_stderr=f"pg_dump: error: connection to server failed: password={SECRET}\n"
                                            "FATAL: password authentication failed for user simdash_app")
    error = _fails("DB_BACKUP_AUTH_FAILED")
    text = json.dumps(error.as_dict(), ensure_ascii=False)
    assert SECRET not in text and error.details["returncode"] == 1 and error.details["exception_type"] == "CalledProcessError"
    assert not [p for p in backup_dir.iterdir()]
    assert SECRET not in caplog.text


def test_verification_failure_discards_the_dump(backup_dir, monkeypatch):
    _fake_postgres(monkeypatch, restore_fail=True)
    assert _fails("DB_BACKUP_VERIFY_FAILED").stage == "pg_restore_list"
    assert not [p for p in backup_dir.iterdir()]


def test_failed_job_is_reported_by_the_api_and_audited(admin_client, backup_dir, monkeypatch):
    _fake_postgres(monkeypatch, dump_stderr="pg_dump: error: could not connect to server: Connection refused")
    assert admin_client.post("/api/admin/db-backups", json={}).status_code == 202
    job = _wait(admin_client)
    assert job["status"] == "failed" and job["error"]["code"] == "DB_BACKUP_CONNECTION_FAILED" and job["backup"] is None
    with connect() as conn:
        detail = conn.execute("SELECT detail_json FROM audit_events WHERE action='BACKUP_CREATED' "
                              "ORDER BY occurred_at DESC LIMIT 1").fetchone()[0]
    assert "DB_BACKUP_CONNECTION_FAILED" in detail


def test_backup_folder_inside_the_web_root_is_refused(monkeypatch):
    monkeypatch.setenv("SIMDASH_DB_BACKUP_DIR", str(db_backup.ROOT / "frontend" / "dist" / "backups"))
    with pytest.raises(db_backup.BackupError) as caught:
        db_backup.backup_dir()
    assert caught.value.code == "DB_BACKUP_DIR_IN_WEB_ROOT"


# -------------------------------------------------------------- retention
def test_retention_keeps_newest_automatic_and_all_manual(backup_dir, monkeypatch):
    _fake_postgres(monkeypatch)
    monkeypatch.setattr(db_backup, "_owner_url", lambda: None)
    monkeypatch.setenv("SIMDASH_DB_BACKUP_KEEP", "2")
    backup_dir.mkdir()

    def seed(stamp, reason, kind):
        stem = f"{stamp}_{reason}"
        (backup_dir / f"{stem}.dump").write_bytes(b"old")
        (backup_dir / f"{stem}.manifest.json").write_text(json.dumps(
            {"id": stem, "kind": kind, "size": 3, "created_at": f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}T00:00:00Z"}),
            encoding="utf-8")
        return stem
    manual = seed("20200101T000000Z", "manual", "manual")
    old = [seed(f"2020010{n}T000000Z", "auto", "automatic") for n in range(2, 6)]
    unknown = backup_dir / "20200101T000001Z_auto.dump"          # no manifest: kind unknown -> never auto-deleted
    unknown.write_bytes(b"?")
    db_backup.create_backup("auto", None)
    kept = {item["id"] for item in db_backup.list_backups()}
    assert manual in kept and unknown.stem in kept
    assert not ({old[0], old[1], old[2]} & kept)                # only the 2 newest automatic remain
    assert old[3] in kept
    assert len([i for i in db_backup.list_backups() if i["kind"] == "automatic"]) == 2


def test_manual_backups_do_not_trigger_pruning(backup_dir, monkeypatch):
    _fake_postgres(monkeypatch)
    monkeypatch.setenv("SIMDASH_DB_BACKUP_KEEP", "1")
    for _ in range(3):
        db_backup.create_backup("manual", "a")
    assert len(db_backup.list_backups()) == 3 and all(i["kind"] == "manual" for i in db_backup.list_backups())


def test_same_second_backups_get_distinct_ids(backup_dir, monkeypatch):
    _fake_postgres(monkeypatch)
    ids = {db_backup.create_backup("x y!", "a")["id"] for _ in range(3)}
    assert len(ids) == 3 and all(i.split("_", 1)[1].startswith("x-y") for i in ids)
