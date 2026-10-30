"""SCX relay for PhysicsAI (docs/features/scx-relay.md §8).

Everything runs against the in-memory fake adapter (``drive_fakes``, job API with
``jobs=True``) and an isolated test database; no real SDK, drive, PhysicsAI, user database or
running service is touched.  Relay requests come from a loopback TestClient
(``client=("127.0.0.1", …)``); the queue runs on the test thread (``run_until_idle``).  Every
test asserts that no drive or job call happened while the calling thread held a database
connection.
"""
from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import logging
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.database_connection import connect, connection_held
from app.main import app
from app.routers import scx_relay as relay_router
from app.services.drive import gateway as drive_gateway
from app.services.drive import reads as drive_reads
from app.services.drive import relay
from app.services.drive import upload_queue
from app.services.storage import server_local
from app.services.storage.provider import SpdmStorageError

from drive_fakes import ErrorCode, JobErrorCode, MemoryDrive, make_fake_adapter_module
from test_drive_reads import SERVER, _bundle

pytestmark = pytest.mark.duckdb_integration

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
API = relay.PREFIX
SECRET = "relayTestKey_" + "x7" * 20 + "-Q"
SPDM = "SPDM/Projects"
JOB_DIR = "cushion/01_D-003_run__00012_20261017T105700"
STARTER = f"{JOB_DIR}/cushion_0000.rad"
ENGINE = f"{JOB_DIR}/cushion_0001.rad"
INCLUDE = f"{JOB_DIR}/part_1.inc"
LOOPBACK = ("127.0.0.1", 51234)


@dataclass
class Relay:
    client: TestClient
    drive: MemoryDrive
    admin: dict[str, str]
    violations: list

    def get(self, route: str, **kwargs):
        return self.client.get(API + route, headers={**HEADERS, **kwargs.pop("headers", {})}, **kwargs)

    def post(self, route: str, **kwargs):
        return self.client.post(API + route, headers={**HEADERS, **kwargs.pop("headers", {})}, **kwargs)

    def upload(self, folder: str, name: str, data: bytes, *, request_id: str, digest: str | None = None, **headers):
        return self.client.post(API + "/files", params={"dir": folder, "name": name}, content=data, headers={
            **HEADERS, "Content-Type": "application/octet-stream", "X-Request-Id": request_id,
            "X-Content-SHA256": digest or hashlib.sha256(data).hexdigest(), **headers})


HEADERS = {"X-SCX-Relay-Key": SECRET, "X-SCX-Relay-Client": "physicsai"}


def _secret_file(tmp_path: Path, value: str = SECRET) -> Path:
    path = tmp_path / "state" / "secrets" / "scx_relay.key"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value + "\n", encoding="utf-8")
    return path


def _env(monkeypatch, tmp_path: Path, *, mode: str = "scx", relay_on: bool = True, **overrides: str) -> None:
    values = {
        "SIMDASH_DRIVE_GATEWAY": mode, "SIMDASH_SCX_WORKER_PYTHON": sys.executable, "SIMDASH_SCX_SERVER_URL": SERVER,
        "SIMDASH_SCX_CLIENT_NAME": "test-client", "SIMDASH_SECRET_ENC_KEY": Fernet.generate_key().decode(),
        "SIMDASH_SCX_WORK_DIR": str(tmp_path / "scx-work"), "SIMDASH_SCX_DRIVE_ROOT": SPDM, "AUTH_MODE": "password",
        "SIMDASH_DRIVE_WRITES_ENABLED": "true", "AUTH_SECRET_KEY": "scx-relay-isolated-test-secret-at-least-32",
        "SIMDASH_SCX_RELAY_SECRET_FILE": str(_secret_file(tmp_path)),
    }
    if relay_on:
        values["SIMDASH_SCX_RELAY_ENABLED"] = "true"
    else:
        monkeypatch.delenv("SIMDASH_SCX_RELAY_ENABLED", raising=False)
    values.update(overrides)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    for name in ("SIMDASH_SCX_RELAY_DRIVE_BASE", "SIMDASH_SCX_RELAY_JOB_PATH_FORM", "SIMDASH_SCX_RELAY_HOME_ABS",
                 "SIMDASH_SCX_RELAY_MAX_UPLOAD_BYTES", "SIMDASH_SCX_RELAY_MAX_DOWNLOAD_BYTES"):
        if name not in overrides:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("SIMDASH_SPDM_ROOT", raising=False)


def _reset() -> None:
    upload_queue.reset_for_tests()
    drive_reads.reset_for_tests()
    relay.shutdown()
    drive_gateway.shutdown()
    with connect() as conn:
        conn.execute("DELETE FROM drive_credentials")
        conn.execute("DELETE FROM drive_connected_account")


@pytest.fixture
def start(tmp_path, monkeypatch, password_auth_bootstrap_admin):
    """``start(jobs=True, **env)`` → a running app with the relay on (scx mode, writes on, fake adapter)."""
    monkeypatch.setattr(upload_queue, "AUTOSTART", False)
    clients: list[TestClient] = []
    violations: list[tuple[str, str]] = []

    def run(*, jobs: bool = True, register: bool = True, mode: str = "scx", relay_on: bool = True, **env: str) -> Relay:
        drive = MemoryDrive(page_size=50)
        monkeypatch.setitem(sys.modules, "scx_drive_adapter", make_fake_adapter_module(drive, jobs=jobs))
        _env(monkeypatch, tmp_path, mode=mode, relay_on=relay_on, **env)
        _reset()
        drive.add_dir(SPDM)
        drive.add_dir("cushion")
        drive.on_call = lambda op, rel: violations.append((op, rel)) if connection_held() else None
        client = TestClient(app, client=LOOPBACK)
        client.__enter__()
        clients.append(client)
        _, username, password = password_auth_bootstrap_admin
        login = client.post("/api/auth/login", json={"username": username, "password": password})
        assert login.status_code == 200, login.text
        admin = {"Authorization": f"Bearer {login.json()['access_token']}"}
        if register and mode == "scx":
            saved = client.put("/api/admin/drive/credentials", content=_bundle(), headers=admin)
            assert saved.status_code == 200 and saved.json()["test"]["ok"], saved.text
        return Relay(client, drive, admin, violations)

    yield run
    for client in clients:
        client.__exit__(None, None, None)
    _reset()
    assert violations == [], f"drive calls while a DB connection was held: {violations}"


def _settle() -> int:
    assert not connection_held()
    return upload_queue.run_until_idle()


def _submit_body(request_id: str = "pai-1", **changes) -> dict:
    body = {"request_id": request_id, "input_path": STARTER, "engine_paths": [ENGINE], "additional_paths": [INCLUDE],
            "version": "2026.1", "resources": {"appliance": "radioss-appliance", "ncpu": 16, "memory_gb": None,
                                               "queue_type": "implicit"},
            "run_type": "both - use starter and engine files", "mpi": "SMP (default)", "single_precision": False,
            "client_ref": {"app": "physicsai", "job_id": "job-a", "hpc_job_id": "1", "run_key": "run__00012"}}
    body.update(changes)
    return body


def _stage_inputs(env: Relay) -> None:
    for path in (STARTER, ENGINE, INCLUDE):
        env.drive.add_file(path, b"deck " + path.encode())


def _row(request_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT state, job_id, error_code, request_sha256, output_rel, last_status "
                           "FROM scx_relay_submissions WHERE request_id=?", [request_id]).fetchone()
    return dict(zip(("state", "job_id", "error_code", "request_sha256", "output_rel", "last_status"), row)) if row else None


def _submits(env: Relay) -> list:
    return [ref for op, ref in env.drive.job_calls if op == "submit_radioss"]


def _code(response) -> str:
    return response.json()["code"]


# --- access (§3) ----------------------------------------------------------------------------------

def test_disabled_relay_answers_404_for_every_route(start):
    env = start(relay_on=False)
    for method, route in (("GET", "/health"), ("POST", "/mkdirs"), ("POST", "/files"), ("GET", "/batches/x"),
                          ("GET", "/list"), ("GET", "/files"), ("POST", "/jobs/radioss"), ("GET", "/jobs/j"),
                          ("POST", "/jobs/j/terminate"), ("POST", "/submissions/pai-1/resolve")):
        response = env.client.request(method, API + route, headers=HEADERS)
        assert response.status_code == 404 and _code(response) == "RELAY_DISABLED", (route, response.text)
        assert response.headers["cache-control"] == "no-store"
    assert env.drive.job_calls == []


def test_none_mode_with_relay_on_answers_503_after_the_key_check(start):
    env = start(mode="none")
    assert env.client.get(API + "/health").status_code == 401
    response = env.get("/health")
    assert response.status_code == 503 and _code(response) == "DRIVE_MODE_DISABLED"


def test_missing_or_wrong_key_is_401_with_the_same_body(start):
    env = start()
    missing = env.client.get(API + "/health")
    wrong = env.client.get(API + "/health", headers={"X-SCX-Relay-Key": SECRET[:-1] + "Z"})
    empty = env.client.get(API + "/health", headers={"X-SCX-Relay-Key": ""})
    assert missing.status_code == wrong.status_code == empty.status_code == 401
    assert missing.json() == wrong.json() == empty.json()
    assert _code(missing) == "RELAY_UNAUTHORIZED"


@pytest.mark.parametrize("header", ["X-Forwarded-For", "X-Forwarded-Proto", "X-Forwarded-Host", "X-Forwarded-Port",
                                    "X-Forwarded-Prefix", "Forwarded", "X-Real-IP", "X-Simdash-Proxy-Peer"])
def test_proxied_request_is_forbidden(start, header):
    env = start()
    response = env.get("/health", headers={header: "127.0.0.1"})
    assert response.status_code == 403 and _code(response) == "RELAY_FORBIDDEN"


def test_non_loopback_client_is_forbidden(start):
    env = start()
    for host in ("testclient", "10.0.0.5", "192.168.1.20", "::ffff:127.0.0.1"):
        response = TestClient(app, client=(host, 40000)).get(API + "/health", headers=HEADERS)   # no second lifespan
        assert response.status_code == 403 and _code(response) == "RELAY_FORBIDDEN", host
    assert env.get("/health").status_code == 200


def test_key_comparison_uses_hmac_compare_digest():
    tree = ast.parse((BACKEND / "app" / "services" / "drive" / "relay.py").read_text(encoding="utf-8"))
    calls = {f"{node.func.value.id}.{node.func.attr}" for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)}
    assert "hmac.compare_digest" in calls
    source = (BACKEND / "app" / "services" / "drive" / "relay.py").read_text(encoding="utf-8")
    assert "secret ==" not in source and "== current.secret" not in source


def test_secret_never_appears_in_logs_or_responses(start, caplog):
    caplog.set_level(logging.DEBUG)
    env = start()
    _stage_inputs(env)
    texts = [env.get("/health").text,
             env.client.get(API + "/health", headers={"X-SCX-Relay-Key": SECRET + "x"}).text,
             env.post("/jobs/radioss", json=_submit_body()).text,
             env.post("/mkdirs", json={"request_id": "m1", "path": f"{SPDM}/x"}).text]
    assert repr(relay.settings()).count(SECRET) == 0
    for text in [*texts, caplog.text]:
        assert SECRET not in text


@pytest.mark.parametrize("content", ["short", "has space " + "a" * 50, "a" * 129, "ä" * 50, ""])
def test_bad_secret_file_refuses_startup_without_the_value(start, tmp_path, monkeypatch, content):
    start()
    _secret_file(tmp_path, content)
    relay.shutdown()
    with pytest.raises(drive_gateway.DriveStartupError) as raised:
        relay.startup()
    assert "SIMDASH_SCX_RELAY_SECRET_FILE" in str(raised.value)
    if content:
        assert content not in str(raised.value)
    monkeypatch.setenv("SIMDASH_SCX_RELAY_SECRET_FILE", str(tmp_path / "missing.key"))
    with pytest.raises(drive_gateway.DriveStartupError, match="SIMDASH_SCX_RELAY_SECRET_FILE"):
        relay.startup()
    monkeypatch.setenv("SIMDASH_SCX_RELAY_SECRET_FILE", "relative/scx_relay.key")
    with pytest.raises(drive_gateway.DriveStartupError, match="SIMDASH_SCX_RELAY_SECRET_FILE"):
        relay.startup()


def test_secret_file_with_bom_and_crlf_is_accepted(start, tmp_path):
    start()
    path = _secret_file(tmp_path)
    path.write_bytes(b"\xef\xbb\xbf  " + SECRET.encode() + b"\r\n")
    relay.shutdown()
    assert relay.startup().secret == SECRET.encode()


@pytest.mark.parametrize("base", [SPDM, f"{SPDM}/pai", "spdm/projects/x"])
def test_relay_base_inside_the_spdm_root_refuses_startup(start, monkeypatch, base):
    start()
    monkeypatch.setenv("SIMDASH_SCX_RELAY_DRIVE_BASE", base)
    relay.shutdown()
    with pytest.raises(drive_gateway.DriveStartupError, match="SIMDASH_SCX_RELAY_DRIVE_BASE"):
        relay.startup()


# --- paths (§4) -----------------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["cushion/../x", "/cushion/x", "~/cushion/x", "cushion\\x", "cushion/\x01x", "cushion//x",
                                  "cushion/./x", "SPDM /Projects/x", "SPDM/Projects /x", "SPDM./Projects/x",
                                  "cushion /x", " cushion/x", "cushion/x.", "cushion/\u200bx", "cushion/x\ufeff",
                                  "cushion/" + "a" * 256, "cushion/" + "/".join(["d" * 200] * 6)])
def test_invalid_path_forms_are_400(start, path):
    env = start()
    response = env.post("/mkdirs", json={"request_id": "m-bad", "path": path})
    assert response.status_code == 400 and _code(response) == "INVALID_PATH", (path, response.text)


@pytest.mark.parametrize("path", [SPDM, f"{SPDM}/job", "spdm/PROJECTS/job", ".hidden/job"])
def test_spdm_root_and_hidden_folders_are_never_written(start, path):
    env = start()
    for response in (env.post("/mkdirs", json={"request_id": "m-spdm", "path": path}),
                     env.upload(path, "a.rad", b"x", request_id="u-spdm")):
        assert response.status_code == 403 and _code(response) == "PATH_NOT_ALLOWED", (path, response.text)
    assert env.drive.writes() == []
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM drive_upload_queue").fetchone()[0] == 0


def test_hidden_folders_are_not_read_either(start):
    env = start()
    for path in (".hidden", ".hidden/job", f"{SPDM}/../x"):
        response = env.get("/list", params={"path": path})
        assert response.status_code in (400, 403), (path, response.text)
    assert env.get("/files", params={"path": ".hidden/a.csv"}).status_code == 403
    assert env.get("/files", params={"path": "/abs/a.csv"}).status_code == 400


def test_spdm_root_is_readable_but_never_written_or_submitted(start):
    env = start()
    data = b"node,val\n1,2\n"
    env.drive.add_file(f"{SPDM}/R95J/WR-1/out/result.csv", data)
    for path in (SPDM, f"{SPDM}/R95J", f"{SPDM}/R95J/WR-1/out"):
        listing = env.get("/list", params={"path": path})
        assert listing.status_code == 200, (path, listing.text)
    assert [e["name"] for e in env.get("/list", params={"path": f"{SPDM}/R95J/WR-1/out"}).json()["entries"]] == ["result.csv"]
    download = env.get("/files", params={"path": f"{SPDM}/R95J/WR-1/out/result.csv"})
    assert download.status_code == 200 and download.content == data
    assert download.headers["x-content-sha256"] == hashlib.sha256(data).hexdigest()
    assert env.get("/files", params={"path": f"{SPDM}/missing.csv"}).status_code == 404
    for path in (SPDM, f"{SPDM}/R95J/WR-1/out"):
        for response in (env.post("/mkdirs", json={"request_id": "m-r", "path": f"{path}/new"}),
                         env.upload(path, "a.rad", b"x", request_id="u-r")):
            assert response.status_code == 403 and _code(response) == "PATH_NOT_ALLOWED", (path, response.text)
    _stage_inputs(env)
    inside = f"{SPDM}/R95J/WR-1/a.rad"
    for change in ({"input_path": inside}, {"engine_paths": [inside]}, {"additional_paths": [inside]}):
        response = env.post("/jobs/radioss", json=_submit_body(request_id="s-spdm", **change))
        assert response.status_code == 403 and _code(response) == "PATH_NOT_ALLOWED", (change, response.text)
    assert relay.output_rel(f"~/{SPDM}/R95J/WR-1/out") is None
    assert relay.output_rel(f"{SPDM}/x") is None
    assert env.drive.writes() == [] and _submits(env) == []


def test_spdm_root_outside_the_relay_base_stays_unreadable(start):
    env = start(SIMDASH_SCX_RELAY_DRIVE_BASE="cushion")
    env.drive.add_file(f"{SPDM}/a.csv", b"x")
    for response in (env.get("/list", params={"path": SPDM}), env.get("/files", params={"path": f"{SPDM}/a.csv"})):
        assert response.status_code == 403 and _code(response) == "PATH_NOT_ALLOWED", response.text


def test_queue_refuses_a_relay_item_inside_the_spdm_root(start):
    start()
    with pytest.raises(SpdmStorageError):
        relay.queue_drive_path(f"{SPDM}/x")
    with pytest.raises(SpdmStorageError):
        relay.queue_drive_path("")
    assert relay.queue_drive_path("cushion/x") == "cushion/x"


def test_paths_outside_the_relay_base_are_forbidden(start):
    env = start(SIMDASH_SCX_RELAY_DRIVE_BASE="pai")
    env.drive.add_dir("pai")
    env.drive.add_dir("other")
    assert env.post("/mkdirs", json={"request_id": "m-out", "path": "other/job"}).status_code == 403
    assert env.get("/list", params={"path": "other"}).status_code == 403
    assert env.get("/list", params={"path": "pai"}).status_code == 200
    created = env.post("/mkdirs", json={"request_id": "m-in", "path": "pai/cushion"})
    assert created.status_code == 202, created.text
    with connect() as conn:
        item = upload_queue.batch_items(conn, created.json()["batch_id"])[0]
    assert item.dst_rel_dir == "cushion" and item.root_key == relay.root_key()     # relative to the relay base
    _settle()
    assert ("mkdirs", "pai/cushion") in env.drive.writes()
    assert created.json()["items"][0]["target"] == "pai/cushion"


def test_upload_needs_a_job_folder_two_levels_below_the_base(start):
    env = start()
    response = env.upload("cushion", "a.rad", b"x", request_id="u-depth")
    assert response.status_code == 403 and _code(response) == "PATH_NOT_ALLOWED"
    assert env.upload(JOB_DIR, "a.rad", b"x", request_id="u-depth2").status_code == 202


# --- uploads (§5.2–5.4) -----------------------------------------------------------------------------

def test_upload_is_one_queue_batch_and_the_worker_writes_it(start):
    env = start()
    data = b"#RADIOSS STARTER\n" * 100
    response = env.upload(JOB_DIR, "cushion_0000.rad", data, request_id="pai-7:f:cushion_0000.rad")
    assert response.status_code == 202, response.text
    batch = response.json()
    assert batch["state"] == "QUEUED" and [item["kind"] for item in batch["items"]] == ["MKDIR", "FILE"]
    assert env.drive.writes() == []                     # nothing on the drive before the worker runs
    with connect() as conn:
        items = upload_queue.batch_items(conn, batch["batch_id"])
    assert {(item.origin, item.root_key, item.request_id, item.requested_by) for item in items} == {
        (upload_queue.RELAY_ORIGIN, relay.root_key(), "pai-7:f:cushion_0000.rad", "physicsai")}
    assert items[1].sha256 == hashlib.sha256(data).hexdigest() and items[1].size_bytes == len(data)
    staging = upload_queue.staging_dir_for(batch["batch_id"])
    assert (staging / "cushion_0000.rad").read_bytes() == data
    assert _settle() == 2
    assert env.drive.content(STARTER) == data
    done = env.get(f"/batches/{batch['batch_id']}").json()
    assert (done["state"], done["finished"], done["items"][1]["target"]) == ("DONE", True, STARTER)
    assert not staging.exists()
    assert [op for op, _ in env.drive.writes()] == ["mkdirs", "upload_new"]


def test_hash_or_size_mismatch_is_400_and_leaves_no_staging(start):
    env = start()
    response = env.upload(JOB_DIR, "a.rad", b"abc", request_id="u-hash", digest="0" * 64)
    assert response.status_code == 400 and _code(response) == "HASH_MISMATCH" and response.json()["retryable"]
    staging_root = drive_gateway.current_settings().staging_dir
    assert not [path for path in staging_root.glob("upload-*") if any(path.iterdir())]
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM drive_upload_queue").fetchone()[0] == 0
    bad = env.upload(JOB_DIR, "a.rad", b"abc", request_id="u-hash", digest="ABC")
    assert bad.status_code == 422


def test_upload_limits_staging_room_and_length(start, monkeypatch):
    env = start(SIMDASH_SCX_RELAY_MAX_UPLOAD_BYTES=str(1024 * 1024))
    big = b"0" * (1024 * 1024 + 1)
    response = env.upload(JOB_DIR, "big.rad", big, request_id="u-big")
    assert response.status_code == 413 and _code(response) == "TOO_LARGE"
    monkeypatch.setattr(server_local, "disk_free", lambda _path: 10)
    full = env.upload(JOB_DIR, "a.rad", b"x", request_id="u-full")
    assert full.status_code == 503 and _code(full) == "DRIVE_STAGING_FULL" and full.json()["retryable"]
    chunked = env.client.post(API + "/files", params={"dir": JOB_DIR, "name": "c.rad"}, content=iter([b"a", b"b"]),
                              headers={**HEADERS, "X-Request-Id": "u-len", "X-Content-SHA256": "0" * 64})
    assert chunked.status_code == 411 and _code(chunked) == "LENGTH_REQUIRED"
    bad_name = env.upload(JOB_DIR, "a:b.rad", b"x", request_id="u-name")
    assert bad_name.status_code == 400 and _code(bad_name) == "INVALID_PATH"


def test_same_request_id_replays_the_batch_and_different_content_is_409(start):
    env = start()
    first = env.upload(JOB_DIR, "a.rad", b"one", request_id="u-same")
    again = env.upload(JOB_DIR, "a.rad", b"one", request_id="u-same")
    assert first.status_code == 202 and again.status_code == 200
    assert again.json()["batch_id"] == first.json()["batch_id"]
    other = env.upload(JOB_DIR, "a.rad", b"two", request_id="u-same")
    assert other.status_code == 409 and _code(other) == "REQUEST_ID_REUSED"
    with connect() as conn:
        assert conn.execute("SELECT count(DISTINCT batch_id) FROM drive_upload_queue WHERE request_id='u-same'").fetchone()[0] == 1


def test_existing_same_content_is_done_and_different_content_is_conflict_without_overwrite(start):
    env = start()
    env.drive.add_file(f"{JOB_DIR}/same.inc", b"same")
    env.drive.add_file(f"{JOB_DIR}/other.inc", b"someone else")
    same = env.upload(JOB_DIR, "same.inc", b"same", request_id="u-s").json()
    other = env.upload(JOB_DIR, "other.inc", b"mine", request_id="u-o").json()
    _settle()
    assert env.get(f"/batches/{same['batch_id']}").json()["state"] == "DONE"
    conflict = env.get(f"/batches/{other['batch_id']}").json()
    assert conflict["state"] == "CONFLICT" and conflict["items"][1]["error_code"] == "SPDM_CONFLICT"
    assert env.drive.content(f"{JOB_DIR}/other.inc") == b"someone else"


def test_auth_required_pauses_the_batch(start):
    env = start()
    env.drive.fail_always["mkdirs"] = ErrorCode.AUTH_REQUIRED
    batch = env.upload(JOB_DIR, "a.rad", b"x", request_id="u-auth").json()
    _settle()
    paused = env.get(f"/batches/{batch['batch_id']}").json()
    assert paused["state"] == "PAUSED" and paused["paused"] and not paused["finished"]
    assert env.get("/health").json()["queue_paused"] is True


def test_writes_disabled_is_409_but_reads_and_jobs_still_answer(start):
    env = start(SIMDASH_DRIVE_WRITES_ENABLED="false")
    _stage_inputs(env)
    for response in (env.post("/mkdirs", json={"request_id": "m-off", "path": JOB_DIR}),
                     env.upload(JOB_DIR, "a.rad", b"x", request_id="u-off")):
        assert response.status_code == 409 and _code(response) == "DRIVE_WRITE_DISABLED"
    assert env.get("/list", params={"path": JOB_DIR}).status_code == 200
    assert env.post("/jobs/radioss", json=_submit_body()).status_code == 201
    assert env.get("/health").json()["writes_active"] is False


def test_mkdirs_is_idempotent_and_an_existing_folder_is_done(start):
    env = start()
    env.drive.add_dir(JOB_DIR)
    first = env.post("/mkdirs", json={"request_id": "pai-9:mkdir", "path": JOB_DIR})
    again = env.post("/mkdirs", json={"request_id": "pai-9:mkdir", "path": JOB_DIR})
    assert first.status_code == 202 and again.status_code == 200 and first.json()["batch_id"] == again.json()["batch_id"]
    other = env.post("/mkdirs", json={"request_id": "pai-9:mkdir", "path": f"{JOB_DIR}_2"})
    assert other.status_code == 409 and _code(other) == "REQUEST_ID_REUSED"
    _settle()
    assert env.get(f"/batches/{first.json()['batch_id']}").json()["state"] == "DONE"
    assert env.post("/mkdirs", json={"request_id": "m", "path": JOB_DIR, "extra": 1}).status_code == 422


def test_batch_endpoint_shows_only_relay_batches(start):
    env = start()
    with connect() as conn:
        foreign = upload_queue.enqueue(conn, origin="result_drop", requested_by="u", items=[{"kind": "MKDIR",
                                                                                            "dst_rel_dir": "x"}])
    assert env.get(f"/batches/{foreign}").status_code == 404
    assert env.get("/batches/nope").status_code == 404


# --- reads (§5.5–5.6) ---------------------------------------------------------------------------------

def test_list_inside_the_base(start):
    env = start()
    _stage_inputs(env)
    listing = env.get("/list", params={"path": JOB_DIR})
    assert listing.status_code == 200
    body = listing.json()
    assert body["path"] == JOB_DIR and {entry["name"] for entry in body["entries"]} == {
        "cushion_0000.rad", "cushion_0001.rad", "part_1.inc"}
    assert all(entry["kind"] == "file" and entry["sha1"] and entry["modified_at"].endswith("Z") for entry in body["entries"])
    assert env.get("/list", params={"path": "missing"}).status_code == 404
    assert env.get("/list", params={"path": JOB_DIR, "max_entries": "0"}).status_code == 422
    assert env.get("/list", params={"path": JOB_DIR, "max_entries": "1"}).status_code == 413


def test_download_streams_with_sha256_and_removes_its_staging(start):
    env = start()
    data = b"h3d" * 1000
    env.drive.add_file(f"{JOB_DIR}/out/result.h3d", data)
    response = env.get("/files", params={"path": f"{JOB_DIR}/out/result.h3d"})
    assert response.status_code == 200 and response.content == data
    assert response.headers["x-content-sha256"] == hashlib.sha256(data).hexdigest()
    assert int(response.headers["content-length"]) == len(data) and response.headers["x-drive-version"]
    assert response.headers["cache-control"] == "no-store"
    staging = drive_gateway.current_settings().staging_dir
    assert list(staging.glob(relay.DOWNLOAD_PREFIX + "*")) == []
    small = env.get("/files", params={"path": f"{JOB_DIR}/out/result.h3d", "max_bytes": "10"})
    assert small.status_code == 413 and _code(small) == "TOO_LARGE"
    assert env.get("/files", params={"path": f"{JOB_DIR}/out"}).status_code == 404
    assert env.get("/files", params={"path": f"{SPDM}/a.csv"}).status_code == 404  # SPDM read allowed, file missing


def test_interrupted_download_still_removes_its_staging(start):
    env = start()
    env.drive.add_file(f"{JOB_DIR}/r.h3d", b"x" * 100)
    download = relay.prepare_download({"path": f"{JOB_DIR}/r.h3d"})
    assert download.work_dir.exists()
    response = relay_router._DownloadResponse(download)

    async def receive():
        return {"type": "http.disconnect"}

    async def send(_message):
        raise OSError("client went away")

    scope = {"type": "http", "method": "GET", "headers": [], "path": "/", "query_string": b""}
    with pytest.raises(OSError):
        asyncio.run(response(scope, receive, send))
    assert not download.work_dir.exists()
    assert env.drive.downloads()


def test_reads_outside_the_base_are_allowed_only_below_a_recorded_output(start):
    env = start(SIMDASH_SCX_RELAY_DRIVE_BASE="pai")
    env.drive.add_file("scratch/run1/out/a.h3d", b"a")
    env.drive.add_file("scratch/run2/out/b.h3d", b"b")
    assert env.get("/list", params={"path": "scratch/run1/out"}).status_code == 403
    now = relay._now()
    with connect() as conn:
        conn.execute("INSERT INTO scx_relay_submissions (request_id,request_sha256,client,input_rel,job_dir_rel,state,job_id,"
                     "output_rel,created_at,updated_at) VALUES ('r-out','0','physicsai','pai/c/j/a.rad','pai/c/j','SUBMITTED',"
                     "'job-out','scratch/run1',?,?)", [now, now])
    assert env.get("/list", params={"path": "scratch/run1/out"}).status_code == 200
    assert env.get("/files", params={"path": "scratch/run1/out/a.h3d"}).content == b"a"
    assert env.get("/list", params={"path": "scratch/run2/out"}).status_code == 403


# --- jobs (§5.7–5.10) -------------------------------------------------------------------------------

def test_adapter_without_job_api_is_501_and_drive_endpoints_still_work(start):
    env = start(jobs=False)
    _stage_inputs(env)
    health = env.get("/health").json()
    assert health["job_api"] is False and health["adapter_version"] == "0.2.0-fake"
    for response in (env.post("/jobs/radioss", json=_submit_body()), env.get("/jobs/job-1"),
                     env.post("/jobs/job-1/terminate")):
        assert response.status_code == 501 and _code(response) == "JOB_API_UNAVAILABLE", response.text
    assert env.get("/list", params={"path": JOB_DIR}).status_code == 200
    assert env.post("/mkdirs", json={"request_id": "m-old", "path": JOB_DIR}).status_code == 202
    assert _row("pai-1") is None


def test_health_reports_the_relay_and_never_builds_the_gateway(start):
    env = start(register=False)
    health = env.get("/health")
    assert health.status_code == 200 and health.headers["cache-control"] == "no-store"
    body = health.json()
    assert drive_gateway.existing_gateway() is None
    assert body == {"relay_version": 1, "drive_mode": "scx", "writes_active": True, "job_api": True,
                    "adapter_version": "0.3.0-fake", "drive": None, "queue_paused": False, "relay_base": "",
                    "forbidden_prefixes": [SPDM], "spdm_read": True, "home_display": "/Users/vd.dashboard", "job_path_form": "rel",
                    "max_upload_bytes": 4294967296, "max_download_bytes": 21474836480}


def test_submit_records_and_replays_without_a_second_adapter_call(start):
    env = start()
    _stage_inputs(env)
    env.drive.job_output_path = "~/{dir}/output"
    created = env.post("/jobs/radioss", json=_submit_body())
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["state"], body["replayed"], body["job"]["status"]) == ("SUBMITTED", False, "Queued")
    assert body["job"]["output_rel"] == f"{JOB_DIR}/output" and body["job"]["output_path"] == f"~/{JOB_DIR}/output"
    [sent] = _submits(env)
    assert (sent.input_path, sent.engine_paths, sent.additional_paths) == (STARTER, (ENGINE,), (INCLUDE,))
    assert (sent.resources.appliance, sent.resources.ncpu, sent.resources.memory_gb, sent.version) == (
        "radioss-appliance", 16, None, "2026.1")
    replay = env.post("/jobs/radioss", json=_submit_body())
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert replay.json()["job"]["job_id"] == body["job"]["job_id"]
    assert len(_submits(env)) == 1
    reused = env.post("/jobs/radioss", json=_submit_body(version="2026.2"))
    assert reused.status_code == 409 and _code(reused) == "REQUEST_ID_REUSED"
    assert len(_submits(env)) == 1
    assert _row("pai-1")["state"] == "SUBMITTED"


def test_submission_unknown_is_recorded_and_replay_never_calls_the_adapter(start):
    env = start()
    _stage_inputs(env)
    env.drive.job_fail_next["submit_radioss"] = JobErrorCode.SUBMISSION_UNKNOWN
    response = env.post("/jobs/radioss", json=_submit_body("pai-u"))
    assert response.status_code == 409 and _code(response) == "SUBMISSION_UNKNOWN"
    assert response.json()["retryable"] is False and response.json()["adapter_code"] == "SUBMISSION_UNKNOWN"
    assert _row("pai-u")["state"] == "UNKNOWN" and _row("pai-u")["job_id"] is None
    replay = env.post("/jobs/radioss", json=_submit_body("pai-u"))
    assert replay.status_code == 409 and _code(replay) == "SUBMISSION_UNKNOWN"
    assert len(_submits(env)) == 1


def test_code_less_adapter_failure_is_unknown(start):
    env = start()
    _stage_inputs(env)
    env.drive.job_fail_next["submit_radioss"] = RuntimeError("worker died")
    response = env.post("/jobs/radioss", json=_submit_body("pai-x"))
    assert response.status_code == 409 and _code(response) == "SUBMISSION_UNKNOWN"
    assert _row("pai-x")["state"] == "UNKNOWN"


def test_submission_in_progress_is_409_retryable(start):
    env = start()
    _stage_inputs(env)
    digest = relay.parse_submit(_submit_body("pai-p")).digest()
    now = relay._now()
    with connect() as conn:
        conn.execute("INSERT INTO scx_relay_submissions (request_id,request_sha256,client,input_rel,job_dir_rel,state,"
                     "created_at,updated_at) VALUES ('pai-p',?,'physicsai',?,?,'SUBMITTING',?,?)",
                     [digest, STARTER, JOB_DIR, now, now])
    response = env.post("/jobs/radioss", json=_submit_body("pai-p"))
    assert response.status_code == 409 and _code(response) == "SUBMISSION_IN_PROGRESS" and response.json()["retryable"]
    assert _submits(env) == []


def test_startup_turns_submitting_into_unknown(start):
    start()
    now = relay._now()
    with connect() as conn:
        conn.execute("INSERT INTO scx_relay_submissions (request_id,request_sha256,client,input_rel,job_dir_rel,state,"
                     "created_at,updated_at) VALUES ('pai-s','0','physicsai',?,?,'SUBMITTING',?,?)", [STARTER, JOB_DIR, now, now])
    relay.shutdown()
    relay.startup()
    row = _row("pai-s")
    assert row["state"] == "UNKNOWN" and row["error_code"] == "SUBMISSION_UNKNOWN"


@pytest.mark.parametrize("code, status, relay_code, retryable", [
    (ErrorCode.AUTH_REQUIRED, 503, "DRIVE_AUTH_REQUIRED", False),
    (ErrorCode.BUSY, 503, "DRIVE_BUSY", True),
    (ErrorCode.TIMEOUT, 504, "DRIVE_TIMEOUT", True),
    (ErrorCode.FORBIDDEN, 403, "FORBIDDEN", False),
    (ErrorCode.LOCKED, 423, "LOCKED", True),
    (JobErrorCode.JOB_REJECTED, 502, "SCX_JOB_ERROR", False),
])
def test_defined_submit_errors_are_failed_and_replayed_as_recorded(start, code, status, relay_code, retryable):
    env = start()
    _stage_inputs(env)
    env.drive.job_fail_next["submit_radioss"] = code
    response = env.post("/jobs/radioss", json=_submit_body("pai-e"))
    assert response.status_code == status and _code(response) == relay_code, response.text
    assert response.json()["retryable"] is retryable and response.json()["adapter_code"] == code.value
    assert _row("pai-e")["state"] == "FAILED" and _row("pai-e")["error_code"] == code.value
    replay = env.post("/jobs/radioss", json=_submit_body("pai-e"))
    assert replay.status_code == status and _code(replay) == relay_code
    assert len(_submits(env)) == 1           # never retried, never re-sent


def test_busy_answer_carries_retry_after(start):
    env = start()
    _stage_inputs(env)
    env.drive.job_fail_next["submit_radioss"] = ErrorCode.OVERLOADED
    response = env.post("/jobs/radioss", json=_submit_body("pai-b"))
    assert response.status_code == 503 and response.json()["retry_after"] == 5 and response.headers["retry-after"] == "5"


@pytest.mark.parametrize("form, expected", [("rel", STARTER), ("tilde", f"~/{STARTER}"),
                                            ("absolute", f"/Users/vd.dashboard/{STARTER}")])
def test_job_path_forms(start, form, expected):
    env = start(SIMDASH_SCX_RELAY_JOB_PATH_FORM=form)
    _stage_inputs(env)
    assert env.post("/jobs/radioss", json=_submit_body()).status_code == 201
    [sent] = _submits(env)
    assert sent.input_path == expected and sent.engine_paths == (expected.replace("_0000", "_0001"),)


def test_output_rel_conversion(start):
    start(SIMDASH_SCX_RELAY_HOME_ABS="/Users/vd.dashboard/")
    assert relay.output_rel("~/cushion/j/out") == "cushion/j/out"
    assert relay.output_rel("/Users/vd.dashboard/cushion/j/out/") == "cushion/j/out"
    assert relay.output_rel("cushion/j/out") == "cushion/j/out"
    for value in ("/srv/scratch/j", "C:\\work\\j", "~", "~other/x", f"~/{SPDM}/x", "~/a/../b", None, ""):
        assert relay.output_rel(value) is None, value


def test_input_missing_is_404_before_any_submit(start):
    env = start()
    response = env.post("/jobs/radioss", json=_submit_body("pai-m"))
    assert response.status_code == 404 and _code(response) == "NOT_FOUND"
    assert _submits(env) == [] and _row("pai-m") is None


@pytest.mark.parametrize("changes", [
    {"engine_paths": ["cushion/other/cushion_0001.rad"]},
    {"additional_paths": ["cushion/x.inc"]},
    {"resources": {"appliance": "a", "ncpu": 0}},
    {"resources": {"appliance": "a", "ncpu": 4097}},
    {"resources": {"appliance": "a", "ncpu": True}},
    {"resources": {"appliance": "a", "memory_gb": 0}},
    {"version": ""},
    {"version": "x" * 129},
    {"mpi": "a\nb"},
    {"request_id": "bad id"},
    {"unexpected": 1},
    {"single_precision": "no"},
])
def test_invalid_submit_requests_are_422(start, changes):
    env = start()
    _stage_inputs(env)
    response = env.post("/jobs/radioss", json=_submit_body(**changes))
    assert response.status_code == 422 and _code(response) == "INVALID_REQUEST", response.text
    assert _submits(env) == []


def test_submit_paths_follow_the_path_rules(start):
    env = start()
    assert _code(env.post("/jobs/radioss", json=_submit_body(input_path=f"{SPDM}/j/a.rad"))) == "PATH_NOT_ALLOWED"
    assert _code(env.post("/jobs/radioss", json=_submit_body(input_path="cushion/a.rad", engine_paths=[],
                                                               additional_paths=[]))) == "PATH_NOT_ALLOWED"
    assert _code(env.post("/jobs/radioss", json=_submit_body(input_path="../a/b.rad"))) == "INVALID_PATH"
    assert env.post("/jobs/radioss", content=b"{not json", headers={"Content-Type": "application/json"}).status_code == 422


def test_get_job_and_terminate_only_for_recorded_jobs(start):
    env = start()
    _stage_inputs(env)
    assert _code(env.get("/jobs/job-404")) == "JOB_NOT_FOUND"
    assert _code(env.post("/jobs/job-404/terminate")) == "JOB_NOT_FOUND"
    assert env.drive.job_calls == []
    job_id = env.post("/jobs/radioss", json=_submit_body()).json()["job"]["job_id"]
    env.drive.job_output_path = "/Users/vd.dashboard/{dir}/out"
    env.drive.jobs[job_id]["output_path"] = f"/Users/vd.dashboard/{JOB_DIR}/out"
    status = env.get(f"/jobs/{job_id}")
    assert status.status_code == 200 and status.json()["status"] == "Running"
    assert status.json()["output_rel"] == f"{JOB_DIR}/out"
    assert _row("pai-1")["last_status"] == "Running" and _row("pai-1")["output_rel"] == f"{JOB_DIR}/out"
    stopped = env.post(f"/jobs/{job_id}/terminate")
    assert stopped.status_code == 200 and stopped.json() == {"job_id": job_id, "requested": True}
    assert env.get(f"/jobs/{job_id}").json()["status"] == "Aborted"
    del env.drive.jobs[job_id]
    assert _code(env.get(f"/jobs/{job_id}")) == "SCX_JOB_NOT_FOUND"
    assert _code(env.post(f"/jobs/{job_id}/terminate")) == "SCX_JOB_NOT_FOUND"
    assert [op for op, _ in env.drive.job_calls].count("terminate_job") == 2


def test_resolve_attach_and_abandon(start):
    env = start()
    _stage_inputs(env)
    env.drive.job_fail_next["submit_radioss"] = JobErrorCode.SUBMISSION_UNKNOWN
    env.post("/jobs/radioss", json=_submit_body("pai-r1"))
    [lost_job] = list(env.drive.jobs)                       # the server did accept it
    env.drive.job_fail_next["submit_radioss"] = JobErrorCode.SUBMISSION_UNKNOWN
    env.post("/jobs/radioss", json=_submit_body("pai-r2"))
    ok = env.post("/jobs/radioss", json=_submit_body("pai-ok")).json()["job"]["job_id"]
    assert _code(env.post("/submissions/pai-ok/resolve", json={"action": "abandon"})) == "NOT_UNKNOWN"
    assert env.post("/submissions/pai-none/resolve", json={"action": "abandon"}).status_code == 404
    assert _code(env.post("/submissions/pai-r1/resolve", json={"action": "attach", "job_id": "job-nope"})) == "SCX_JOB_NOT_FOUND"
    assert _code(env.post("/submissions/pai-r1/resolve", json={"action": "attach", "job_id": ok})) == "JOB_ID_IN_USE"
    assert _code(env.post("/submissions/pai-r1/resolve", json={"action": "delete"})) == "INVALID_REQUEST"
    attached = env.post("/submissions/pai-r1/resolve", json={"action": "attach", "job_id": lost_job})
    assert attached.status_code == 200 and attached.json()["state"] == "ATTACHED"
    assert _row("pai-r1")["job_id"] == lost_job
    assert env.get(f"/jobs/{lost_job}").status_code == 200
    replay = env.post("/jobs/radioss", json=_submit_body("pai-r1"))
    assert replay.status_code == 200 and replay.json()["state"] == "ATTACHED"
    abandoned = env.post("/submissions/pai-r2/resolve", json={"action": "abandon"})
    assert abandoned.status_code == 200 and abandoned.json() == {"request_id": "pai-r2", "state": "ABANDONED"}
    assert _code(env.post("/jobs/radioss", json=_submit_body("pai-r2"))) == "SUBMISSION_ABANDONED"
    assert _code(env.post("/submissions/pai-r2/resolve", json={"action": "abandon"})) == "NOT_UNKNOWN"
    assert len(_submits(env)) == 3


def test_error_map_covers_the_contract_table():
    expected = {"NOT_FOUND": (404, "NOT_FOUND"), "FORBIDDEN": (403, "FORBIDDEN"), "CONFLICT": (409, "CONFLICT"),
                "LIMIT": (413, "LIMIT"), "LOCKED": (423, "LOCKED"), "BUSY": (503, "DRIVE_BUSY"),
                "OVERLOADED": (503, "DRIVE_BUSY"), "RATE_LIMITED": (503, "DRIVE_BUSY"),
                "AUTH_REQUIRED": (503, "DRIVE_AUTH_REQUIRED"), "AUTH_REFRESH_EXPIRED": (503, "DRIVE_AUTH_REQUIRED"),
                "TIMEOUT": (504, "DRIVE_TIMEOUT"), "UNAVAILABLE": (503, "DRIVE_UNAVAILABLE"),
                "INTERNAL": (500, "DRIVE_INTERNAL"), "SUBMISSION_UNKNOWN": (409, "SUBMISSION_UNKNOWN")}
    for code, (status, relay_code) in expected.items():
        error = relay._mapped(code)
        assert (error.status, error.code, error.adapter_code) == (status, relay_code, code), code
    assert relay._mapped("NOT_FOUND", job_lookup=True).code == "SCX_JOB_NOT_FOUND"
    assert relay._mapped("SOMETHING_NEW", job=True).status == 502
    assert relay._mapped("Bearer abc.def", "token Bearer secret-value").message == "token Bearer ***"


# --- static and schema --------------------------------------------------------------------------------

def _called_names(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            func = node.func
            if [keyword.arg for keyword in node.keywords] == ["tzinfo"]:
                continue                         # datetime.replace(tzinfo=...) is not a drive operation
            names.add(func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else "")
    return names


def test_relay_never_calls_drive_write_delete_or_move_methods():
    forbidden = {"upload_new", "mkdirs", "copy_within", "rm", "mv", "mv_to_trash", "move", "rename", "replace", "remove",
                 "delete", "unlink", "rmdir", "touch", "set_metadata", "update_type"}
    for path in (BACKEND / "app" / "services" / "drive" / "relay.py", BACKEND / "app" / "routers" / "scx_relay.py"):
        assert not (_called_names(path) & forbidden), path


def test_internal_prefix_is_not_in_any_proxy_table():
    assert relay.PREFIX.startswith("/internal/") and not relay.PREFIX.startswith("/api")
    for path in (REPO / "frontend" / "vite.proxy.ts", REPO / "frontend" / "vite.config.ts",
                 REPO / "deploy" / "windows" / "Caddyfile.intranet.template",
                 REPO / "deploy" / "windows" / "offline" / "install.ps1",
                 *sorted((REPO / "deploy" / "rocky8" / "nginx").glob("*.template"))):
        assert "/internal" not in path.read_text(encoding="utf-8"), path
    assert not any(route.path.startswith("/internal") for route in app.routes if getattr(route, "include_in_schema", False))


def test_migration_0044_is_additive_and_matches_the_startup_check():
    text = (BACKEND / "migrations" / "versions" / "0044_scx_relay_submissions.py").read_text(encoding="utf-8")
    upgrade = text.split("def downgrade")[0]
    assert "CREATE TABLE IF NOT EXISTS scx_relay_submissions" in upgrade
    assert 'down_revision = "0043_password_change_required"' in text
    for forbidden in ("DROP TABLE", "DELETE FROM", "TRUNCATE", "ALTER TABLE"):
        assert forbidden not in upgrade
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON {table}" in upgrade
    source = (BACKEND / "app" / "database.py").read_text(encoding="utf-8")
    assert '"scx_relay_submissions",' in source and '"scx_relay_submissions": {"request_id"' in source


def test_duckdb_bootstrap_adds_the_relay_table_to_an_existing_database():
    from app.config import database_settings
    from app.database import initialize_database

    if database_settings().backend != "duckdb":
        pytest.skip("DuckDB bootstrap only")
    with connect() as conn:
        conn.execute("DROP TABLE scx_relay_submissions")
    initialize_database()
    now = relay._now()
    with connect() as conn:
        for index in range(2):      # NULL job ids never collide
            conn.execute("INSERT INTO scx_relay_submissions (request_id,request_sha256,client,input_rel,job_dir_rel,state,"
                         "created_at,updated_at) VALUES (?,'0','c','a/b/c','a/b','UNKNOWN',?,?)", [f"r{index}", now, now])
        with pytest.raises(Exception):
            conn.execute("INSERT INTO scx_relay_submissions (request_id,request_sha256,client,input_rel,job_dir_rel,state,"
                         "created_at,updated_at) VALUES ('r9','0','c','a/b/c','a/b','DELETED',?,?)", [now, now])


def test_contract_copy_is_linked_from_the_drive_feature_doc():
    copy = REPO / "docs" / "features" / "scx-relay.md"
    assert copy.exists() and "/internal/scx-relay/v1" in copy.read_text(encoding="utf-8")
    assert "scx-relay.md" in (REPO / "docs" / "features" / "scx-drive.md").read_text(encoding="utf-8")


def test_relay_requests_are_json_with_unique_request_ids():
    assert relay.REQUEST_ID.fullmatch("pai-12:f:cushion_0000.rad")
    assert not relay.REQUEST_ID.fullmatch("pai 12")
    assert json.loads(json.dumps(relay.RelayError(409, "X", "m").body())) == {
        "code": "X", "message": "m", "retryable": False, "retry_after": None, "adapter_code": None}
    assert uuid.UUID(upload_queue.new_batch_id())


def test_relay_items_queued_before_the_relay_was_turned_off_are_not_written(start, monkeypatch):
    env = start()
    batch = env.upload(JOB_DIR, "late.rad", b"late", request_id="u-off-later").json()
    monkeypatch.delenv("SIMDASH_SCX_RELAY_ENABLED")
    relay.shutdown()
    _settle()
    with connect() as conn:
        items = upload_queue.batch_items(conn, batch["batch_id"])
    assert items[0].state == "FAILED" and items[0].last_error_code == "RELAY_DISABLED"
    assert env.drive.writes() == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_secret_file_open_to_others_logs_a_warning(start, tmp_path, caplog):
    start()
    path = _secret_file(tmp_path)
    path.chmod(0o644)
    relay.shutdown()
    with caplog.at_level(logging.WARNING):
        relay.startup()
    assert "SIMDASH_SCX_RELAY_SECRET_FILE is readable by group/others" in caplog.text and SECRET not in caplog.text


def test_submission_and_refusal_leave_a_log_line(start, caplog):
    """2026-10-26: a PhysicsAI Radioss submission must leave what was handed to SCX (or why it was refused)."""
    caplog.set_level(logging.WARNING)   # the server file gets WARNING and above only
    env = start()
    missing = env.post("/jobs/radioss", json=_submit_body("pai-log-m"))
    assert missing.status_code == 404
    assert "SCX relay received: POST" in caplog.text
    assert "SCX relay refused: POST" in caplog.text and "code=NOT_FOUND" in caplog.text
    _stage_inputs(env)
    assert env.post("/jobs/radioss", json=_submit_body("pai-log")).status_code == 201
    line = next(r.getMessage() for r in caplog.records if "pai-log request:" in r.getMessage())
    assert "ncpu=16" in line and "version=2026.1" in line and "appliance=radioss-appliance" in line
    assert "pai-log submitted: job=" in caplog.text
    assert SECRET not in caplog.text
