"""Issue #55 (2026-10-27): 소재물성 is prepared in the background and served from the DB (SCX drive).

Production: opening 소재물성 failed with ``DRIVE_READ_NOT_PREPARED: 드라이브 자료를 준비하지 못했습니다
(조회 횟수 한도)`` because every page view walked and sniffed the Scene deck folders on the drive.  Now the
catalog and deck endpoints read only the DB (``materials_preparations``); the capture worker's materials
pass downloads, parses and stores the decks, and prepares a Scene again when a drive version changes.

In-memory fake drive adapter and the isolated test database only (no real drive, DB or service).
"""
from __future__ import annotations

import pytest

from app.database_connection import connect
from app.services import materials_preparation
from tests.test_drive_reads import OPTION, _register, _sync, scx  # noqa: F401  (fixture)

pytestmark = pytest.mark.duckdb_integration

BASE = "/api/materials"
SCENE = f"{OPTION}/2_Face"


def _cells(*values: object, width: int = 10) -> str:
    return "".join(f"{value:>{width}}" for value in values)


PARTS = ("/PART/1\nSynthetic part\n" + _cells(0, 2, 0) + f"{1.5:>20}" + "\n").encode()
MATERIALS = ("/MAT/ELAST/2\nSynthetic material\n" f"{1.2:>20}\n{1000.0:>20}{0.3:>20}\n"
             "/INCLUDE \"inc/curves.inc\"\n").encode()
CURVES = ("/FUNCT/10\nSynthetic curve\n" f"{0.0:>20}{0.0:>20}\n{1.0:>20}{2.0:>20}\n").encode()


@pytest.fixture(autouse=True)
def _fresh_state():
    materials_preparation.reset_for_tests()
    yield
    materials_preparation.reset_for_tests()


def _decks(scx, materials: bytes = MATERIALS) -> None:
    scx.add(f"{SCENE}/parts.inc", PARTS)
    scx.add(f"{SCENE}/materials.inc", materials)
    scx.add(f"{SCENE}/inc/curves.inc", CURVES)


def _drive_calls(scx) -> int:
    return len(scx.drive.calls)


def _catalog(scx, request_id: str) -> dict:
    response = scx.client.get(BASE + "/catalog", params={"request_id": request_id, "environment": "DISTRIBUTION"})
    assert response.status_code == 200, response.text
    return response.json()


def _scene(catalog: dict, name: str) -> dict:
    return next(item for item in catalog["scenes"] if item["relative_path"].endswith("/" + name))


def _deck(scx, request_id: str, scene_id: str, status: int = 200, **extra) -> dict:
    response = scx.client.get(BASE + "/deck", params={"request_id": request_id, "environment": "DISTRIBUTION",
                                                     "scene_id": scene_id, **extra})
    assert response.status_code == status, response.text
    return response.json()


def _prepare() -> dict:
    result = materials_preparation.run_pending(budget_seconds=120.0, session_seconds=110.0)
    assert result["status"] == "DONE", result
    return result


def _row(request_id: str, name: str) -> dict:
    with connect() as conn:
        found = conn.execute("SELECT status,needs_check,error_code,fingerprint FROM materials_preparations "
                             "WHERE request_id=? AND relative_path LIKE ?", [request_id, f"%/{name}"]).fetchone()
    assert found is not None
    return {"status": found[0], "needs_check": bool(found[1]), "error_code": found[2], "fingerprint": found[3]}


def test_catalog_and_deck_are_served_from_the_db_without_drive_reads(scx):
    _decks(scx)
    _project_id, request_id = _register(scx)

    calls = _drive_calls(scx)
    catalog = _catalog(scx, request_id)
    deck = _deck(scx, request_id, _scene(catalog, "2_Face")["scene_id"])
    assert _drive_calls(scx) == calls, "a page view never reads the drive"
    face = _scene(catalog, "2_Face")
    assert (face["materials_status"], face["has_deck"]) == ("PREPARING", False)
    assert catalog["preparation"]["status"] == "PREPARING"
    assert deck["deck"] is None and deck["analysis"]["status"] == "QUEUED"

    result = _prepare()
    assert result["ready"] >= 1, result
    downloaded = {path.rsplit("/", 1)[-1] for path in scx.drive.downloads()}
    assert {"parts.inc", "materials.inc", "curves.inc"} <= downloaded

    calls = _drive_calls(scx)
    catalog = _catalog(scx, request_id)
    face = _scene(catalog, "2_Face")
    assert (face["materials_status"], face["has_deck"]) == ("READY", True)
    assert (_scene(catalog, "3_Face")["materials_status"], _scene(catalog, "3_Face")["has_deck"]) == ("NO_DECK", False)
    assert catalog["preparation"]["status"] == "READY"
    body = _deck(scx, request_id, face["scene_id"])
    assert _drive_calls(scx) == calls, "a page view never reads the drive"
    assert [part["id"] for part in body["deck"]["parts"]] == ["1"]
    assert [item["id"] for item in body["deck"]["materials"]] == ["2"]
    assert [item["id"] for item in body["deck"]["functions"]] == ["10"]
    assert {item["relative_path"].rsplit("/", 1)[-1] for item in body["files"]} == {"parts.inc", "materials.inc", "curves.inc"}
    missing = _deck(scx, request_id, _scene(catalog, "3_Face")["scene_id"], status=404)
    assert missing["detail"]["code"] == "MATERIALS_DECK_NOT_FOUND"

    # Unchanged drive: a later check lists again, downloads nothing and keeps the prepared deck.
    with connect() as conn:
        conn.execute("UPDATE materials_preparations SET needs_check=true WHERE request_id=?", [request_id])
    downloads = len(scx.drive.downloads())
    assert _prepare()["unchanged"] >= 1
    assert len(scx.drive.downloads()) == downloads
    assert _row(request_id, "2_Face")["needs_check"] is False


def test_request_without_folder_snapshot_is_preparing_not_an_error(scx):
    _decks(scx)
    _project_id, request_id = _register(scx)
    with connect() as conn:
        conn.execute("UPDATE folder_environment_scans SET status='SUPERSEDED_TEST' WHERE request_id=? "
                     "AND id LIKE 'folder-refresh-%'", [request_id])
    calls = _drive_calls(scx)
    catalog = _catalog(scx, request_id)
    assert _drive_calls(scx) == calls
    assert catalog["scenes"] == [] and catalog["preparation"]["status"] == "PREPARING", catalog
    assert catalog["preparation"]["code"] == "MATERIALS_FOLDERS_PREPARING"
    # The worker takes the folder snapshot (listings) and prepares the Scenes.
    _prepare()
    assert _scene(_catalog(scx, request_id), "2_Face")["materials_status"] == "READY"


def test_changed_drive_version_prepares_the_scene_again(scx):
    _decks(scx)
    project_id, request_id = _register(scx)
    _sync(scx, project_id, request_id)
    _catalog(scx, request_id)
    _prepare()
    before = _row(request_id, "2_Face")
    assert before["status"] == "READY"

    changed = MATERIALS.replace(b"/MAT/ELAST/2", b"/MAT/ELAST/7")
    scx.drive.modify_file(scx.path(f"{SCENE}/materials.inc"), changed)
    pending = _sync(scx, project_id, request_id)
    assert pending["drive"]["pending_changes"] == 1, pending
    # Not accepted yet: the dashboard keeps the registered version, so the prepared deck stays as it is.
    _prepare()
    scene_id = _scene(_catalog(scx, request_id), "2_Face")["scene_id"]
    assert [item["id"] for item in _deck(scx, request_id, scene_id)["deck"]["materials"]] == ["2"]

    accepted = scx.post("/api/drive/source-changes/accept", {"project_id": project_id, "request_id": request_id})
    assert accepted["accepted"] == 1
    refreshed = _sync(scx, project_id, request_id)
    assert refreshed["status"] == "REFRESHED", refreshed
    assert _row(request_id, "2_Face")["needs_check"] is True, "the refresh asks for a check"
    # While the check waits the screen keeps the prepared deck.
    assert [item["id"] for item in _deck(scx, request_id, scene_id)["deck"]["materials"]] == ["2"]
    result = _prepare()
    assert result["ready"] >= 1, result
    after = _row(request_id, "2_Face")
    assert after["status"] == "READY" and after["fingerprint"] != before["fingerprint"]
    assert [item["id"] for item in _deck(scx, request_id, scene_id)["deck"]["materials"]] == ["7"]


def test_parse_failure_is_failed_with_a_code_and_retry_prepares_again(scx):
    broken = MATERIALS.replace(b"inc/curves.inc", b"../../../../../../../../outside.inc")
    _decks(scx, broken)
    _project_id, request_id = _register(scx)
    _catalog(scx, request_id)
    result = _prepare()
    assert result["failed"] >= 1, result
    catalog = _catalog(scx, request_id)
    face = _scene(catalog, "2_Face")
    assert face["materials_status"] == "FAILED" and face["has_deck"] is True
    assert face["materials_error_code"] == "MATERIALS_INCLUDE_OUTSIDE_REQUEST"
    assert face["materials_error_message"]
    assert catalog["preparation"]["failed"] == 1
    body = _deck(scx, request_id, face["scene_id"])
    assert body["deck"] is None and body["analysis"]["status"] == "FAILED"
    assert body["analysis"]["error_code"] == "MATERIALS_INCLUDE_OUTSIDE_REQUEST"

    scx.drive.modify_file(scx.path(f"{SCENE}/materials.inc"), MATERIALS)   # fixed on the drive (not registered yet)
    retried = _deck(scx, request_id, face["scene_id"], retry="true")
    assert retried["analysis"]["status"] == "QUEUED"
    assert _row(request_id, "2_Face")["status"] == "PENDING"


def test_usage_retry_parses_a_failed_input_again_once_per_minute(scx, monkeypatch):
    """[다시 분석] of a usage Scene drops the cached FAILED parse so the next pass parses again (1/min/file)."""
    import uuid

    from app.parsers.optistruct_deck_parser import OptiStructParseError
    from app.services import optistruct_materials
    from tests.test_depth_schema import COND, USAGE, USAGE_CASE
    from tests.test_materials_optistruct import _mesh, _sample

    optistruct_materials.reset_for_tests()
    case = f"{USAGE}/Working/{USAGE_CASE}"
    scx.add(f"{case}/Settle/{COND}_settle_result.json", b'{"Set Tilt Angle @ Settle (deg)": 1.18}')
    scx.add(f"{case}/Settle/model.fem", ("BEGIN BULK\n" + _sample() + _mesh(20) + "ENDDATA\n").encode("utf-8"))
    scan = scx.post("/api/folder-discovery/environments/scan", {"environment": "USAGE", "relative_path": ""})
    preview = scx.post("/api/folder-discovery/environments/previews", {"scan_id": scan["id"], "assignments": []})
    request_id = scx.post("/api/folder-discovery/environments/registrations",
                          {"preview_id": preview["id"], "idempotency_key": f"rt-{uuid.uuid4()}", "capture": False})["request_id"]

    original = optistruct_materials._parse
    broken = {"on": True}

    def parse(*args, **kwargs):
        if broken["on"]:
            raise OptiStructParseError("MATERIALS_CARD_TOO_LARGE", "test: broken input")
        return original(*args, **kwargs)

    monkeypatch.setattr(optistruct_materials, "_parse", parse)

    def deck(**extra):
        response = scx.client.get(BASE + "/deck", params={"request_id": request_id, "environment": "USAGE",
                                                         "scene_id": scene_id, **extra})
        assert response.status_code == 200, response.text
        return response.json()

    _prepare()
    usage = scx.client.get(BASE + "/catalog", params={"request_id": request_id, "environment": "USAGE"}).json()
    scene_id = next(item for item in usage["scenes"] if item["label"] == "Settle")["scene_id"]
    assert deck()["analysis"]["error_code"] == "MATERIALS_CARD_TOO_LARGE"

    assert deck(retry="true")["analysis"]["status"] == "QUEUED"      # accepted: the cached failure is dropped
    _prepare()
    assert deck()["analysis"]["status"] == "FAILED"                  # parsed again (still broken)
    refused = deck(retry="true")                                     # a second retry within the minute
    assert refused["analysis"]["status"] == "FAILED" and refused["analysis"]["retry_after_seconds"]
    with optistruct_materials._lock:
        optistruct_materials._last_retry.clear()                     # a minute later
    broken["on"] = False
    assert deck(retry="true")["analysis"]["status"] == "QUEUED"
    _prepare()
    ready = deck()
    assert ready["analysis"]["status"] == "READY" and ready["deck"]["materials"]


def test_materials_pass_yields_to_capture_jobs_and_cancel(scx, monkeypatch):
    from app.services import capture_jobs

    _decks(scx)
    _project_id, request_id = _register(scx)
    capture_jobs._capture_wake.clear()                               # the worker's pass would clear it (none here)
    _catalog(scx, request_id)
    assert not capture_jobs.captures_waiting(), "a materials wake-up never interrupts the materials pass"
    downloads = len(scx.drive.downloads())

    with monkeypatch.context() as patch:
        patch.setattr(capture_jobs, "has_pending", lambda conn: True)
        assert materials_preparation.run_pending(yield_to_captures=True)["status"] == "YIELDED"

    capture_jobs.wake()                                              # a new capture job arrived
    try:
        stopped = materials_preparation.run_pending(should_stop=capture_jobs.captures_waiting)
        assert stopped["status"] == "STOPPED" and stopped["ready"] == 0, stopped
    finally:
        capture_jobs._capture_wake.clear()
        capture_jobs._wake.clear()
    import threading

    cancel = threading.Event()
    calls = {"n": 0}
    original = materials_preparation._prepare_distribution

    def cancel_after_first(*args, **kwargs):
        calls["n"] += 1
        cancel.set()
        return original(*args, **kwargs)

    monkeypatch.setattr(materials_preparation, "_prepare_distribution", cancel_after_first)
    result = materials_preparation.run_pending(cancel=cancel, scopes=[(_project_id, request_id, "DISTRIBUTION")])
    assert calls["n"] >= 1 and result["interrupted"] >= 1, result
    assert _row(request_id, "3_Face")["status"] == "PENDING" or _row(request_id, "2_Face")["status"] == "PENDING"
    assert len(scx.drive.downloads()) >= downloads


def test_scene_results_are_kept_across_read_rounds_and_written_per_scene(scx, monkeypatch):
    """Scenes prepared before a read miss are written once, each with its own short connection."""
    _decks(scx)
    scx.add(f"{OPTION}/3_Face/parts.inc", PARTS)
    scx.add(f"{OPTION}/3_Face/materials.inc", MATERIALS.replace(b"inc/curves.inc", b"../2_Face/inc/curves.inc"))
    _project_id, request_id = _register(scx)
    _catalog(scx, request_id)
    stores = []
    original = materials_preparation._store
    monkeypatch.setattr(materials_preparation, "_store", lambda conn, row, result: stores.append(row["relative_path"])
                        or original(conn, row, result))
    assert _prepare()["ready"] == 2
    assert sorted(path.rsplit("/", 1)[-1] for path in stores) == ["2_Face", "3_Face"]
    assert _row(request_id, "3_Face")["status"] == "READY"


def test_failed_folder_snapshot_backs_off(scx, monkeypatch):
    _decks(scx)
    project_id, request_id = _register(scx)
    with connect() as conn:
        conn.execute("UPDATE folder_environment_scans SET status='SUPERSEDED_TEST' WHERE request_id=? "
                     "AND id LIKE 'folder-refresh-%'", [request_id])
    calls = []
    monkeypatch.setattr(materials_preparation, "_ensure_snapshot", lambda *args: calls.append(args) or False)
    scope = (project_id, request_id, "DISTRIBUTION")
    _catalog(scx, request_id)                                        # queues the scope
    materials_preparation.run_pending()
    _catalog(scx, request_id)                                        # a poll queues it again
    materials_preparation.run_pending()
    assert [args[:3] for args in calls] == [scope], "one forced folder check per backoff period"


def test_duckdb_bootstrap_adds_materials_preparations_to_an_existing_database():
    """DuckDB parity of migration 0046 on a database that already has data (additive only)."""
    from app.config import database_settings
    from app.database import initialize_database

    if database_settings().backend != "duckdb":
        pytest.skip("DuckDB bootstrap only; PostgreSQL uses migration 0046")
    with connect() as conn:
        conn.execute("DROP TABLE IF EXISTS materials_preparations")
        conn.execute("INSERT INTO projects(id,name,product_name,description,created_at) VALUES "
                     "('materials-prep-upgrade-project','keep','p','d',CURRENT_TIMESTAMP) ON CONFLICT DO NOTHING")
    initialize_database()
    with connect() as conn:
        columns = {row[0] for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='materials_preparations'").fetchall()}
        kept = conn.execute("SELECT name FROM projects WHERE id='materials-prep-upgrade-project'").fetchone()
    assert {"root_key", "request_id", "environment", "scene_key", "status", "needs_check", "fingerprint",
            "deck_json", "files_json", "error_code", "error_message", "checked_at"} <= columns
    assert kept == ("keep",)
