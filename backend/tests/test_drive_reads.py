"""Stage D2 SCX drive read path (docs/features/scx-drive.md §8, plan §4 D2).

Everything runs against the in-memory fake adapter (``drive_fakes``) with an
isolated DuckDB test database and synthetic folders; no real SDK, drive, user
database or running service is touched.  Every test also asserts that no drive
call happened while the calling thread held a database connection.
"""
from __future__ import annotations

import ast
import json
import sys
import uuid
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.database_connection import connect, connection_held
from app.main import app
from app.services import folder_auto_sync, spdm_storage
from app.services.drive import gateway as drive_gateway
from app.services.drive import reads as drive_reads
from app.services.storage import provider_for_root
from app.services.storage.drive import DriveRoot, DriveStorageProvider
from app.services.storage.provider import SpdmStorageError

from drive_fakes import ErrorCode, MemoryDrive, make_fake_adapter_module

pytestmark = pytest.mark.duckdb_integration

BACKEND = Path(__file__).resolve().parents[1]
SERVER = "https://scx.example.test/"
ENV = "/api/folder-discovery/environments"
REQUEST = "75R9J_PV/[WR-0001]_[유통_환경]"
CASE = f"{REQUEST}/Working/Package_Model_SetCase2_CushionCase2_조건표시"
OPTION = f"{CASE}/Drop/85qn80h_ref_organized/INDIVIDUAL"
CSV = "MAX_RESULT_Max_Stress_P1 (major)_Mid_C24_scene.h3d.csv"
CSV_BYTES = b"Position,Layer_1,Layer_2,Layer_3,Layer_4\nTOP,20,20,20,20\n"
CHANGED_BYTES = CSV_BYTES + b"BOTTOM,21,21,21,21\n"


def _bundle() -> str:
    return json.dumps({"server_url": SERVER, "access_token": "eyJ.test-access", "refresh_token": "refresh-test",
                       "obtained_at": "2026-10-07T01:02:03.123456Z", "account_hint": "spdm-shared"})


class Scx:
    def __init__(self, client: TestClient, drive: MemoryDrive, root: str, violations: list) -> None:
        self.client, self.drive, self.root, self.violations = client, drive, root, violations

    def path(self, rel: str) -> str:
        return f"{self.root}/{rel}"

    def add(self, rel: str, content: bytes = CSV_BYTES) -> None:
        self.drive.add_file(self.path(rel), content)

    def post(self, route: str, payload: dict) -> dict:
        response = self.client.post(route, json=payload)
        assert response.status_code == 200, response.text
        return response.json()


@pytest.fixture
def scx(tmp_path, monkeypatch, password_auth_bootstrap_admin):
    drive = MemoryDrive(page_size=50)
    monkeypatch.setitem(sys.modules, "scx_drive_adapter", make_fake_adapter_module(drive))
    root = f"SPDM/T{uuid.uuid4().hex[:10]}"   # own root_key per test (shared test database)
    for name, value in {
        "SIMDASH_DRIVE_GATEWAY": "scx", "SIMDASH_SCX_WORKER_PYTHON": sys.executable,
        "SIMDASH_SCX_SERVER_URL": SERVER, "SIMDASH_SCX_CLIENT_NAME": "test-client",
        "SIMDASH_SECRET_ENC_KEY": Fernet.generate_key().decode(), "SIMDASH_SCX_WORK_DIR": str(tmp_path / "scx-work"),
        "SIMDASH_SCX_DRIVE_ROOT": root, "AUTH_MODE": "password",
        "AUTH_SECRET_KEY": "drive-reads-isolated-test-secret-at-least-32",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("SIMDASH_SPDM_ROOT", raising=False)
    monkeypatch.delenv("SIMDASH_DRIVE_WRITES_ENABLED", raising=False)
    monkeypatch.setattr(folder_auto_sync, "MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(folder_auto_sync, "FORCE_MIN_INTERVAL_SECONDS", 0.0)
    folder_auto_sync.reset_for_tests()
    drive_reads.reset_for_tests()
    drive_gateway.shutdown()
    with connect() as conn:
        conn.execute("DELETE FROM drive_credentials")
    drive.add_dir(root)
    violations: list[tuple[str, str]] = []
    drive.on_call = lambda op, rel: violations.append((op, rel)) if connection_held() else None
    _, username, password = password_auth_bootstrap_admin
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": username, "password": password})
        assert login.status_code == 200, login.text
        client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        saved = client.put("/api/admin/drive/credentials", content=_bundle())
        assert saved.status_code == 200 and saved.json()["test"]["ok"], saved.text
        yield Scx(client, drive, root, violations)
    drive_gateway.shutdown()
    folder_auto_sync.reset_for_tests()
    assert violations == [], f"drive calls while a DB connection was held: {violations}"


def _register(scx: Scx) -> tuple[str, str]:
    for scene in ("2_Face", "3_Face"):
        scx.add(f"{OPTION}/{scene}/{CSV}")
    scan = scx.post(ENV + "/scan", {"environment": "DISTRIBUTION", "relative_path": ""})
    scenes = [n for n in scan["nodes"] if n["relative_path"] in {f"{OPTION}/2_Face", f"{OPTION}/3_Face"}]
    assert [n["role_kind"] for n in scenes] == ["SCENE", "SCENE"]
    preview = scx.post(ENV + "/previews", {"scan_id": scan["id"], "assignments": []})
    assert preview["can_apply"], preview
    registered = scx.post(ENV + "/registrations", {"preview_id": preview["id"], "idempotency_key": f"d2-{uuid.uuid4()}",
                                                  "capture": True})
    assert registered["capture_jobs"] and {job["status"] for job in registered["capture_jobs"]} == {"COMPLETED"}, registered
    with connect() as conn:
        request_id = conn.execute("SELECT request_id FROM folder_environment_registrations WHERE id=?",
                                  [registered["registration_id"]]).fetchone()[0]
    return registered["project_id"], request_id


def _sync(scx: Scx, project_id: str, request_id: str, **extra) -> dict:
    return scx.post(ENV + "/sync", {"project_id": project_id, "request_id": request_id, "environment": "DISTRIBUTION", **extra})


def _snapshots(request_id: str) -> int:
    with connect() as conn:
        return conn.execute("SELECT count(*) FROM folder_environment_scans WHERE request_id=? AND id LIKE 'folder-refresh-%'",
                            [request_id]).fetchone()[0]


def _versions(scx: Scx, rel_suffix: str) -> list[tuple]:
    root_key = DriveRoot(scx.root, "scx.example.test").root_key()
    with connect() as conn:
        return [tuple(row) for row in conn.execute(
            "SELECT version_no,review_state,source_state,superseded_at IS NULL,sha256 FROM drive_source_versions "
            "WHERE root_key=? AND rel_path LIKE ? ORDER BY version_no", [root_key, f"%{rel_suffix}"]).fetchall()]


def _scenes(scx: Scx, request_id: str) -> set[str]:
    response = scx.client.get("/api/dashboard/catalog", params={"request_id": request_id, "environment": "DISTRIBUTION"})
    assert response.status_code == 200, response.text
    return {str(scene.get("relative_path") or scene.get("label") or "").rsplit("/", 1)[-1] for scene in response.json()["scenes"]}


def _latest_asset(request_id: str, scene: str) -> bytes:
    with connect() as conn:
        row = conn.execute(
            "SELECT a.content FROM dashboard_assets a JOIN dashboard_captures c ON c.id=a.capture_id "
            "JOIN dashboard_cases d ON d.id=c.case_id WHERE d.request_id=? AND a.relative_path LIKE ? "
            "ORDER BY c.created_at DESC LIMIT 1", [request_id, f"%/{scene}/{CSV}"]).fetchone()
    return bytes(row[0])


# --- provider ---------------------------------------------------------------------------------

def test_scx_root_is_on_the_drive_and_provider_reads_through_the_gateway(scx):
    scx.add("A/one.csv", b"a,b\n1,2\n")
    with connect() as conn:
        current = spdm_storage.storage_root(conn)
    assert isinstance(current.root, DriveRoot) and current.locked and current.configured
    assert current.root.identity == f"scx:scx.example.test:/{scx.root}"
    fs = provider_for_root(current.root)
    assert isinstance(fs, DriveStorageProvider)
    assert [entry.name for entry in fs.list("")] == ["A"]
    entry = fs.stat("A/one.csv")
    assert entry.kind == "file" and entry.size == 8 and entry.etag.startswith("sha1:") and not entry.is_link
    assert fs.read_stable("A/one.csv") == b"a,b\n1,2\n"
    assert fs.stat("A/missing.csv") is None and not fs.is_dir("A/one.csv")
    with pytest.raises(FileNotFoundError):
        fs.list("missing")
    # local mode parity: the configured local root setting is refused, writes are refused
    with connect() as conn, pytest.raises(SpdmStorageError) as locked:
        spdm_storage.set_storage_root(conn, str(BACKEND))
    assert locked.value.code == "SPDM_ROOT_LOCKED"
    for write in (lambda: fs.mkdirs("A/B", zone="LEGACY"), lambda: fs.create_exclusive("A/x.csv", b"x", zone="LEGACY"),
                  lambda: fs.remove("A/one.csv", zone="FINAL"), lambda: fs.replace("A/one.csv", "A/two.csv", zone="FINAL")):
        with pytest.raises(SpdmStorageError) as refused:
            write()
        assert refused.value.code == "DRIVE_WRITE_DISABLED"
    assert {op for op, _ in scx.drive.calls} <= {"stat", "list_dir", "download_to", "root_identity"}


def test_drive_read_while_holding_a_connection_outside_a_session_is_refused(scx):
    scx.add("A/one.csv")
    root = spdm_storage.drive_storage_root().root
    fs = provider_for_root(root)
    before = len(scx.drive.calls)
    with connect():
        with pytest.raises(SpdmStorageError) as refused:
            fs.list("A")
    assert refused.value.code == drive_reads.NOT_PREPARED
    assert len(scx.drive.calls) == before   # the drive was never called with the connection open


def test_read_session_fetches_misses_without_a_connection_and_repeats_the_body(scx):
    scx.add("A/B/one.csv", b"1")
    scx.add("A/B/two.csv", b"2")
    root = spdm_storage.drive_storage_root().root
    rounds = []

    def body():
        rounds.append(1)
        with connect():
            fs = provider_for_root(root)
            return sorted(fs.read_stable(f"A/B/{entry.name}") for entry in fs.list("A/B"))

    assert drive_reads.run(body) == [b"1", b"2"]
    assert 2 <= len(rounds) <= 4   # one listing round, one content round (speculative batch)
    assert scx.drive.downloads() == [f"{scx.root}/A/B/one.csv", f"{scx.root}/A/B/two.csv"]
    staging = Path(drive_gateway.current_settings().staging_dir)
    assert not any(staging.iterdir()), "read session staging must be removed"


# --- registration, auto-reflect, pending changes ------------------------------------------------

def test_registration_captures_and_unchanged_sync_downloads_nothing(scx):
    project_id, request_id = _register(scx)
    assert _scenes(scx, request_id) >= {"2_Face", "3_Face"}
    first = _sync(scx, project_id, request_id)
    assert first["status"] in {"REFRESHED", "UNCHANGED"} and first["drive"]["pending_changes"] == 0
    downloads = len(scx.drive.downloads())
    for _ in range(3):
        quiet = _sync(scx, project_id, request_id)
        assert (quiet["status"], quiet["check_mode"]) == ("UNCHANGED", "QUICK"), quiet
    assert len(scx.drive.downloads()) == downloads, "an unchanged sync must not download files (S2-5)"
    rows = _versions(scx, f"2_Face/{CSV}")
    assert rows and rows[0][:4] == (1, "NONE", "PRESENT", True) and rows[0][4]   # sha256 recorded from the capture
    assert first["drive"]["token_kind"] == "sha1"


def test_changed_file_waits_for_confirmation_and_accept_registers_version_two(scx):
    project_id, request_id = _register(scx)
    _sync(scx, project_id, request_id)
    snapshots = _snapshots(request_id)
    scx.drive.modify_file(scx.path(f"{OPTION}/2_Face/{CSV}"), CHANGED_BYTES)

    pending = _sync(scx, project_id, request_id)
    assert pending["status"] == "UNCHANGED" and pending["drive"]["pending_changes"] == 1, pending
    assert _snapshots(request_id) == snapshots, "a changed existing file is not applied automatically"
    assert _versions(scx, f"2_Face/{CSV}")[0][:3] == (1, "PENDING", "CHANGED")
    listed = scx.client.get("/api/drive/source-changes", params={"project_id": project_id, "request_id": request_id}).json()
    assert [(item["kind"], item["review_state"]) for item in listed["items"]] == [("CHANGED", "PENDING")]
    assert listed["items"][0]["registered"]["size"] == len(CSV_BYTES) and listed["items"][0]["drive"]["size"] == len(CHANGED_BYTES)

    # a new Scene is still reflected automatically while the change waits (registered bytes stay in use)
    scx.add(f"{OPTION}/6_Corner/{CSV}")
    added = _sync(scx, project_id, request_id)
    assert (added["status"], added["changed"]) == ("REFRESHED", True), added
    assert "6_Corner" in _scenes(scx, request_id)
    assert _latest_asset(request_id, "2_Face") == CSV_BYTES

    accepted = scx.post("/api/drive/source-changes/accept", {"project_id": project_id, "request_id": request_id})
    assert (accepted["accepted"], accepted["pending_changes"]) == (1, 0)
    assert _versions(scx, f"2_Face/{CSV}")[1][4], "accept records the verified sha256 (L4)"
    applied = _sync(scx, project_id, request_id)
    assert (applied["status"], applied["changed"]) == ("REFRESHED", True), applied
    assert _latest_asset(request_id, "2_Face") == CHANGED_BYTES
    versions = _versions(scx, f"2_Face/{CSV}")
    assert [(row[0], row[3]) for row in versions] == [(1, False), (2, True)]   # version 1 kept, superseded
    assert versions[1][4], "the accepted version's sha256 is recorded after it was read"


def test_dismissed_change_is_not_asked_again_until_the_next_change(scx):
    project_id, request_id = _register(scx)
    _sync(scx, project_id, request_id)
    target = scx.path(f"{OPTION}/3_Face/{CSV}")
    scx.drive.modify_file(target, CHANGED_BYTES)
    assert _sync(scx, project_id, request_id)["drive"]["pending_changes"] == 1
    dismissed = scx.post("/api/drive/source-changes/dismiss", {"project_id": project_id, "request_id": request_id})
    assert (dismissed["dismissed"], dismissed["pending_changes"], dismissed["ignored"]) == (1, 0, 1)
    assert _sync(scx, project_id, request_id)["drive"]["pending_changes"] == 0
    scx.drive.modify_file(target, CHANGED_BYTES + b"MID,1,1,1,1\n")
    assert _sync(scx, project_id, request_id)["drive"]["pending_changes"] == 1


def test_deleted_source_is_missing_and_db_data_is_kept(scx):
    project_id, request_id = _register(scx)
    _sync(scx, project_id, request_id)
    snapshots = _snapshots(request_id)
    scx.drive.delete(scx.path(f"{OPTION}/3_Face"))
    result = _sync(scx, project_id, request_id)
    assert result["status"] == "UNCHANGED" and result["drive"]["missing"] == 1, result
    assert _snapshots(request_id) == snapshots and "3_Face" in _scenes(scx, request_id)
    assert _versions(scx, f"3_Face/{CSV}")[0][:3] == (1, "NONE", "MISSING")
    listed = scx.client.get("/api/drive/source-changes", params={"project_id": project_id, "request_id": request_id}).json()
    assert [item["kind"] for item in listed["items"]] == ["MISSING"]
    # a later new Scene still refreshes; the missing Scene stays at its registered version
    scx.add(f"{OPTION}/7_Edge/{CSV}")
    assert _sync(scx, project_id, request_id)["status"] == "REFRESHED"
    assert {"3_Face", "7_Edge"} <= _scenes(scx, request_id)


def test_size_and_time_tokens_compare_content_before_asking(scx):
    scx.drive.list_has_sha1 = False   # C1/C9 unknown: listings without sha1
    project_id, request_id = _register(scx)
    first = _sync(scx, project_id, request_id)
    assert first["drive"]["token_kind"] == "size_mtime"
    target = scx.path(f"{OPTION}/2_Face/{CSV}")
    scx.drive.touch(target)    # same bytes, new time: content compared, no question
    assert _sync(scx, project_id, request_id)["drive"]["pending_changes"] == 0
    assert _versions(scx, f"2_Face/{CSV}")[0][:3] == (1, "NONE", "PRESENT")
    scx.drive.modify_file(target, CSV_BYTES.replace(b"20", b"30"))   # same size, new bytes
    assert _sync(scx, project_id, request_id)["drive"]["pending_changes"] == 1
    observed = scx.client.get("/api/admin/drive/status").json()["version_tokens"]
    assert observed["last_kind"] == "size_mtime" and observed["size_mtime_files"] > 0


@pytest.mark.parametrize("code, expected", [("BUSY", "SPDM_FILE_BUSY"), ("TIMEOUT", "DRIVE_TIMEOUT"),
                                            ("AUTH_REQUIRED", "DRIVE_AUTH_REQUIRED")])
def test_drive_errors_fail_the_sync_without_touching_registered_versions(scx, code, expected):
    project_id, request_id = _register(scx)
    _sync(scx, project_id, request_id)
    before = _versions(scx, f"2_Face/{CSV}")
    scx.drive.modify_file(scx.path(f"{OPTION}/2_Face/{CSV}"), CHANGED_BYTES)
    scx.drive.fail_always["list_dir"] = ErrorCode[code]
    failed = _sync(scx, project_id, request_id)
    assert (failed["status"], failed["code"]) == ("FAILED", expected), failed
    assert _versions(scx, f"2_Face/{CSV}") == before
    scx.drive.fail_always.clear()
    assert _sync(scx, project_id, request_id)["drive"]["pending_changes"] == 1


def test_busy_download_is_retried_by_the_next_sync(scx):
    project_id, request_id = _register(scx)
    _sync(scx, project_id, request_id)
    added = scx.path(f"{OPTION}/8_Busy/{CSV}")
    scx.drive.add_file(added, CSV_BYTES)
    scx.drive.busy_paths.add(added)
    busy = _sync(scx, project_id, request_id)
    assert busy["status"] == "FAILED", busy
    scx.drive.busy_paths.clear()
    assert _sync(scx, project_id, request_id)["status"] == "REFRESHED"
    assert "8_Busy" in _scenes(scx, request_id)


# --- review fixes (D2 independent review: M1, M2, L1–L5) ----------------------------------------

def _root_key(scx: Scx) -> str:
    return DriveRoot(scx.root, "scx.example.test").root_key()


def _assets_with(request_id: str, content: bytes) -> int:
    with connect() as conn:
        found = conn.execute(
            "SELECT a.content FROM dashboard_assets a JOIN dashboard_captures c ON c.id=a.capture_id "
            "JOIN dashboard_cases d ON d.id=c.case_id WHERE d.request_id=?", [request_id]).fetchall()
    return sum(1 for row in found if row[0] is not None and bytes(row[0]) == content)


def test_file_back_after_confirmed_removal_is_tracked_again(scx):
    """M1: delete → sync → accept MISSING → re-add → sync → modify → sync asks again (never applied silently)."""
    project_id, request_id = _register(scx)
    _sync(scx, project_id, request_id)
    target = scx.path(f"{OPTION}/3_Face/{CSV}")
    scx.drive.delete(scx.path(f"{OPTION}/3_Face"))
    assert _sync(scx, project_id, request_id)["drive"]["missing"] == 1
    removed = scx.post("/api/drive/source-changes/accept", {"project_id": project_id, "request_id": request_id})
    assert removed["removed"] == 1
    _sync(scx, project_id, request_id)
    scx.add(f"{OPTION}/3_Face/{CSV}")
    back = _sync(scx, project_id, request_id)
    assert back["drive"]["new_files"] == 1, back
    versions = _versions(scx, f"3_Face/{CSV}")
    assert [(row[0], row[2], row[3]) for row in versions] == [(1, "MISSING", False), (2, "PRESENT", True)]
    scx.drive.modify_file(target, CHANGED_BYTES)
    changed = _sync(scx, project_id, request_id)
    assert changed["drive"]["pending_changes"] == 1 and changed["status"] == "UNCHANGED", changed
    assert _versions(scx, f"3_Face/{CSV}")[-1][:3] == (2, "PENDING", "CHANGED")
    assert _assets_with(request_id, CHANGED_BYTES) == 0


def test_version_rows_take_the_next_number_and_never_vanish(scx):
    """M1: the version insert uses MAX(version_no)+1 and reports a row it could not store."""
    from app.services.drive import sources
    from app.services.drive.reads import Version

    root_key = _root_key(scx)
    version = Version(token="t:1:2", size=1, modified_us=2, sha1=None)
    with connect() as conn:
        assert sources._insert(conn, root_key, "P/R/a.csv", version, scope_ids=None, actor="t")
        conn.execute("UPDATE drive_source_versions SET superseded_at=CURRENT_TIMESTAMP WHERE root_key=?", [root_key])
        assert sources._insert(conn, root_key, "P/R/a.csv", version, scope_ids=None, actor="t")
        assert not sources._insert(conn, root_key, "P/R/a.csv", version, scope_ids=None, actor="t")   # current row exists
        numbers = [row[0] for row in conn.execute("SELECT version_no FROM drive_source_versions WHERE root_key=? "
                                                  "ORDER BY version_no", [root_key]).fetchall()]
    assert numbers == [1, 2]


def test_manual_capture_before_the_next_sync_keeps_the_registered_version(scx):
    """M2: a drive change no sync has classified yet is not captured (fail closed)."""
    project_id, request_id = _register(scx)
    _sync(scx, project_id, request_id)
    scx.drive.modify_file(scx.path(f"{OPTION}/2_Face/{CSV}"), CHANGED_BYTES)
    downloads = len(scx.drive.downloads())
    response = scx.client.post("/api/dashboard/captures", json={
        "project_id": project_id, "request_id": request_id, "root_relative_path": CASE, "environment": "DISTRIBUTION"})
    assert response.status_code == 200, response.text
    assert _latest_asset(request_id, "2_Face") == CSV_BYTES
    assert _assets_with(request_id, CHANGED_BYTES) == 0
    assert f"{scx.root}/{OPTION}/2_Face/{CSV}" not in scx.drive.downloads()[downloads:]
    assert _sync(scx, project_id, request_id)["drive"]["pending_changes"] == 1


def test_capture_retry_before_the_next_sync_never_reads_the_unreviewed_change(scx):
    """M2: capture retry between a drive change and the next sync."""
    for scene in ("2_Face", "3_Face"):
        scx.add(f"{OPTION}/{scene}/{CSV}")
    busy = scx.path(f"{OPTION}/3_Face/{CSV}")
    scx.drive.busy_paths.add(busy)
    scan = scx.post(ENV + "/scan", {"environment": "DISTRIBUTION", "relative_path": ""})
    preview = scx.post(ENV + "/previews", {"scan_id": scan["id"], "assignments": []})
    registered = scx.post(ENV + "/registrations", {"preview_id": preview["id"], "idempotency_key": f"d2-{uuid.uuid4()}",
                                                  "capture": True})
    assert {job["status"] for job in registered["capture_jobs"]} == {"FAILED"}, registered
    scx.drive.busy_paths.clear()
    project_id, request_id = registered["project_id"], registered["request_id"]
    _sync(scx, project_id, request_id)       # classifies the request folder (rows of every result file)
    scx.drive.modify_file(scx.path(f"{OPTION}/2_Face/{CSV}"), CHANGED_BYTES)
    retried = scx.post(f"{ENV}/registrations/{registered['registration_id']}/capture/retry", {})
    jobs = retried["capture_jobs"]
    assert _assets_with(request_id, CHANGED_BYTES) == 0
    # the registered version was never captured (its bytes are nowhere on the server): fail closed, retryable
    assert [(job["status"], job["error_code"]) for job in jobs] == [("FAILED", drive_reads.SOURCE_CHANGED)], jobs
    assert _sync(scx, project_id, request_id)["drive"]["pending_changes"] == 1


def test_refresh_fails_when_the_drive_folder_cannot_be_classified(scx):
    """M2: /refresh does not read unclassified drive files when classification fails."""
    project_id, request_id = _register(scx)
    _sync(scx, project_id, request_id)
    snapshots = _snapshots(request_id)
    scx.drive.modify_file(scx.path(f"{OPTION}/2_Face/{CSV}"), CHANGED_BYTES)
    scx.drive.fail_always["list_dir"] = ErrorCode.TIMEOUT
    payload = {"project_id": project_id, "request_id": request_id, "environment": "DISTRIBUTION"}
    failed = scx.client.post(ENV + "/refresh", json=payload)
    assert failed.status_code == 504 and failed.json()["detail"]["code"] == "DRIVE_TIMEOUT", failed.text
    assert _snapshots(request_id) == snapshots and _assets_with(request_id, CHANGED_BYTES) == 0
    with connect() as conn:
        audit = conn.execute("SELECT detail_json FROM audit_events WHERE action='FOLDER_ENVIRONMENT_REFRESH_FAILED' "
                             "ORDER BY occurred_at DESC LIMIT 1").fetchone()
    assert audit and "DRIVE_TIMEOUT" in str(audit[0])
    scx.drive.fail_always.clear()
    refreshed = scx.post(ENV + "/refresh", payload)   # classified now: the change waits for confirmation
    assert refreshed["status"] in {"UNCHANGED", "REFRESHED"} and _assets_with(request_id, CHANGED_BYTES) == 0
    assert _versions(scx, f"2_Face/{CSV}")[0][:2] == (1, "PENDING")


def test_a_read_miss_after_a_commit_fails_instead_of_repeating_the_body(scx):
    """L1: inside ``committed_phase``/after ``after_commit`` a miss is DRIVE_READ_NOT_PREPARED, the body runs once."""
    scx.add("A/one.csv", b"1")
    root = spdm_storage.drive_storage_root().root
    rounds = []

    def body():
        rounds.append(1)
        with connect():
            with drive_reads.committed_phase():
                provider_for_root(root).list("A")

    with pytest.raises(SpdmStorageError) as refused:
        drive_reads.run(body)
    assert refused.value.code == drive_reads.NOT_PREPARED and rounds == [1]
    rounds.clear()

    def body_after_commit():
        rounds.append(1)
        drive_reads.after_commit()
        with connect():
            provider_for_root(root).list("A")

    with pytest.raises(SpdmStorageError):
        drive_reads.run(body_after_commit)
    assert rounds == [1]


def test_blob_store_evicts_only_its_own_blobs_and_refuses_overlapping_folders(tmp_path, monkeypatch):
    """L2: foreign files are never evicted; the blob folder cannot be (or contain) the work/staging folders."""
    import hashlib
    import os
    import time

    from app.services.drive.config import DriveConfigError, drive_settings
    from app.services.storage.server_local import BlobStore

    store_dir = tmp_path / "blobs"
    foreign = [store_dir / "ab" / "not-a-blob.txt", store_dir / "zz" / ("a" * 64), store_dir / "ab" / ("c" * 64)]
    for path in foreign:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 1000)
        os.utime(path, (time.time() - 100, time.time() - 100))
    old = b"o" * 1500
    old_path = store_dir / hashlib.sha256(old).hexdigest()[:2] / hashlib.sha256(old).hexdigest()
    old_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.write_bytes(old)
    os.utime(old_path, (time.time() - 50, time.time() - 50))
    data = b"y" * 2000
    source = tmp_path / "src.bin"
    source.write_bytes(data)
    BlobStore(store_dir, max_bytes=2500).put(source, hashlib.sha256(data).hexdigest())
    assert all(path.exists() for path in foreign) and not old_path.exists()
    work = tmp_path / "work"
    for bad, kwargs in ((work, {}), (tmp_path, {}), (work / "staging" / "blobs", {})):
        with pytest.raises(ValueError):
            BlobStore(bad, 10 ** 9, reserved=(work, work / "staging"), outside=(work / "staging",), **kwargs)
    BlobStore(work / "blobs", 10 ** 9, reserved=(work, work / "staging"), outside=(work / "staging",))
    for name, value in {"SIMDASH_DRIVE_GATEWAY": "scx", "SIMDASH_SCX_WORKER_PYTHON": sys.executable,
                        "SIMDASH_SCX_SERVER_URL": SERVER, "SIMDASH_SCX_CLIENT_NAME": "test-client",
                        "SIMDASH_SECRET_ENC_KEY": Fernet.generate_key().decode(), "SIMDASH_SCX_WORK_DIR": str(work),
                        "SIMDASH_DRIVE_BLOB_DIR": str(work)}.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(DriveConfigError) as refused:
        drive_settings()
    assert refused.value.args and "SIMDASH_DRIVE_BLOB_DIR" in str(refused.value.args[0]) + str(refused.value)


def test_read_session_staging_has_one_byte_budget_and_a_free_space_floor(scx, monkeypatch):
    """L3: a session stops downloading at its staging budget or when the staging disk runs low."""
    from app.services.storage import server_local

    scx.add("A/one.csv", b"1" * 100)
    scx.add("A/two.csv", b"2" * 100)
    root = spdm_storage.drive_storage_root().root

    def read_all():
        with connect():
            fs = provider_for_root(root)
            return [fs.read_stable(f"A/{entry.name}") for entry in fs.list("A")]

    monkeypatch.setattr(drive_reads, "SESSION_STAGING_MAX_BYTES", 150)
    with pytest.raises(SpdmStorageError) as full:
        drive_reads.run(read_all)
    assert full.value.code == drive_reads.STAGING_FULL
    monkeypatch.setattr(drive_reads, "SESSION_STAGING_MAX_BYTES", 10 ** 9)
    monkeypatch.setattr(server_local, "disk_free", lambda _path: drive_reads.STAGING_MIN_FREE_BYTES + 50)
    with pytest.raises(SpdmStorageError) as low:
        drive_reads.run(read_all)
    assert low.value.code == drive_reads.STAGING_FULL
    monkeypatch.setattr(server_local, "disk_free", lambda _path: None)
    assert drive_reads.run(read_all) == [b"1" * 100, b"2" * 100]


def test_accept_downloads_and_records_the_pending_version_or_refuses_a_newer_one(scx):
    """L4: [새 버전 등록] verifies the pending drive version and records its sha256 at once."""
    import hashlib

    project_id, request_id = _register(scx)
    _sync(scx, project_id, request_id)
    target = scx.path(f"{OPTION}/2_Face/{CSV}")
    scx.drive.modify_file(target, CHANGED_BYTES)
    assert _sync(scx, project_id, request_id)["drive"]["pending_changes"] == 1
    scx.drive.modify_file(target, CHANGED_BYTES + b"AGAIN,1,1,1,1\n")   # changed again before the decision
    stale = scx.client.post("/api/drive/source-changes/accept", json={"project_id": project_id, "request_id": request_id})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "DRIVE_SOURCE_CHANGE_STALE", stale.text
    assert [row[:4] for row in _versions(scx, f"2_Face/{CSV}")] == [(1, "PENDING", "CHANGED", True)]
    scx.drive.modify_file(target, CHANGED_BYTES)
    _sync(scx, project_id, request_id)
    accepted = scx.post("/api/drive/source-changes/accept", {"project_id": project_id, "request_id": request_id})
    assert accepted["accepted"] == 1
    versions = _versions(scx, f"2_Face/{CSV}")
    assert versions[-1][:4] == (2, "NONE", "PRESENT", True)
    assert versions[-1][4] == hashlib.sha256(CHANGED_BYTES).hexdigest()   # before any sync read it


def test_version_of_normalises_sha1_values():
    """L5: sha1 is lower-case 40 hex digits or None (PG VARCHAR(40) and DuckDB alike)."""
    from types import SimpleNamespace

    upper = "A" * 40
    assert drive_reads.version_of(SimpleNamespace(sha1=upper, size=3, modified_at=None)).sha1 == "a" * 40
    for bad in ("xyz", "a" * 41, "g" * 40, ""):
        version = drive_reads.version_of(SimpleNamespace(sha1=bad, size=3, modified_at=None))
        assert version.sha1 is None and version.token.startswith("t:3:")


def test_registered_content_never_lands_outside_the_session_folder(scx, tmp_path):
    """Info: the local file name of a registered version comes from a DB path; unsafe names are refused."""
    from app.services.drive.reads import Accepted, DriveReadSession, Version

    staging = tmp_path / "session"
    staging.mkdir()
    session = DriveReadSession(spdm_root=scx.root, root_key=_root_key(scx), staging=staging)
    for rel in ("A/..\\x.csv", "A/c:x.csv", "A/../x.csv"):
        session.overlay[rel] = Accepted(rel_path=rel, version=Version(token="t:1:1", size=1, modified_us=1, sha1=None),
                                        sha256="0" * 64, stored_path=None, source_state="CHANGED", review_state="PENDING")
        with pytest.raises(SpdmStorageError) as refused:
            drive_reads._old_content(session, rel)
        assert refused.value.code == drive_reads.SOURCE_CHANGED
    assert list(staging.iterdir()) == []


# --- write availability -------------------------------------------------------------------------

def test_write_endpoints_answer_drive_write_disabled_and_status_reports_it(scx):
    status = scx.client.get("/api/drive/status").json()
    assert (status["mode"], status["writes_available"], status["writes_enabled"]) == ("scx", False, False)
    admin = scx.client.get("/api/admin/drive/status").json()
    assert (admin["writes_available"], admin["writes_enabled"]) == (False, False)
    for method, route in (("POST", "/api/result-registration/folders/prepare"),
                          ("POST", "/api/result-registration/drop-uploads"),
                          ("POST", "/api/dashboard/finalizations/confirm"),
                          ("POST", "/api/load-cases/x/storage/upload")):
        response = scx.client.request(method, route, json={})
        assert response.status_code == 409 and response.json()["detail"]["code"] == "DRIVE_WRITE_DISABLED", (route, response.text)
    assert not any(op in {"upload_new", "mkdirs", "copy_within"} for op, _ in scx.drive.calls)


def test_writes_enabled_setting_makes_d3_writes_available(scx, monkeypatch):
    """D3: with "드라이브 쓰기 허용" the drive write path is available (tests/test_drive_writes.py)."""
    monkeypatch.setenv("SIMDASH_DRIVE_WRITES_ENABLED", "true")
    drive_gateway.shutdown()
    status = scx.client.get("/api/drive/status").json()
    assert (status["writes_enabled"], status["writes_available"]) == (True, True)


def test_source_changes_api_requires_scx_and_a_matching_request(scx, monkeypatch):
    project_id, request_id = _register(scx)
    mismatch = scx.client.get("/api/drive/source-changes", params={"project_id": "other", "request_id": request_id})
    assert mismatch.status_code == 404 and mismatch.json()["detail"]["code"] == "DRIVE_SOURCE_SCOPE_MISMATCH"
    stale = scx.client.post("/api/drive/source-changes/accept",
                            json={"project_id": project_id, "request_id": request_id, "ids": ["drive-source-none"]})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "DRIVE_SOURCE_CHANGE_STALE"


# --- static rules -------------------------------------------------------------------------------

def test_read_path_calls_only_read_methods_of_the_gateway():
    """D2 never writes to the drive: the read modules call list_dir/stat/download_to only."""
    sources = [BACKEND / "app" / "services" / "drive" / name for name in ("reads.py", "sources.py")]
    sources.append(BACKEND / "app" / "services" / "storage" / "drive.py")
    called = set()
    for source in sources:
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) \
                    and node.func.value.id == "gateway":
                called.add(node.func.attr)
    assert called == {"list_dir", "stat", "download_to"}


def test_auto_discovery_registers_a_new_drive_request_with_captures(scx, monkeypatch):
    from app.services import folder_auto_discovery

    folder_auto_discovery.reset_for_tests()
    monkeypatch.setattr(folder_auto_discovery, "MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(folder_auto_discovery, "FORCE_MIN_INTERVAL_SECONDS", 0.0)
    project = f"{uuid.uuid4().hex[:5]}_PV"   # projects of earlier tests share the database
    request = f"{project}/[WR-0007]_[유통_환경]"
    option = f"{request}/Working/Package_Model_SetCase3_CushionCase1/Drop/85qn80h_ref_organized/INDIVIDUAL"
    for scene in ("2_Face", "3_Face"):
        scx.add(f"{option}/{scene}/{CSV}")
    scx.drive.add_dir(scx.path(f"{request}/Final"))
    result = scx.post(ENV + "/discover", {"force": True})
    assert [item["name"] for item in result["created_requests"]] == ["[WR-0007]_[유통_환경]"], result
    request_id = result["created_requests"][0]["id"]
    with connect() as conn:
        jobs = conn.execute("SELECT j.status FROM folder_environment_capture_jobs j JOIN folder_environment_registrations r "
                            "ON r.id=j.registration_id WHERE r.request_id=?", [request_id]).fetchall()
    assert jobs and {row[0] for row in jobs} == {"COMPLETED"}
    assert {"2_Face", "3_Face"} <= _scenes(scx, request_id)
    folder_auto_discovery.reset_for_tests()


def test_duckdb_bootstrap_adds_drive_source_versions_to_an_existing_database():
    """DuckDB parity of migration 0037 on a database that already has data (additive only)."""
    from app.config import database_settings
    from app.database import initialize_database

    if database_settings().backend != "duckdb":
        pytest.skip("DuckDB bootstrap only; PostgreSQL uses migration 0037")
    with connect() as conn:
        conn.execute("DROP TABLE IF EXISTS drive_source_versions")
        conn.execute("INSERT INTO projects(id,name,product_name,description,created_at) VALUES "
                     "('drive-d2-upgrade-project','keep','p','d',CURRENT_TIMESTAMP) ON CONFLICT DO NOTHING")
    initialize_database()
    with connect() as conn:
        columns = {row[0] for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='drive_source_versions'").fetchall()}
        kept = conn.execute("SELECT name FROM projects WHERE id='drive-d2-upgrade-project'").fetchone()
    assert {"root_key", "rel_path", "version_no", "version_token", "pending_version_token", "review_state",
            "source_state", "superseded_at", "sha256", "stored_path"} <= columns
    assert kept == ("keep",)
