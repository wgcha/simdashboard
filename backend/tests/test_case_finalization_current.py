"""W3: current Final per request + environment, re-designation, SPDM summary file (Final/current.json).

Synthetic SPDM tree in an isolated temp root (DEPTH_V1 Working layout); never real data.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import threading
from pathlib import Path
from uuid import uuid4

import pytest

from app.services import case_finalization, case_finalization_jobs, dashboard_capture
from app.services.storage import provider as storage_provider
from tests.test_case_finalization_copy_jobs import _ready, _start
from tests.test_case_finalization_reports import API, CASE_LABEL, FINAL, _body, _confirm, _job, _preview, _status
from tests.test_new_scene_registration import CASE, CSV, CSV_BYTES, ENV, OPTION, REQUEST, _post, admin_client  # noqa: F401

pytestmark = pytest.mark.duckdb_integration

SUMMARY = f"{FINAL}/current.json"
_REAL_WRITE_SUMMARY = case_finalization._write_summary
CASE_B = f"{REQUEST}/Working/Package_Model_SetCase3_CushionCase3"


@pytest.fixture(autouse=True)
def _test_wrappers_may_call_final_writes(monkeypatch):
    monkeypatch.setattr(storage_provider, "FINAL_WRITER_MODULES",
                        frozenset({*storage_provider.FINAL_WRITER_MODULES, __name__}))


def _seed_two(client, root: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Two Cases (A, B) of one distribution request, registered and captured."""
    for scene in ("2_Face", "3_Face"):
        folder = root / OPTION / scene
        folder.mkdir(parents=True)
        (folder / CSV).write_bytes(CSV_BYTES)
        (folder / "model.rad").write_text("/INCLUDE part.inc\n", encoding="utf-8")
        (folder / "part.inc").write_text(f"{scene} include\n", encoding="utf-8")
    shutil.copytree(root / CASE, root / CASE_B)
    scan = _post(client, ENV + "/scan", {"environment": "DISTRIBUTION", "relative_path": ""})
    preview = _post(client, ENV + "/previews", {"scan_id": scan["id"], "assignments": []})
    assert preview["can_apply"], preview
    registered = _post(client, ENV + "/registrations", {
        "preview_id": preview["id"], "idempotency_key": f"final-{uuid4()}", "capture": True,
    })
    jobs = registered["capture_jobs"]
    assert len(jobs) == 2 and all(job["status"] == "COMPLETED" for job in jobs), registered
    contexts = []
    for job in jobs:
        contexts.append({"project_id": registered["project_id"], "request_id": registered["request_id"],
                         "environment": "DISTRIBUTION", "case_id": job["case_id"],
                         "capture_id": dashboard_capture.latest_capture_id(job["case_id"]), "_stored": job["capture_id"]})
    by_label = {}
    for ctx in contexts:
        plan = _preview(client, ctx)
        by_label[plan["case_label"]] = ctx
    return by_label[CASE_LABEL], by_label[CASE_B.rsplit("/", 1)[-1]]


def _designate(client, root, ctx):
    plan = _ready(client, root, ctx)
    done = _confirm(client, ctx, plan["operation_id"], ["html"])
    assert done.status_code == 200, done.text
    return done.json()


def _summary(root: Path) -> dict:
    return json.loads((root / SUMMARY).read_text(encoding="utf-8"))


def _tree(path: Path) -> dict[str, str]:
    return {item.relative_to(path).as_posix(): hashlib.sha256(item.read_bytes()).hexdigest()
            for item in sorted(path.rglob("*")) if item.is_file()}


def test_redesignation_moves_the_current_final_and_keeps_the_previous_files(admin_client):
    client, root = admin_client
    ctx_a, ctx_b = _seed_two(client, root)
    record_a = _designate(client, root, ctx_a)
    summary = _summary(root)
    entry = summary["environments"]["DISTRIBUTION"]
    assert summary["format"] == "simdashboard-final-summary" and summary["schema_version"] == 1
    assert entry["final_id"] == record_a["operation_id"] and entry["case_label"] == CASE_LABEL
    assert entry["previous_final_id"] is None
    assert entry["cae_path"] == f"CAE/{CASE_LABEL}/{record_a['operation_id']}"
    assert entry["report_path"] == f"Report/{CASE_LABEL}/{record_a['operation_id']}"
    assert entry["complete_record"] == f".finalizations/{record_a['operation_id']}/complete.json"
    complete_a = (root / FINAL / ".finalizations" / record_a["operation_id"] / "complete.json").read_bytes()
    assert entry["complete_sha256"] == hashlib.sha256(complete_a).hexdigest()
    assert {item["path"]: item["sha256"] for item in entry["files"]} == {
        item["case_relative_path"]: item["sha256"] for item in record_a["files"]}
    assert entry["reports"][0]["path"] == f"Report/{CASE_LABEL}/{record_a['operation_id']}/{CASE_LABEL}_report.html"
    status_a = _status(client, ctx_a)
    assert status_a["current_final"]["operation_id"] == record_a["operation_id"]
    assert status_a["summary"]["state"] == "OK"
    cae_a = _tree(root / record_a["output_paths"]["CAE"])

    record_b = _designate(client, root, ctx_b)
    entry = _summary(root)["environments"]["DISTRIBUTION"]
    assert entry["final_id"] == record_b["operation_id"] and entry["previous_final_id"] == record_a["operation_id"]
    assert entry["case_label"] == CASE_B.rsplit("/", 1)[-1]
    # The previous Final's folders stay exactly as they were (D14).
    assert _tree(root / record_a["output_paths"]["CAE"]) == cae_a
    assert (root / record_a["output_paths"]["Reports"] / f"{CASE_LABEL}_report.html").is_file()
    status = _status(client, ctx_a)
    assert status["current_final"]["operation_id"] == record_b["operation_id"]
    assert status["current_final"]["case_id"] == ctx_b["case_id"] and status["current_final"]["designated_at"]
    assert [(item["operation_id"], item["role"]) for item in status["final_history"]] == [
        (record_b["operation_id"], "CURRENT"), (record_a["operation_id"], "PREVIOUS")]
    assert status["summary"]["state"] == "OK"
    # The same Case designated again becomes current too.
    record_a2 = _designate(client, root, ctx_a)
    entry = _summary(root)["environments"]["DISTRIBUTION"]
    assert entry["final_id"] == record_a2["operation_id"] and entry["previous_final_id"] == record_b["operation_id"]
    assert [item["role"] for item in _status(client, ctx_b)["final_history"]] == ["CURRENT", "PREVIOUS", "PREVIOUS"]


def test_last_committed_completion_is_current_even_if_it_started_first(admin_client, monkeypatch):
    client, root = admin_client
    ctx_a, ctx_b = _seed_two(client, root)
    plan_a = _ready(client, root, ctx_a)
    plan_b = _ready(client, root, ctx_b)
    gate, reached = threading.Event(), threading.Event()
    real_verify = case_finalization._verify_published

    def hold_a(root_, plan, *args, **kwargs):
        if plan["operation_id"] == plan_a["operation_id"]:
            reached.set()
            assert gate.wait(60)
        return real_verify(root_, plan, *args, **kwargs)

    monkeypatch.setattr(case_finalization, "_verify_published", hold_a)
    _start(client, ctx_a, plan_a["operation_id"])
    assert reached.wait(60)
    _start(client, ctx_b, plan_b["operation_id"])
    # Same request: the worker queue runs B only after A; A waits before its completion commit.
    gate.set()
    assert case_finalization_jobs.wait_idle(120)
    a = _job(client, ctx_a, plan_a["operation_id"])["record"]
    b = _job(client, ctx_b, plan_b["operation_id"])["record"]
    newest = max((a, b), key=lambda record: record["confirmed_at"])
    assert a["confirmed_at"] != b["confirmed_at"]
    assert _summary(root)["environments"]["DISTRIBUTION"]["final_id"] == newest["operation_id"]
    assert _status(client, ctx_a)["current_final"]["operation_id"] == newest["operation_id"]


def test_an_older_completion_never_replaces_a_newer_pointer(admin_client):
    client, root = admin_client
    ctx_a, ctx_b = _seed_two(client, root)
    record_a = _designate(client, root, ctx_a)
    record_b = _designate(client, root, ctx_b)
    metadata = f"{FINAL}/.finalizations"
    designations = case_finalization._read_designations(root, metadata)
    plan_a = json.loads((root / metadata / record_a["operation_id"] / "plan.json").read_text(encoding="utf-8"))
    complete_a = json.loads((root / metadata / record_a["operation_id"] / "complete.json").read_text(encoding="utf-8"))
    assert case_finalization._designate(root, plan_a, complete_a, designations) is False
    assert _summary(root)["environments"]["DISTRIBUTION"]["final_id"] == record_b["operation_id"]
    # Completion times are strictly increasing per request even if the clock steps back.
    later = case_finalization._next_confirmed_at({"last_confirmed_at": "2999-01-01T00:00:00.000000Z"})
    assert later == "2999-01-01T00:00:00.000001Z"


def test_summary_write_failure_keeps_the_final_complete_and_repair_fixes_it(admin_client, monkeypatch):
    client, root = admin_client
    ctx_a, _ctx_b = _seed_two(client, root)

    def refuse(*args, **kwargs):
        raise case_finalization.CaseFinalizationError("FINALIZATION_WRITE_FAILED", "synthetic")

    monkeypatch.setattr(case_finalization, "_write_summary", refuse)
    record = _designate(client, root, ctx_a)
    assert not (root / SUMMARY).exists()
    status = _status(client, ctx_a)
    assert status["current_final"]["operation_id"] == record["operation_id"]
    assert status["selected_case_latest"]["operation_id"] == record["operation_id"]
    assert status["summary"]["state"] == "MISSING"
    monkeypatch.setattr(case_finalization, "_write_summary", _REAL_WRITE_SUMMARY)
    repaired = client.post(f"{API}/summary/repair", json={k: v for k, v in _body(ctx_a).items() if k != "capture_id"})
    assert repaired.status_code == 200, repaired.text
    assert repaired.json()["summary"]["state"] == "OK"
    assert _summary(root)["environments"]["DISTRIBUTION"]["final_id"] == record["operation_id"]



def test_legacy_request_without_summary_shows_newest_as_current_and_get_never_writes(admin_client):
    client, root = admin_client
    ctx_a, ctx_b = _seed_two(client, root)
    record_a = _designate(client, root, ctx_a)
    record_b = _designate(client, root, ctx_b)
    # A request from before W3: no summary and no signed pointer.
    (root / SUMMARY).unlink()
    (root / FINAL / ".finalizations" / "designations.json").unlink()
    before = sorted(path.relative_to(root).as_posix() for path in (root / FINAL).rglob("*"))
    status = _status(client, ctx_a)
    assert status["current_final"]["operation_id"] == record_b["operation_id"]
    assert [item["role"] for item in status["final_history"]] == ["CURRENT", "PREVIOUS"]
    assert status["summary"]["state"] == "MISSING"
    job = client.get(f"{API}/{record_a['operation_id']}/job", params={k: v for k, v in _body(ctx_a).items() if k != "capture_id"})
    assert job.status_code in {200, 404}
    assert sorted(path.relative_to(root).as_posix() for path in (root / FINAL).rglob("*")) == before
    # The next completion knows its predecessor even without the pointer file.
    record_c = _designate(client, root, ctx_a)
    entry = _summary(root)["environments"]["DISTRIBUTION"]
    assert entry["final_id"] == record_c["operation_id"] and entry["previous_final_id"] == record_b["operation_id"]


def test_stale_summary_is_reported(admin_client):
    client, root = admin_client
    ctx_a, ctx_b = _seed_two(client, root)
    _designate(client, root, ctx_a)
    old = (root / SUMMARY).read_bytes()
    _designate(client, root, ctx_b)
    (root / SUMMARY).write_bytes(old)
    assert _status(client, ctx_a)["summary"]["state"] == "STALE"


def test_summary_is_only_writable_at_its_one_place():
    allows = storage_provider.final_zone_allows
    assert allows("P/WR/Final/current.json")
    assert allows(f"P/WR/Final/.current.json.{'a' * 32}.tmp")
    assert not allows("P/WR/Final/other.json")
    assert not allows("P/WR/Final/current.json/x")
    assert not allows("P/WR/Working/Final/current.json")
    assert not allows("Final/current.json")
    assert not allows("P/WR/current.json")
    with pytest.raises(storage_provider.StorageError):
        storage_provider.check_write("P/WR/Working/current.json", storage_provider.FINAL, "app.services.case_finalization")
