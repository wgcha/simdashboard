"""W2 Final copy rework: streaming copy, no format filter or size caps, free-space check,
staging + folder rename publication, background job with progress, resume and retry.

Synthetic SPDM tree in an isolated temp root (DEPTH_V1 Working layout); never real data.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import tracemalloc
from collections import namedtuple
from pathlib import Path
from uuid import uuid4

import pytest

from app.database_connection import connect
from app.services import case_finalization, case_finalization_jobs
from app.services.storage import local as storage_local
from app.services.storage import provider as storage_provider
from app.services.storage.local import LocalFsProvider
from tests.test_case_finalization_reports import (  # noqa: F401
    API, CASE_LABEL, FINAL, HTML, _body, _confirm, _job, _preview, _seed, _status, _tree, _upload,
)
from tests.test_new_scene_registration import CSV, OPTION, admin_client  # noqa: F401

pytestmark = pytest.mark.duckdb_integration

SCENE2 = f"{OPTION}/2_Face"
MIRROR2 = "Drop/85qn80h_ref_organized/INDIVIDUAL/2_Face"


@pytest.fixture(autouse=True)
def _test_wrappers_may_call_final_writes(monkeypatch):
    """Provider methods wrapped by these tests run with this module on the stack (S3 caller check)."""
    monkeypatch.setattr(storage_provider, "FINAL_WRITER_MODULES",
                        frozenset({*storage_provider.FINAL_WRITER_MODULES, __name__}))


class _Crash(BaseException):
    """Stands in for the server process being killed mid-copy (not caught by the worker)."""


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _start(client, ctx, operation_id, formats=("html",)):
    response = client.post(f"{API}/confirm", json={**_body(ctx), "operation_id": operation_id, "report_formats": list(formats)})
    assert response.status_code == 200, response.text
    return response.json()


def _ready(client, root, ctx, *, html=HTML):
    plan = _preview(client, ctx)
    assert _upload(client, ctx, plan["operation_id"], "html", html).status_code == 200
    return plan


def _signed_progress(root: Path, plan) -> dict:
    return json.loads((root / plan["metadata_relative_path"] / "progress.json").read_text(encoding="utf-8"))


def test_all_scene_files_are_copied_except_temp_lock_and_hidden_entries(admin_client):
    client, root = admin_client
    ctx = _seed(client, root)
    scene = root / SCENE2
    (scene / "model.h3d").write_bytes(b"h3d synthetic")
    (scene / "anim.A001").write_bytes(b"animation step")
    (scene / "notes.txt").write_text("notes", encoding="utf-8")
    (scene / "sub" / "deeper").mkdir(parents=True)
    (scene / "sub" / "deeper" / "raw.bin").write_bytes(os.urandom(4096))
    (scene / "~$scene_review.pptx").write_bytes(b"office lock")
    (scene / "scratch.TMP").write_bytes(b"temp")
    (scene / ".hidden").write_bytes(b"hidden")
    (scene / "$system").write_bytes(b"system")
    (scene / "~backup.rad").write_bytes(b"editor backup")
    (scene / ".git").mkdir()
    (scene / ".git" / "config").write_bytes(b"[core]")
    working_before = _tree(root, exclude_final=True)
    plan = _preview(client, ctx)
    names = {item["case_relative_path"] for item in plan["files"]}
    for kept in ("model.h3d", "anim.A001", "notes.txt", "sub/deeper/raw.bin", "contour.png", "drop.mp4", "scene_review.pdf", CSV):
        assert f"{MIRROR2}/{kept}" in names, kept
    assert not any(part in name for name in names for part in ("~$", ".TMP", ".hidden", "$system", "~backup", ".git"))
    assert plan["counts"]["other_files"] >= 4 and plan["counts"]["total_bytes"] == sum(item["size"] for item in plan["files"])
    by_path = {item["case_relative_path"]: item for item in plan["files"]}
    # Capture-pinned results carry their captured hash; other files are pinned by size/mtime and hashed while copying.
    assert by_path[f"{MIRROR2}/{CSV}"]["source_basis"] == "SOURCE_CAPTURE" and by_path[f"{MIRROR2}/{CSV}"]["sha256"]
    assert by_path[f"{MIRROR2}/notes.txt"]["sha256"] is None and by_path[f"{MIRROR2}/notes.txt"]["modified_ns"] > 0
    assert plan["disk"]["sufficient"] is True and plan["disk"]["required_bytes"] == plan["counts"]["total_bytes"]
    assert _upload(client, ctx, plan["operation_id"], "html", HTML).status_code == 200
    done = _confirm(client, ctx, plan["operation_id"], ["html"])
    assert done.status_code == 200, done.text
    record = done.json()
    cae = root / record["output_paths"]["CAE"]
    for item in record["files"]:
        assert _sha(cae / item["case_relative_path"]) == item["sha256"] == _sha(root / item["source_relative_path"])
    assert (cae / MIRROR2 / "sub" / "deeper" / "raw.bin").is_file()
    assert not (cae / MIRROR2 / "~$scene_review.pptx").exists() and not (cae / MIRROR2 / ".git").exists()
    assert _tree(root, exclude_final=True) == working_before
    # Completed jobs leave no staging behind; signed records stay.
    operation_dir = root / plan["metadata_relative_path"]
    assert not (operation_dir / "staging").exists()
    assert {"plan.json", "job.json", "progress.json", "complete.json", "copied.jsonl", "reports.json"} <= {p.name for p in operation_dir.iterdir()}
    status = _status(client, ctx)
    assert status["selected_case_latest"]["operation_id"] == plan["operation_id"]
    assert status["selected_case_latest"]["verification"] == "SHA256" and status["active_operations"] == []


def _peak_while_finalizing(client, root, ctx) -> tuple[int, dict]:
    plan = _ready(client, root, ctx)
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        done = _confirm(client, ctx, plan["operation_id"], ["html"])
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert done.status_code == 200, done.text
    return peak, done.json()


def test_large_file_streams_with_bounded_memory(admin_client):
    client, root = admin_client
    ctx = _seed(client, root)
    small_peak, _record = _peak_while_finalizing(client, root, ctx)
    big = root / SCENE2 / "solver_output.bin"
    size = 320 * 1024 * 1024
    with big.open("wb") as stream:  # sparse: no real disk use for the source
        stream.truncate(size)
    big_peak, record = _peak_while_finalizing(client, root, ctx)
    copied = root / record["output_paths"]["CAE"] / MIRROR2 / "solver_output.bin"
    assert copied.stat().st_size == size and _sha(copied) == _sha(big)
    assert {item["case_relative_path"]: item["size"] for item in record["files"]}[f"{MIRROR2}/solver_output.bin"] == size
    # Memory does not grow with the file: the copy and the read-back use fixed 8 MiB chunks.
    assert big_peak - small_peak < 24 * 1024 * 1024, (small_peak, big_peak)
    assert big_peak < size // 4, big_peak


def test_free_space_is_checked_before_copying(admin_client, monkeypatch):
    client, root = admin_client
    ctx = _seed(client, root)
    plan = _ready(client, root, ctx)
    usage = namedtuple("usage", "total used free")
    real_usage = storage_local.shutil.disk_usage
    monkeypatch.setattr(storage_local.shutil, "disk_usage", lambda path: usage(10 ** 12, 10 ** 12, 1024))
    assert _preview(client, ctx)["disk"]["sufficient"] is False
    refused = client.post(f"{API}/confirm", json={**_body(ctx), "operation_id": plan["operation_id"], "report_formats": ["html"]})
    assert refused.status_code == 507 and refused.json()["detail"]["code"] == "FINALIZATION_DISK_SPACE", refused.text
    assert not (root / plan["metadata_relative_path"] / "job.json").exists()
    assert not (root / FINAL / "CAE").exists()
    monkeypatch.setattr(storage_local.shutil, "disk_usage", real_usage)
    assert _confirm(client, ctx, plan["operation_id"], ["html"]).status_code == 200


def test_destination_is_invisible_until_published_and_progress_is_reported(admin_client, monkeypatch):
    client, root = admin_client
    ctx = _seed(client, root)
    plan = _ready(client, root, ctx)
    cae_dest = root / plan["output_paths"]["CAE"]
    report_dest = root / plan["output_paths"]["Reports"]
    staging = root / plan["metadata_relative_path"] / "staging"
    gate, reached = threading.Event(), threading.Event()
    seen: list[tuple[bool, bool, bool]] = []
    real_copy = LocalFsProvider.copy_stream
    real_verify = case_finalization._verify_staged_cae

    def slow_copy(self, *args, **kwargs):
        if not reached.is_set():
            reached.set()
            assert gate.wait(30)
        return real_copy(self, *args, **kwargs)

    def verify_then_look(*args, **kwargs):
        real_verify(*args, **kwargs)
        seen.append((cae_dest.exists(), report_dest.exists(), (staging / "CAE").is_dir() and (staging / "Report").is_dir()))

    monkeypatch.setattr(LocalFsProvider, "copy_stream", slow_copy)
    monkeypatch.setattr(case_finalization, "_verify_staged_cae", verify_then_look)
    started = _start(client, ctx, plan["operation_id"])
    assert started["state"] in {"QUEUED", "RUNNING"} and started["files_total"] == len(plan["files"])
    assert started["bytes_total"] == plan["counts"]["total_bytes"]
    assert reached.wait(30)
    running = _job(client, ctx, plan["operation_id"])
    assert running["state"] == "RUNNING" and running["phase"] == "COPYING" and running["active"] is True
    assert set(running) >= {"files_done", "files_total", "bytes_done", "bytes_total", "current_file", "error", "record"}
    status = _status(client, ctx)
    assert [job["operation_id"] for job in status["active_operations"]] == [plan["operation_id"]]
    assert status["retryable_operations"][0]["job"]["state"] == "RUNNING"
    # While copying: report re-upload is refused, a repeated confirm only reports the running job.
    busy = _upload(client, ctx, plan["operation_id"], "html", HTML)
    assert busy.status_code == 409 and busy.json()["detail"]["code"] == "FINALIZATION_JOB_ACTIVE"
    again = _start(client, ctx, plan["operation_id"])
    assert again["state"] == "RUNNING"
    assert not cae_dest.exists() and not report_dest.exists()
    gate.set()
    assert case_finalization_jobs.wait_idle(60)
    assert seen == [(False, False, True)]
    finished = _job(client, ctx, plan["operation_id"])
    assert finished["state"] == "COMPLETE" and finished["record"]["operation_id"] == plan["operation_id"]
    assert finished["files_done"] == finished["files_total"] and cae_dest.is_dir() and report_dest.is_dir()


def test_crash_mid_copy_resumes_on_startup_with_identical_hashes(admin_client, monkeypatch):
    client, root = admin_client
    ctx = _seed(client, root)
    (root / SCENE2 / "big.dat").write_bytes(os.urandom(3 * 1024 * 1024 + 17))
    working_before = _tree(root, exclude_final=True)
    plan = _ready(client, root, ctx)
    total = len(plan["files"])
    real_copy = LocalFsProvider.copy_stream
    calls: list[str] = []

    def crash_on_third(self, src_rel, dst_rel, **kwargs):
        calls.append(src_rel)
        if len(calls) == 3:
            raise _Crash()
        return real_copy(self, src_rel, dst_rel, **kwargs)

    monkeypatch.setattr(LocalFsProvider, "copy_stream", crash_on_third)
    _start(client, ctx, plan["operation_id"])
    assert case_finalization_jobs.wait_idle(60)
    progress = _signed_progress(root, plan)
    assert progress["state"] == "RUNNING"  # "killed" while running
    partial = root / plan["metadata_relative_path"] / "staging" / "partial"
    # A real kill skips every cleanup: leave a torn partial file exactly as it would remain.
    (partial / ("f" * 32)).write_bytes(b"torn")
    assert not (root / plan["output_paths"]["CAE"]).exists()
    monkeypatch.setattr(LocalFsProvider, "copy_stream", real_copy)
    copied_after_restart: list[str] = []

    def counting(self, src_rel, dst_rel, **kwargs):
        copied_after_restart.append(src_rel)
        return real_copy(self, src_rel, dst_rel, **kwargs)

    monkeypatch.setattr(LocalFsProvider, "copy_stream", counting)
    # App restart: the startup scan finds the interrupted job and resumes the same Final ID.
    with connect() as conn:
        resumed = case_finalization.resume_incomplete_jobs(conn)
    assert resumed == [plan["metadata_relative_path"]]
    assert case_finalization_jobs.wait_idle(60)
    view = _job(client, ctx, plan["operation_id"])
    assert view["state"] == "COMPLETE", view
    assert view["attempt"] == 2
    # The two files staged before the crash were skipped (hash-verified instead); the torn one was redone.
    assert len(copied_after_restart) == total - 2 + 1  # + the HTML report
    record = view["record"]
    cae = root / record["output_paths"]["CAE"]
    for item in record["files"]:
        assert _sha(cae / item["case_relative_path"]) == item["sha256"] == _sha(root / item["source_relative_path"])
    assert not (root / plan["metadata_relative_path"] / "staging").exists()
    assert _tree(root, exclude_final=True) == working_before


def test_status_poll_resumes_an_interrupted_job(admin_client, monkeypatch):
    client, root = admin_client
    ctx = _seed(client, root)
    plan = _ready(client, root, ctx)
    real_copy = LocalFsProvider.copy_stream

    def crash(self, src_rel, dst_rel, **kwargs):
        raise _Crash()

    monkeypatch.setattr(LocalFsProvider, "copy_stream", crash)
    _start(client, ctx, plan["operation_id"])
    assert case_finalization_jobs.wait_idle(60)
    assert _signed_progress(root, plan)["state"] == "RUNNING"
    monkeypatch.setattr(LocalFsProvider, "copy_stream", real_copy)
    status = _status(client, ctx)  # sees RUNNING without a worker -> resumes
    assert status["active_operations"][0]["active"] is True
    assert case_finalization_jobs.wait_idle(60)
    assert _status(client, ctx)["selected_case_latest"]["operation_id"] == plan["operation_id"]


def test_rename_failure_leaves_no_completion_and_retry_publishes_the_rest(admin_client, monkeypatch):
    client, root = admin_client
    ctx = _seed(client, root)
    plan = _ready(client, root, ctx)
    monkeypatch.setattr(case_finalization, "RENAME_RETRY_DELAYS", (0.01, 0.01))
    real_rename = LocalFsProvider.rename_no_replace
    attempts: list[str] = []

    def locked_report_folder(self, src_rel, dst_rel, *, zone):
        if "/Report/" in dst_rel:
            attempts.append(dst_rel)
            raise PermissionError(32, "synthetic sharing violation")
        return real_rename(self, src_rel, dst_rel, zone=zone)

    monkeypatch.setattr(LocalFsProvider, "rename_no_replace", locked_report_folder)
    failed = _confirm(client, ctx, plan["operation_id"], ["html"])
    assert failed.status_code == 409 and failed.json()["detail"]["code"] == "FINALIZATION_PUBLISH_BUSY", failed.text
    assert len(attempts) == 3  # first try + two retries
    operation_dir = root / plan["metadata_relative_path"]
    assert (root / plan["output_paths"]["CAE"]).is_dir() and not (root / plan["output_paths"]["Reports"]).exists()
    assert not (operation_dir / "complete.json").exists()
    status = _status(client, ctx)
    assert status["selected_case_latest"] is None
    assert status["active_operations"][0]["state"] == "FAILED"
    assert status["active_operations"][0]["error"]["code"] == "FINALIZATION_PUBLISH_BUSY"
    monkeypatch.setattr(LocalFsProvider, "rename_no_replace", real_rename)
    copies: list[str] = []
    real_copy = LocalFsProvider.copy_stream

    def counting(self, src_rel, dst_rel, **kwargs):
        copies.append(src_rel)
        return real_copy(self, src_rel, dst_rel, **kwargs)

    monkeypatch.setattr(LocalFsProvider, "copy_stream", counting)
    done = _confirm(client, ctx, plan["operation_id"], ["html"])  # retry: same Final ID
    assert done.status_code == 200, done.text
    assert copies == []  # CAE was already published; the staged report was reused
    assert sorted(p.name for p in (root / plan["output_paths"]["Reports"]).iterdir()) == [f"{CASE_LABEL}_report.html"]
    assert (operation_dir / "complete.json").is_file()


def test_crash_between_rename_and_progress_adopts_only_a_verified_destination(admin_client, monkeypatch):
    client, root = admin_client
    ctx = _seed(client, root)
    plan = _ready(client, root, ctx)
    real_update = case_finalization._Progress.update

    def crash_after_cae_rename(self, *, force=False, **fields):
        if fields.get("published") == ["CAE"]:
            raise _Crash()
        return real_update(self, force=force, **fields)

    monkeypatch.setattr(case_finalization._Progress, "update", crash_after_cae_rename)
    _start(client, ctx, plan["operation_id"])
    assert case_finalization_jobs.wait_idle(60)
    assert (root / plan["output_paths"]["CAE"]).is_dir()
    assert _signed_progress(root, plan)["published"] == []
    monkeypatch.setattr(case_finalization._Progress, "update", real_update)
    view = _job(client, ctx, plan["operation_id"])  # poll resumes
    assert view["active"] is True
    assert case_finalization_jobs.wait_idle(60)
    assert _job(client, ctx, plan["operation_id"])["state"] == "COMPLETE"


def test_foreign_destination_folder_is_never_overwritten(admin_client):
    client, root = admin_client
    ctx = _seed(client, root)
    plan = _ready(client, root, ctx)
    foreign = root / plan["output_paths"]["CAE"]
    foreign.mkdir(parents=True)
    (foreign / "keep.txt").write_bytes(b"user file")
    refused = client.post(f"{API}/confirm", json={**_body(ctx), "operation_id": plan["operation_id"], "report_formats": ["html"]})
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "FINALIZATION_DESTINATION_CONFLICT"
    assert [p.name for p in foreign.iterdir()] == ["keep.txt"]
    # A folder appearing while the job copies: the rename refuses to replace it.
    (foreign / "keep.txt").unlink()
    foreign.rmdir()
    real_verify = case_finalization._verify_staged_cae

    def appear(*args, **kwargs):
        real_verify(*args, **kwargs)
        foreign.mkdir(parents=True)
        (foreign / "late.txt").write_bytes(b"late user file")

    case_finalization._verify_staged_cae = appear
    try:
        failed = _confirm(client, ctx, plan["operation_id"], ["html"])
    finally:
        case_finalization._verify_staged_cae = real_verify
    assert failed.status_code == 409 and failed.json()["detail"]["code"] == "FINALIZATION_DESTINATION_CONFLICT", failed.text
    assert [p.name for p in foreign.iterdir()] == ["late.txt"]
    assert not (root / plan["metadata_relative_path"] / "complete.json").exists()


def test_source_change_during_copy_fails_as_stale_without_publishing(admin_client, monkeypatch):
    client, root = admin_client
    ctx = _seed(client, root)
    plan = _ready(client, root, ctx)
    target = f"{SCENE2}/model.rad"
    real_copy = LocalFsProvider.copy_stream

    def modify_while_copying(self, src_rel, dst_rel, *, on_progress=None, **kwargs):
        if src_rel == target:
            def progress(count):
                with self.path(src_rel).open("ab") as stream:
                    stream.write(b"# edited while copying\n")
                if on_progress:
                    on_progress(count)
            return real_copy(self, src_rel, dst_rel, on_progress=progress, **kwargs)
        return real_copy(self, src_rel, dst_rel, on_progress=on_progress, **kwargs)

    monkeypatch.setattr(LocalFsProvider, "copy_stream", modify_while_copying)
    failed = _confirm(client, ctx, plan["operation_id"], ["html"])
    assert failed.status_code == 409 and failed.json()["detail"]["code"] == "FINALIZATION_SOURCE_STALE", failed.text
    assert not (root / plan["output_paths"]["CAE"]).exists() and not (root / plan["metadata_relative_path"] / "complete.json").exists()
    assert not [p for p in (root / plan["metadata_relative_path"] / "staging" / "partial").iterdir()]


def test_capture_pinned_result_with_same_size_new_bytes_is_stale(admin_client):
    client, root = admin_client
    ctx = _seed(client, root)
    plan = _ready(client, root, ctx)
    result = root / SCENE2 / CSV
    data = result.read_bytes()
    result.write_bytes(data[:-2] + b"9\n")  # same size, different content: only the hash shows it
    failed = _confirm(client, ctx, plan["operation_id"], ["html"])
    assert failed.status_code == 409 and failed.json()["detail"]["code"] == "FINALIZATION_SOURCE_STALE", failed.text
    assert not (root / plan["output_paths"]["CAE"]).exists()


def test_damaged_staged_copy_is_recopied_before_publication(admin_client, monkeypatch):
    client, root = admin_client
    ctx = _seed(client, root)
    plan = _ready(client, root, ctx)
    real_verify = case_finalization._verify_staged_cae
    staging = root / plan["metadata_relative_path"] / "staging" / "CAE"

    def damage_then_verify(*args, **kwargs):
        victim = staging / MIRROR2 / "model.rad"
        victim.write_bytes(b"X" * victim.stat().st_size)
        return real_verify(*args, **kwargs)

    monkeypatch.setattr(case_finalization, "_verify_staged_cae", damage_then_verify)
    done = _confirm(client, ctx, plan["operation_id"], ["html"])
    assert done.status_code == 200, done.text
    cae = root / done.json()["output_paths"]["CAE"]
    assert (cae / MIRROR2 / "model.rad").read_bytes() == (root / SCENE2 / "model.rad").read_bytes()


def test_status_shows_large_records_size_verified_instead_of_failing(admin_client, monkeypatch):
    client, root = admin_client
    ctx = _seed(client, root)
    plan = _ready(client, root, ctx)
    assert _confirm(client, ctx, plan["operation_id"], ["html"]).status_code == 200
    monkeypatch.setattr(case_finalization, "MAX_STATUS_VERIFY_BYTES", 0)
    status = _status(client, ctx)
    assert status["selected_case_latest"]["operation_id"] == plan["operation_id"]
    assert status["selected_case_latest"]["verification"] == "SIZE" and status["unverified_records"] == 0


def test_version_two_completed_record_still_verifies(admin_client):
    """Records completed before W2 (plan version 2, every file hash in the plan) stay valid history."""
    client, root = admin_client
    ctx = _seed(client, root)
    operation = uuid4().hex
    with connect() as conn:
        fingerprint = conn.execute("SELECT fingerprint FROM dashboard_captures WHERE id=?", [ctx["_stored"]]).fetchone()[0]
        case_path = conn.execute("SELECT relative_path FROM dashboard_cases WHERE id=?", [ctx["case_id"]]).fetchone()[0]
    deck = f"{SCENE2}/model.rad"
    deck_bytes = (root / deck).read_bytes()
    files = [{"source_relative_path": deck, "case_relative_path": deck[len(case_path) + 1:], "category": "CAE",
              "source_basis": "CURRENT_CONFIRMED_SCENE", "size": len(deck_bytes), "sha256": hashlib.sha256(deck_bytes).hexdigest()}]
    report_name = f"{CASE_LABEL}_report.html"
    reports = [{"format": "html", "file_name": report_name, "size": len(HTML), "sha256": hashlib.sha256(HTML).hexdigest()}]
    output_paths = {"CAE": f"{FINAL}/CAE/{CASE_LABEL}/{operation}", "Reports": f"{FINAL}/Report/{CASE_LABEL}/{operation}"}
    (root / output_paths["CAE"] / files[0]["case_relative_path"]).parent.mkdir(parents=True)
    (root / output_paths["CAE"] / files[0]["case_relative_path"]).write_bytes(deck_bytes)
    (root / output_paths["Reports"]).mkdir(parents=True)
    (root / output_paths["Reports"] / report_name).write_bytes(HTML)
    counts = {"CAE": 1, "input_decks": 1, "rad_decks": 1, "inc_decks": 0, "results": 0, "scene_reports": 0}
    missing = {"input_decks": False, "rad_decks": False, "inc_decks": True}
    sources = [{"scene_path": SCENE2, "source_capture_id": ctx["_stored"], "source_capture_fingerprint": fingerprint}]
    plan = case_finalization._signed_record({
        "schema_version": 2, "operation_id": operation, "status": "PREVIEW", "project_id": ctx["project_id"],
        "request_id": ctx["request_id"], "environment": "DISTRIBUTION", "case_id": ctx["case_id"],
        "case_path": case_path, "case_label": CASE_LABEL, "basis": "LATEST", "capture_id": ctx["capture_id"],
        "capture_fingerprint": "synthetic", "scene_sources": sources, "folder_schema_snapshot_id": "synthetic-v2",
        "scene_paths": [SCENE2], "excluded_scenes": [],
        "metadata_relative_path": f"{FINAL}/.finalizations/{operation}", "final_relative_path": FINAL,
        "created_by": "synthetic", "previewed_at": "2026-10-04T00:00:00.000000Z", "excluded_capture_file_count": 0,
        "counts": counts, "missing": missing, "files": files,
        "report_files": {"pptx": f"{CASE_LABEL}_report.pptx", "html": report_name},
    }, "plan_signature", case_finalization.PLAN_DOMAIN)
    complete = case_finalization._signed_record({
        "schema_version": 2, "operation_id": operation, "status": "COMPLETE", "plan_sha256": case_finalization._plan_hash(plan),
        "project_id": ctx["project_id"], "request_id": ctx["request_id"], "environment": "DISTRIBUTION",
        "case_id": ctx["case_id"], "capture_id": ctx["capture_id"], "capture_fingerprint": "synthetic",
        "folder_schema_snapshot_id": "synthetic-v2", "output_paths": output_paths, "files": files, "reports": reports,
        "counts": counts, "missing": missing, "created_by": "synthetic", "confirmed_at": "2026-10-04T00:00:01.000000Z",
    }, "complete_signature", case_finalization.COMPLETE_DOMAIN)
    metadata = root / FINAL / ".finalizations" / operation
    metadata.mkdir(parents=True)
    (metadata / "plan.json").write_bytes(case_finalization._encode(plan))
    (metadata / "complete.json").write_bytes(case_finalization._encode(complete))
    status = _status(client, ctx)
    latest = status["selected_case_latest"]
    assert latest["operation_id"] == operation and latest["schema_version"] == 2 and latest["verification"] == "SHA256"
    assert [item["file_name"] for item in latest["reports"]] == [report_name] and status["unverified_records"] == 0
    # The old record is history only: never moved, deleted or re-copied.
    assert (root / output_paths["CAE"] / files[0]["case_relative_path"]).read_bytes() == deck_bytes
    (root / output_paths["Reports"] / report_name).write_bytes(HTML[:-1] + b"#")
    damaged = _status(client, ctx)
    assert damaged["selected_case_latest"] is None and damaged["unverified_records"] == 1


# ---------------------------------------------------------------------------
# Storage primitive (LocalFsProvider) — exercised as the FINAL zone writer module.
# ---------------------------------------------------------------------------

@pytest.fixture
def final_fs(tmp_path):
    root = tmp_path / "spdm"
    (root / "Req" / "Final" / ".finalizations" / "op").mkdir(parents=True)
    (root / "Req" / "Working").mkdir(parents=True)
    return LocalFsProvider(root), root


def test_copy_stream_hashes_while_copying_and_refuses_links_and_existing_targets(final_fs):
    fs, root = final_fs
    payload = os.urandom(3 * 1024 * 1024 + 5)
    (root / "Req" / "Working" / "a.bin").write_bytes(payload)
    progress: list[int] = []
    result = fs.copy_stream("Req/Working/a.bin", "Req/Final/.finalizations/op/a.bin", zone="FINAL",
                            chunk_size=1024 * 1024, on_progress=progress.append)
    assert result.size == len(payload) and result.sha256 == hashlib.sha256(payload).hexdigest() and result.method == "STREAM"
    assert sum(progress) == len(payload) and len(progress) == 4
    assert (root / "Req/Final/.finalizations/op/a.bin").read_bytes() == payload
    with pytest.raises(FileExistsError):
        fs.copy_stream("Req/Working/a.bin", "Req/Final/.finalizations/op/a.bin", zone="FINAL")
    (root / "Req" / "Working" / "link.bin").symlink_to(root / "Req" / "Working" / "a.bin")
    with pytest.raises(storage_provider.SpdmStorageError) as unsafe:
        fs.copy_stream("Req/Working/link.bin", "Req/Final/.finalizations/op/b.bin", zone="FINAL")
    assert unsafe.value.code == "SPDM_PATH_UNSAFE" and not (root / "Req/Final/.finalizations/op/b.bin").exists()
    with pytest.raises(storage_provider.StorageError):
        fs.copy_stream("Req/Working/a.bin", "Req/Working/copy.bin", zone="FINAL")  # outside the FINAL zone


def test_copy_stream_server_side_seam_is_tried_first_and_falls_back(final_fs, monkeypatch):
    fs, root = final_fs
    payload = b"seam payload"
    (root / "Req" / "Working" / "s.bin").write_bytes(payload)
    calls: list[str] = []

    def unsupported(source, destination):
        calls.append("unsupported")
        return False

    monkeypatch.setattr(storage_local, "server_side_copy", unsupported)
    assert fs.copy_stream("Req/Working/s.bin", "Req/Final/.finalizations/op/s1", zone="FINAL").method == "STREAM"

    def os_copy(source, destination):
        calls.append("os")
        with open(destination, "xb") as stream:
            stream.write(Path(source).read_bytes())
        return True

    monkeypatch.setattr(storage_local, "server_side_copy", os_copy)
    result = fs.copy_stream("Req/Working/s.bin", "Req/Final/.finalizations/op/s2", zone="FINAL")
    assert result.method == "OS_COPY" and result.sha256 == hashlib.sha256(payload).hexdigest()
    assert calls == ["unsupported", "os"]


def test_rename_no_replace_never_replaces_a_folder(final_fs):
    fs, root = final_fs
    staging = root / "Req/Final/.finalizations/op/staging/CAE"
    staging.mkdir(parents=True)
    (staging / "x.txt").write_bytes(b"x")
    (root / "Req/Final/CAE/Case").mkdir(parents=True)
    existing = root / "Req/Final/CAE/Case/op"
    existing.mkdir()
    with pytest.raises(FileExistsError):
        fs.rename_no_replace("Req/Final/.finalizations/op/staging/CAE", "Req/Final/CAE/Case/op", zone="FINAL")
    assert (staging / "x.txt").is_file() and not list(existing.iterdir())
    existing.rmdir()
    fs.rename_no_replace("Req/Final/.finalizations/op/staging/CAE", "Req/Final/CAE/Case/op", zone="FINAL")
    assert (existing / "x.txt").read_bytes() == b"x" and not staging.exists()


def test_try_lock_does_not_wait(final_fs):
    fs, _root = final_fs
    with fs.try_lock("Req/Final/.finalizations/op/.job.lock", zone="FINAL"):
        with pytest.raises(storage_provider.SpdmStorageError) as busy:
            with fs.try_lock("Req/Final/.finalizations/op/.job.lock", zone="FINAL"):
                pass
        assert busy.value.code == "FINALIZATION_LOCK_BUSY"
    with fs.try_lock("Req/Final/.finalizations/op/.job.lock", zone="FINAL"):
        pass
