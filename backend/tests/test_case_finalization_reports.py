"""Stage 6 Final designation: CAE mirror, uploaded PPTX/HTML reports, latest basis.

Synthetic SPDM tree in an isolated temp root (DEPTH_V1 Working layout).
"""
from __future__ import annotations

import hashlib
import io
import json
import struct
import zipfile
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from app.database_connection import connect
from app.services import case_finalization, dashboard_capture
from tests.test_environment_folder_flow_api import synthetic_pptx
from tests.test_new_scene_registration import CASE, CSV, CSV_BYTES, ENV, OPTION, REQUEST, _post, admin_client  # noqa: F401

pytestmark = pytest.mark.duckdb_integration

CASE_LABEL = CASE.rsplit("/", 1)[-1]
FINAL = f"{REQUEST}/Final"
HTML = b"<!DOCTYPE html>\n<html><head><meta charset=\"utf-8\"><title>t</title></head><body>\xed\x95\x9c</body></html>"
API = "/api/dashboard/finalizations"


def _seed(client, root: Path) -> dict[str, str]:
    for scene in ("2_Face", "3_Face"):
        folder = root / OPTION / scene
        folder.mkdir(parents=True)
        (folder / CSV).write_bytes(CSV_BYTES)
        (folder / "model.rad").write_text("/INCLUDE part.inc\n", encoding="utf-8")
        (folder / "part.inc").write_text(f"{scene} include\n", encoding="utf-8")
    (root / OPTION / "2_Face" / "contour.png").write_bytes(b"\x89PNG\r\n\x1a\nsynthetic")
    (root / OPTION / "2_Face" / "drop.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42synthetic")
    (root / OPTION / "2_Face" / "scene_review.pdf").write_bytes(b"%PDF-1.4 synthetic scene report")
    (root / OPTION / "3_Face" / "scene_table.xlsx").write_bytes(b"PK synthetic workbook")
    scan = _post(client, ENV + "/scan", {"environment": "DISTRIBUTION", "relative_path": ""})
    # DEPTH_V1: Working L6 is SCENE by depth; manual role assignments are not allowed (§6).
    scenes = [n for n in scan["nodes"] if n["relative_path"] in {f"{OPTION}/2_Face", f"{OPTION}/3_Face"}]
    assert [n["role_kind"] for n in scenes] == ["SCENE", "SCENE"]
    preview = _post(client, ENV + "/previews", {"scan_id": scan["id"], "assignments": []})
    assert preview["can_apply"], preview
    registered = _post(client, ENV + "/registrations", {
        "preview_id": preview["id"], "idempotency_key": f"final-{uuid4()}", "capture": True,
    })
    job = registered["capture_jobs"][0]
    assert job["status"] == "COMPLETED", registered
    return {"project_id": registered["project_id"], "request_id": registered["request_id"],
            "environment": "DISTRIBUTION", "case_id": job["case_id"],
            "capture_id": dashboard_capture.latest_capture_id(job["case_id"]), "_stored": job["capture_id"]}


def _body(ctx: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in ctx.items() if not key.startswith("_")}


def _tree(root: Path, *, exclude_final: bool) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if exclude_final and (relative == FINAL or relative.startswith(FINAL + "/")):
            continue
        result[relative] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "dir"
    return result


def _preview(client, ctx):
    response = client.post(f"{API}/preview", json=_body(ctx))
    assert response.status_code == 200, response.text
    return response.json()


def _upload(client, ctx, operation_id, fmt, data):
    return client.put(f"{API}/{operation_id}/reports/{fmt}", params=_body(ctx), content=data,
                      headers={"Content-Type": "application/octet-stream"})


def _confirm(client, ctx, operation_id, formats):
    return client.post(f"{API}/confirm", json={**_body(ctx), "operation_id": operation_id, "report_formats": formats})


def test_latest_basis_mirrors_scene_files_into_cae_and_stores_only_uploaded_reports(admin_client):
    client, root = admin_client
    ctx = _seed(client, root)
    working_before = _tree(root, exclude_final=True)
    plan = _preview(client, ctx)
    assert plan["schema_version"] == 2 and plan["basis"] == "LATEST"
    assert len(plan["operation_id"]) == 32 and case_finalization._OPERATION_ID.fullmatch(plan["operation_id"])
    assert {item["scene_path"].rsplit("/", 1)[-1] for item in plan["scene_sources"]} == {"2_Face", "3_Face"}
    assert {item["source_capture_id"] for item in plan["scene_sources"]} == {ctx["_stored"]}
    names = {item["case_relative_path"].rsplit("/", 1)[-1] for item in plan["files"]}
    assert {CSV, "model.rad", "part.inc", "scene_review.pdf", "scene_table.xlsx", "contour.png", "drop.mp4"} <= names
    assert {item["category"] for item in plan["files"]} == {"CAE"}
    assert plan["report_paths"]["pptx"] == f"{FINAL}/Reports/{CASE_LABEL}/{plan['operation_id']}/{CASE_LABEL}_report.pptx"
    assert plan["can_confirm"] is True

    # Client-supplied names/headers are ignored; the server picks the stored name.
    pptx = synthetic_pptx()
    staged = client.put(f"{API}/{plan['operation_id']}/reports/pptx", params=_body(ctx), content=pptx,
                        headers={"Content-Disposition": 'attachment; filename="..\\\\evil.pptx"'})
    assert staged.status_code == 200, staged.text
    assert staged.json()["file_name"] == f"{CASE_LABEL}_report.pptx"
    assert _upload(client, ctx, plan["operation_id"], "html", HTML).status_code == 200
    done = _confirm(client, ctx, plan["operation_id"], ["html", "pptx"])
    assert done.status_code == 200, done.text
    record = done.json()
    cae = root / record["output_paths"]["CAE"]
    reports = root / record["output_paths"]["Reports"]
    assert record["output_paths"]["CAE"] == f"{FINAL}/CAE/{CASE_LABEL}/{plan['operation_id']}"
    for item in record["files"]:
        target = cae / item["case_relative_path"]
        assert hashlib.sha256(target.read_bytes()).hexdigest() == item["sha256"]
    mirror = "Drop/85qn80h_ref_organized/INDIVIDUAL/2_Face"
    assert (cae / mirror / CSV).is_file() and (cae / mirror / "scene_review.pdf").is_file()
    assert (cae / mirror / "model.rad").is_file() and (cae / mirror / "part.inc").is_file()
    assert (cae / mirror / "contour.png").is_file() and (cae / mirror / "drop.mp4").is_file()
    assert sorted(path.name for path in reports.iterdir()) == [f"{CASE_LABEL}_report.html", f"{CASE_LABEL}_report.pptx"]
    assert (reports / f"{CASE_LABEL}_report.pptx").read_bytes() == pptx
    assert [(item["format"], item["sha256"]) for item in record["reports"]] == [
        ("pptx", hashlib.sha256(pptx).hexdigest()), ("html", hashlib.sha256(HTML).hexdigest())]
    # Nothing outside Final/ was written and Working is unchanged.
    assert _tree(root, exclude_final=True) == working_before
    assert set(path.name for path in (root / FINAL).iterdir()) == {"CAE", "Reports", ".finalizations"}

    status = client.get(f"{API}/status", params={k: v for k, v in _body(ctx).items() if k != "capture_id"})
    assert status.status_code == 200, status.text
    assert status.json()["selected_case_latest"]["operation_id"] == plan["operation_id"]
    assert status.json()["unverified_records"] == 0
    assert len(status.json()["selected_case_latest"]["reports"]) == 2


def test_latest_basis_takes_each_scene_from_its_newest_capture(admin_client):
    client, root = admin_client
    ctx = _seed(client, root)
    # A later capture (for example one registered Scene) holds only 3_Face with new bytes.
    changed = CSV_BYTES + b"BOTTOM,30,30,30,30\n"
    (root / OPTION / "3_Face" / CSV).write_bytes(changed)
    with connect() as conn:
        row = conn.execute("SELECT case_id,recipe_version,manifest_json,payload_json,created_by,created_at "
                           "FROM dashboard_captures WHERE id=?", [ctx["_stored"]]).fetchone()
        manifest = [dict(item) for item in (json.loads(row[2]) if isinstance(row[2], str) else row[2])
                    if "/3_Face/" in item["relative_path"]]
        for item in manifest:
            if item["relative_path"].endswith(CSV):
                item["sha256"], item["size"] = hashlib.sha256(changed).hexdigest(), len(changed)
        payload = json.loads(row[3]) if isinstance(row[3], str) else row[3]
        for run in payload.get("runs", []):
            run["scenes"] = [scene for scene in run["scenes"] if scene.get("label") == "3_Face"]
        second = f"capture-synthetic-{uuid4().hex}"
        conn.execute("INSERT INTO dashboard_captures(id,case_id,fingerprint,recipe_version,manifest_json,payload_json,created_by,created_at) "
                     "VALUES(?,?,?,?,?,?,?,?)",
                     [second, row[0], uuid4().hex, row[1], json.dumps(manifest), json.dumps(payload), row[4], row[5] + timedelta(hours=1)])
        latest = dashboard_capture.get_latest_capture(conn, ctx["case_id"])
    merged_sources = {scene["label"]: scene["source_capture_id"] for scene in latest["payload"]["scenes"]}
    plan = _preview(client, ctx)
    sources = {item["scene_path"].rsplit("/", 1)[-1]: item["source_capture_id"] for item in plan["scene_sources"]}
    assert sources == {"2_Face": ctx["_stored"], "3_Face": second} == merged_sources
    by_name = {item["case_relative_path"]: item for item in plan["files"]}
    face3 = by_name[f"Drop/85qn80h_ref_organized/INDIVIDUAL/3_Face/{CSV}"]
    assert (face3["source_capture_id"], face3["sha256"]) == (second, hashlib.sha256(changed).hexdigest())
    # The old stored capture is still accepted as a concrete basis (backward compatible),
    # and its stale 3_Face bytes are refused.
    stale = client.post(f"{API}/preview", json={**_body(ctx), "capture_id": ctx["_stored"]})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "FINALIZATION_SOURCE_STALE"
    # A capture arriving after preview makes the plan stale.
    assert _upload(client, ctx, plan["operation_id"], "pptx", synthetic_pptx()).status_code == 200
    with connect() as conn:
        conn.execute("INSERT INTO dashboard_captures(id,case_id,fingerprint,recipe_version,manifest_json,payload_json,created_by,created_at) "
                     "VALUES(?,?,?,?,?,?,?,?)",
                     [f"capture-synthetic-{uuid4().hex}", row[0], uuid4().hex, row[1], json.dumps(manifest), json.dumps(payload), row[4], row[5] + timedelta(hours=2)])
    late = _confirm(client, ctx, plan["operation_id"], ["pptx"])
    assert late.status_code == 409 and late.json()["detail"]["code"] == "FINALIZATION_CAPTURE_CHANGED"
    assert not (root / FINAL / "CAE").exists() and not (root / FINAL / "Reports").exists()


def test_report_is_required_and_must_be_uploaded_before_anything_is_copied(admin_client):
    client, root = admin_client
    ctx = _seed(client, root)
    plan = _preview(client, ctx)
    missing = _confirm(client, ctx, plan["operation_id"], [])
    assert missing.status_code == 422 and missing.json()["detail"]["code"] == "FINALIZATION_REPORT_REQUIRED"
    not_staged = _confirm(client, ctx, plan["operation_id"], ["html"])
    assert not_staged.status_code == 422 and not_staged.json()["detail"]["code"] == "FINALIZATION_REPORT_NOT_STAGED"
    assert not (root / FINAL / "CAE").exists() and not (root / FINAL / "Reports").exists()
    assert not (root / plan["metadata_relative_path"] / "complete.json").exists()
    status = client.get(f"{API}/status", params={k: v for k, v in _body(ctx).items() if k != "capture_id"}).json()
    assert status["selected_case_latest"] is None
    assert [item["operation_id"] for item in status["retryable_operations"]] == [plan["operation_id"]]


@pytest.mark.parametrize(("fmt", "data", "code"), [
    ("pptx", b"not a zip at all", "FINALIZATION_REPORT_PPTX_INVALID"),
    ("pptx", synthetic_pptx({"ppt/vbaProject.bin": b"macro"}), "FINALIZATION_REPORT_PPTX_MACRO"),
    ("pptx", synthetic_pptx(content_types=b'<Types><Default Extension="xml" ContentType="application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml"/></Types>'), "FINALIZATION_REPORT_PPTX_MACRO"),
    ("pptx", synthetic_pptx({"../escape.xml": b"x"}), "FINALIZATION_REPORT_PPTX_UNSAFE_ENTRY"),
    ("pptx", synthetic_pptx({"/abs.xml": b"x"}), "FINALIZATION_REPORT_PPTX_UNSAFE_ENTRY"),
    ("pptx", synthetic_pptx({"ppt/media/bomb.bin": b"\0" * (4 * 1024 * 1024)}), "FINALIZATION_REPORT_PPTX_TOO_LARGE_UNCOMPRESSED"),
    ("html", b"<html><body>no doctype</body></html>", "FINALIZATION_REPORT_HTML_INVALID"),
    ("html", b"<!doctype html><p>\xff\xfe</p>", "FINALIZATION_REPORT_HTML_INVALID"),
    ("html", b"", "FINALIZATION_REPORT_EMPTY"),
])
def test_unsafe_or_malformed_reports_are_rejected(admin_client, fmt, data, code):
    client, root = admin_client
    ctx = _seed(client, root)
    plan = _preview(client, ctx)
    response = _upload(client, ctx, plan["operation_id"], fmt, data)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == code
    assert not (root / plan["metadata_relative_path"] / "reports.json").exists()
    assert not (root / FINAL / "Reports").exists()


def test_pptx_without_presentation_part_and_oversize_reports_are_rejected(admin_client, monkeypatch):
    client, root = admin_client
    ctx = _seed(client, root)
    plan = _preview(client, ctx)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
    assert _upload(client, ctx, plan["operation_id"], "pptx", buffer.getvalue()).json()["detail"]["code"] == "FINALIZATION_REPORT_PPTX_INVALID"
    monkeypatch.setitem(case_finalization.MAX_REPORT_BYTES, "html", 64)
    oversize = _upload(client, ctx, plan["operation_id"], "html", HTML + b" " * 64)
    assert oversize.status_code == 413 and oversize.json()["detail"]["code"] == "FINALIZATION_REPORT_TOO_LARGE"
    monkeypatch.setattr(case_finalization, "MAX_PPTX_UNCOMPRESSED_BYTES", 10)
    bomb = _upload(client, ctx, plan["operation_id"], "pptx", synthetic_pptx())
    assert bomb.json()["detail"]["code"] == "FINALIZATION_REPORT_PPTX_TOO_LARGE_UNCOMPRESSED"
    assert not (root / plan["metadata_relative_path"] / "reports.json").exists()


def test_retry_replaces_unfinished_reports_and_completion_is_immutable(admin_client, monkeypatch):
    client, root = admin_client
    ctx = _seed(client, root)
    plan = _preview(client, ctx)
    operation = plan["operation_id"]
    first = synthetic_pptx({"ppt/slides/slide1.xml": b"first"})
    second = synthetic_pptx({"ppt/slides/slide1.xml": b"second"})
    assert _upload(client, ctx, operation, "pptx", first).status_code == 200
    # The reports are published, then verification fails before completion.
    original_verify = case_finalization._verify_outputs
    monkeypatch.setattr(case_finalization, "_verify_outputs", lambda *args, **kwargs: False)
    failed = _confirm(client, ctx, operation, ["pptx"])
    assert failed.status_code == 422 and failed.json()["detail"]["code"] == "FINALIZATION_OUTPUT_VERIFY_FAILED"
    report_path = root / plan["report_paths"]["pptx"]
    assert report_path.read_bytes() == first
    assert not (root / plan["metadata_relative_path"] / "complete.json").exists()
    monkeypatch.setattr(case_finalization, "_verify_outputs", original_verify)
    # Before completion a re-upload may replace this operation's own report.
    assert _upload(client, ctx, operation, "pptx", second).status_code == 200
    done = _confirm(client, ctx, operation, ["pptx"])
    assert done.status_code == 200, done.text
    assert report_path.read_bytes() == second
    assert done.json()["reports"][0]["sha256"] == hashlib.sha256(second).hexdigest()
    # Same operation again: idempotent, and the completed files are immutable.
    again = _confirm(client, ctx, operation, ["pptx", "html"])
    assert again.status_code == 200 and again.json()["confirmed_at"] == done.json()["confirmed_at"]
    late = _upload(client, ctx, operation, "pptx", first)
    assert late.status_code == 409 and late.json()["detail"]["code"] == "FINALIZATION_ALREADY_COMPLETED"
    assert report_path.read_bytes() == second
    # A file in Reports that this operation did not upload is never overwritten.
    other = _preview(client, ctx)
    foreign = root / other["report_paths"]["pptx"]
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(b"user file")
    assert _upload(client, ctx, other["operation_id"], "pptx", first).status_code == 200
    conflict = _confirm(client, ctx, other["operation_id"], ["pptx"])
    assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "FINALIZATION_DESTINATION_CONFLICT"
    assert foreign.read_bytes() == b"user file"
    assert not (root / other["metadata_relative_path"] / "complete.json").exists()


def test_version_one_completed_record_still_verifies_in_status(admin_client):
    """Records written before stage 6 kept results under Final/Reports; history must stay valid."""
    client, root = admin_client
    ctx = _seed(client, root)
    operation = uuid4().hex
    with connect() as conn:
        fingerprint, manifest = conn.execute("SELECT fingerprint,manifest_json FROM dashboard_captures WHERE id=?", [ctx["_stored"]]).fetchone()
        case_path = conn.execute("SELECT relative_path FROM dashboard_cases WHERE id=?", [ctx["case_id"]]).fetchone()[0]
    manifest = json.loads(manifest) if isinstance(manifest, str) else manifest
    result = next(item for item in manifest if item["relative_path"].endswith(f"2_Face/{CSV}"))
    deck = f"{OPTION}/2_Face/model.rad"
    deck_bytes = (root / deck).read_bytes()
    files = [
        {"source_relative_path": deck, "case_relative_path": deck[len(case_path) + 1:], "category": "CAE",
         "source_basis": "CURRENT_CONFIRMED_SCENE", "size": len(deck_bytes), "sha256": hashlib.sha256(deck_bytes).hexdigest()},
        {"source_relative_path": result["relative_path"], "case_relative_path": result["relative_path"][len(case_path) + 1:],
         "category": "Reports", "source_basis": "SELECTED_CAPTURE", "size": result["size"], "sha256": result["sha256"]},
    ]
    output_paths = {category: f"{FINAL}/{category}/{CASE_LABEL}/{operation}" for category in ("CAE", "Reports")}
    for item in files:
        target = root / output_paths[item["category"]] / item["case_relative_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((root / item["source_relative_path"]).read_bytes())
    counts = {"CAE": 1, "Reports": 1, "input_decks": 1, "rad_decks": 1, "inc_decks": 0, "reports": 0, "results": 1}
    missing = {"input_decks": False, "rad_decks": False, "inc_decks": True, "reports": True}
    plan = case_finalization._signed_record({
        "schema_version": 1, "operation_id": operation, "status": "PREVIEW", "project_id": ctx["project_id"],
        "request_id": ctx["request_id"], "environment": "DISTRIBUTION", "case_id": ctx["case_id"],
        "case_path": case_path, "case_label": CASE_LABEL, "capture_id": ctx["_stored"], "capture_fingerprint": fingerprint,
        "folder_schema_snapshot_id": "synthetic-v1", "scene_paths": [f"{OPTION}/2_Face"],
        "metadata_relative_path": f"{FINAL}/.finalizations/{operation}", "final_relative_path": FINAL,
        "created_by": "synthetic", "previewed_at": "2026-10-01T00:00:00.000000Z", "excluded_capture_file_count": 0,
        "counts": counts, "missing": missing, "files": files,
    }, "plan_signature", case_finalization.PLAN_DOMAIN)
    complete = case_finalization._signed_record({
        "schema_version": 1, "operation_id": operation, "status": "COMPLETE", "plan_sha256": case_finalization._plan_hash(plan),
        "project_id": ctx["project_id"], "request_id": ctx["request_id"], "environment": "DISTRIBUTION",
        "case_id": ctx["case_id"], "capture_id": ctx["_stored"], "capture_fingerprint": fingerprint,
        "folder_schema_snapshot_id": "synthetic-v1", "output_paths": output_paths, "files": files,
        "counts": counts, "missing": missing, "created_by": "synthetic", "confirmed_at": "2026-10-01T00:00:01.000000Z",
    }, "complete_signature", case_finalization.COMPLETE_DOMAIN)
    metadata = root / FINAL / ".finalizations" / operation
    metadata.mkdir(parents=True)
    (metadata / "plan.json").write_bytes(case_finalization._encode(plan))
    (metadata / "complete.json").write_bytes(case_finalization._encode(complete))
    status = client.get(f"{API}/status", params={k: v for k, v in _body(ctx).items() if k != "capture_id"})
    assert status.status_code == 200, status.text
    latest = status.json()["selected_case_latest"]
    assert latest["operation_id"] == operation and latest["schema_version"] == 1
    assert latest["reports"] == [] and status.json()["unverified_records"] == 0
    # Damaging an old Final file is reported, never repaired or deleted.
    (root / output_paths["Reports"] / files[1]["case_relative_path"]).write_bytes(b"tampered")
    damaged = client.get(f"{API}/status", params={k: v for k, v in _body(ctx).items() if k != "capture_id"}).json()
    assert damaged["selected_case_latest"] is None and damaged["unverified_records"] == 1


# ---------------------------------------------------------------------------
# Hardening (review 2026-10-03): orphan reports, PPTX active content, zip
# directory limits, upload pre-checks, write failures, status budget, basis.
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures" / "case_finalization"
REAL_PPTX = FIXTURES / "pptxgenjs_4.0.1_text_table_image_chart.pptx"
CT_NS = b'xmlns="http://schemas.openxmlformats.org/package/2006/content-types"'
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"


def _status(client, ctx):
    response = client.get(f"{API}/status", params={k: v for k, v in _body(ctx).items() if k != "capture_id"})
    assert response.status_code == 200, response.text
    return response.json()


def _rels(*relationships: str) -> bytes:
    return ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(relationships) + "</Relationships>").encode()


def _workbook(extra: dict[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", b'<Types ' + CT_NS + b'><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/></Types>')
        archive.writestr("xl/workbook.xml", b"<workbook/>")
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _validate(data: bytes) -> str | None:
    try:
        case_finalization.validate_report("pptx", io.BytesIO(data))
    except case_finalization.CaseFinalizationError as exc:
        return exc.code
    return None


def test_orphan_report_retry_must_keep_published_formats_and_completion_cleans_staging(admin_client, monkeypatch):
    client, root = admin_client
    ctx = _seed(client, root)
    plan = _preview(client, ctx)
    operation = plan["operation_id"]
    pptx = synthetic_pptx()
    assert _upload(client, ctx, operation, "pptx", pptx).status_code == 200
    assert _upload(client, ctx, operation, "html", HTML).status_code == 200
    real_verify = case_finalization._verify_outputs
    monkeypatch.setattr(case_finalization, "_verify_outputs", lambda *args, **kwargs: False)
    failed = _confirm(client, ctx, operation, ["pptx", "html"])
    assert failed.status_code == 422 and failed.json()["detail"]["code"] == "FINALIZATION_OUTPUT_VERIFY_FAILED"
    monkeypatch.setattr(case_finalization, "_verify_outputs", real_verify)
    reports_dir = root / FINAL / "Reports" / CASE_LABEL / operation
    published = sorted(path.name for path in reports_dir.iterdir())
    assert published == [f"{CASE_LABEL}_report.html", f"{CASE_LABEL}_report.pptx"]
    # Dropping a format that is already published would leave an unrecorded report behind.
    subset = _confirm(client, ctx, operation, ["pptx"])
    assert subset.status_code == 409 and subset.json()["detail"]["code"] == "FINALIZATION_REPORT_FORMATS_MISMATCH", subset.text
    assert sorted(path.name for path in reports_dir.iterdir()) == published
    assert not (root / plan["metadata_relative_path"] / "complete.json").exists()
    done = _confirm(client, ctx, operation, ["pptx", "html"])
    assert done.status_code == 200, done.text
    record = done.json()
    assert sorted(path.name for path in (root / record["output_paths"]["Reports"]).iterdir()) == sorted(
        item["file_name"] for item in record["reports"])
    # The operation's own staged copies are removed after completion; the signed record stays.
    metadata = root / plan["metadata_relative_path"]
    assert (metadata / "reports.json").is_file()
    assert not any(path.is_file() for path in (metadata / "reports").iterdir())
    assert _status(client, ctx)["selected_case_latest"]["operation_id"] == operation


def test_unexpected_file_in_operation_reports_folder_blocks_completion_and_is_kept(admin_client, monkeypatch):
    client, root = admin_client
    ctx = _seed(client, root)
    plan = _preview(client, ctx)
    operation = plan["operation_id"]
    assert _upload(client, ctx, operation, "html", HTML).status_code == 200
    reports_dir = root / FINAL / "Reports" / CASE_LABEL / operation
    reports_dir.mkdir(parents=True)
    stray = reports_dir / "notes.txt"
    stray.write_bytes(b"user notes")
    blocked = _confirm(client, ctx, operation, ["html"])
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "FINALIZATION_REPORTS_UNEXPECTED_FILE"
    assert stray.read_bytes() == b"user notes" and not (root / FINAL / "CAE").exists()
    stray.unlink()
    # A file appearing while the reports are published is caught before complete.json.
    original_cleanup = case_finalization._cleanup_partial_files

    def cleanup_then_drop(*args, **kwargs):
        original_cleanup(*args, **kwargs)
        (reports_dir / f"{CASE_LABEL}_report.pptx").write_bytes(b"not ours")

    monkeypatch.setattr(case_finalization, "_cleanup_partial_files", cleanup_then_drop)
    late = _confirm(client, ctx, operation, ["html"])
    assert late.status_code == 409 and late.json()["detail"]["code"] == "FINALIZATION_REPORTS_UNEXPECTED_FILE"
    assert (reports_dir / f"{CASE_LABEL}_report.pptx").read_bytes() == b"not ours"
    assert not (root / plan["metadata_relative_path"] / "complete.json").exists()


def test_real_pptxgenjs_report_passes_validation():
    """pptxgenjs output (text, shape, table, image, chart with embedded .xlsx) must stay accepted."""
    assert _validate(REAL_PPTX.read_bytes()) is None
    import shutil
    import subprocess
    frontend = Path(__file__).resolve().parents[2] / "frontend"
    module = frontend / "node_modules" / "pptxgenjs" / "dist" / "pptxgen.cjs.js"
    node = shutil.which("node")
    if not node or not module.is_file():
        pytest.skip("node/pptxgenjs not available; the committed fixture covers the format")
    script = (
        "const P=require(process.argv[1]);(async()=>{const p=new P();p.layout='LAYOUT_WIDE';"
        "const s=p.addSlide();s.addText('한글',{x:0.5,y:0.3,w:8,h:0.5});s.addShape('roundRect',{x:0.5,y:1,w:3,h:1});"
        "s.addTable([[{text:'A'},{text:'B'}]],{x:0.5,y:2.5,w:4});"
        "s.addImage({data:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',x:5,y:1,w:1,h:1});"
        "const c=p.addSlide();c.addChart(p.ChartType.bar,[{name:'v',labels:['a','b'],values:[1,2]}],{x:0.5,y:0.5,w:6,h:4});"
        "process.stdout.write(await p.write({outputType:'nodebuffer'}));})();"
    )
    generated = subprocess.run([node, "-e", script, str(module)], capture_output=True, timeout=60, check=True).stdout
    assert generated.startswith(b"PK\x03\x04") and _validate(generated) is None


@pytest.mark.parametrize(("extra", "content_types", "code"), [
    # Content types decoded by BOM/declaration (UTF-16), not as UTF-8 with replacement.
    ({}, ('<?xml version="1.0" encoding="UTF-16"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml"/>'
          '</Types>').encode("utf-16"), "FINALIZATION_REPORT_PPTX_MACRO"),
    ({"ppt/macros.bin": b"x"}, b'<Types ' + CT_NS + b'><Override PartName="/ppt/macros.bin" ContentType="application/vnd.ms-office.vbaProject"/></Types>', "FINALIZATION_REPORT_PPTX_MACRO"),
    ({}, b'<?xml version="1.0"?><!DOCTYPE t [<!ENTITY a "aaaa">]><Types ' + CT_NS + b'/>', "FINALIZATION_REPORT_PPTX_INVALID"),
    ({}, b'<?xml version="1.0" encoding="ISO-8859-1"?><Types ' + CT_NS + b'/>', "FINALIZATION_REPORT_PPTX_INVALID"),
    ({"ppt/activeX/activeX1.xml": b"<x/>"}, None, "FINALIZATION_REPORT_PPTX_ACTIVE_CONTENT"),
    ({"ppt/embeddings/oleObject1.bin": b"\xd0\xcf\x11\xe0"}, None, "FINALIZATION_REPORT_PPTX_ACTIVE_CONTENT"),
    ({}, b'<Types ' + CT_NS + b'><Default Extension="bin" ContentType="application/vnd.openxmlformats-officedocument.oleObject"/></Types>', "FINALIZATION_REPORT_PPTX_ACTIVE_CONTENT"),
    ({"ppt/slides/_rels/slide1.xml.rels": _rels(f'<Relationship Id="r1" Type="{REL}image" Target="file://server/share/x.png" TargetMode="External"/>')}, None, "FINALIZATION_REPORT_PPTX_ACTIVE_CONTENT"),
    ({"ppt/_rels/presentation.xml.rels": _rels(f'<Relationship Id="r1" Type="{REL}attachedTemplate" Target="../t.potm"/>')}, None, "FINALIZATION_REPORT_PPTX_ACTIVE_CONTENT"),
    ({"ppt/slides/_rels/slide1.xml.rels": _rels(f'<Relationship Id="r1" Type="{REL}control" Target="../activeX/a.xml"/>')}, None, "FINALIZATION_REPORT_PPTX_ACTIVE_CONTENT"),
    ({"ppt/slides/_rels/slide1.xml.rels": _rels(f'<Relationship Id="r1" Type="http://schemas.microsoft.com/office/2006/relationships/vbaProject" Target="../x.bin"/>')}, None, "FINALIZATION_REPORT_PPTX_MACRO"),
    ({"ppt/charts/_rels/chart1.xml.rels": _rels(f'<Relationship Id="r1" Type="{REL}package" Target="../media/payload.docm"/>')}, None, "FINALIZATION_REPORT_PPTX_ACTIVE_CONTENT"),
    ({"ppt/slides/_rels/slide1.xml.rels": "<?xml version='1.0' encoding='UTF-16'?><Relationships><Relationship Id='r1' Type='x/oleObject' Target='a'/></Relationships>".encode("utf-16")}, None, "FINALIZATION_REPORT_PPTX_ACTIVE_CONTENT"),
    ({"ppt/embeddings/Microsoft_Excel_Worksheet1.xlsx": _workbook({"xl/vbaProject.bin": b"x"})}, None, "FINALIZATION_REPORT_PPTX_ACTIVE_CONTENT"),
    ({"ppt/a.xml:evil": b"x"}, None, "FINALIZATION_REPORT_PPTX_UNSAFE_ENTRY"),
])
def test_pptx_macro_and_active_content_is_rejected(extra, content_types, code):
    assert _validate(synthetic_pptx(extra, content_types=content_types)) == code


def test_pptx_chart_workbook_like_pptxgenjs_is_accepted():
    data = synthetic_pptx({
        "ppt/charts/_rels/chart1.xml.rels": _rels(f'<Relationship Id="r1" Type="{REL}package" Target="../embeddings/Microsoft_Excel_Worksheet1.xlsx"/>'),
        "ppt/embeddings/Microsoft_Excel_Worksheet1.xlsx": _workbook(),
    })
    assert _validate(data) is None


def test_zip_central_directory_is_bounded_before_zipfile_reads_it(monkeypatch):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr("ppt/presentation.xml", b"<p/>")
        for index in range(case_finalization.MAX_PPTX_ENTRIES):
            archive.writestr(zipfile.ZipInfo(format(index, "x")), b"")
    many = buffer.getvalue()
    constructed = []
    original = zipfile.ZipFile
    monkeypatch.setattr(case_finalization.zipfile, "ZipFile", lambda *a, **k: constructed.append(1) or original(*a, **k))
    assert _validate(many) == "FINALIZATION_REPORT_PPTX_TOO_MANY_ENTRIES"
    assert not constructed
    monkeypatch.setattr(case_finalization.zipfile, "ZipFile", original)
    with monkeypatch.context() as patcher:
        patcher.setattr(case_finalization, "MAX_ZIP_CENTRAL_DIRECTORY_BYTES", 64)
        assert _validate(synthetic_pptx({"ppt/slides/slide1.xml": b"<s/>"})) == "FINALIZATION_REPORT_PPTX_TOO_MANY_ENTRIES"
    # ZIP64 end record: its counts replace the 16/32-bit EOCD fields.
    small = synthetic_pptx()
    eocd_at = small.rindex(b"PK\x05\x06")
    eocd = bytearray(small[eocd_at:])
    directory_size, directory_offset = struct.unpack_from("<LL", eocd, 12)
    zip64 = struct.pack("<4sQ2H2L4Q", b"PK\x06\x06", 44, 45, 45, 0, 0, 20_000, 20_000, directory_size, directory_offset)
    locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, eocd_at, 1)
    struct.pack_into("<HH", eocd, 8, 0xFFFF, 0xFFFF)
    assert _validate(small[:eocd_at] + zip64 + locator + bytes(eocd)) == "FINALIZATION_REPORT_PPTX_TOO_MANY_ENTRIES"


def test_upload_checks_operation_before_reading_body_and_limits_concurrency(admin_client, monkeypatch):
    client, root = admin_client
    ctx = _seed(client, root)
    plan = _preview(client, ctx)
    staged = []
    with monkeypatch.context() as patcher:
        patcher.setattr(case_finalization, "stage_report", lambda *a, **k: staged.append(1))
        # The operation is checked before the body: an over-limit body to an unknown operation is 404, not 413.
        patcher.setitem(case_finalization.MAX_REPORT_BYTES, "html", 8)
        unknown = _upload(client, ctx, uuid4().hex, "html", HTML)
        assert unknown.status_code == 404 and unknown.json()["detail"]["code"] == "FINALIZATION_PLAN_NOT_FOUND"
        wrong_basis = _upload(client, {**ctx, "capture_id": ctx["_stored"]}, plan["operation_id"], "html", HTML)
        assert wrong_basis.status_code == 403 and wrong_basis.json()["detail"]["code"] == "FINALIZATION_OPERATION_SCOPE_MISMATCH"
        plan_path = root / plan["metadata_relative_path"] / "plan.json"
        original_plan = plan_path.read_bytes()
        tampered = json.loads(original_plan)
        tampered["case_label"] = "other"
        plan_path.write_bytes(json.dumps(tampered).encode())
        untrusted = _upload(client, ctx, plan["operation_id"], "html", HTML)
        assert untrusted.status_code == 409 and untrusted.json()["detail"]["code"] == "FINALIZATION_PLAN_UNTRUSTED"
        plan_path.write_bytes(original_plan)
        assert _upload(client, ctx, plan["operation_id"], "html", HTML).status_code == 413
        patcher.setitem(case_finalization.MAX_REPORT_BYTES, "html", 64 * 1024 * 1024)
        assert not staged
        # Per-process concurrency: a full slot pool answers 429 without reading the body.
        slots = case_finalization._REPORT_SLOTS
        for _ in range(case_finalization.REPORT_UPLOAD_CONCURRENCY):
            assert slots.acquire(blocking=False)
        try:
            busy = _upload(client, ctx, plan["operation_id"], "html", HTML)
            assert busy.status_code == 429 and busy.json()["detail"]["code"] == "FINALIZATION_REPORT_BUSY"
        finally:
            for _ in range(case_finalization.REPORT_UPLOAD_CONCURRENCY):
                slots.release()
        assert not staged
    assert _upload(client, ctx, plan["operation_id"], "html", HTML).status_code == 200
    assert _confirm(client, ctx, plan["operation_id"], ["html"]).status_code == 200
    with monkeypatch.context() as patcher:
        patcher.setitem(case_finalization.MAX_REPORT_BYTES, "html", 8)
        completed = _upload(client, ctx, plan["operation_id"], "html", HTML)
    assert completed.status_code == 409 and completed.json()["detail"]["code"] == "FINALIZATION_ALREADY_COMPLETED"


def test_storage_write_failures_are_reported_as_retryable_errors(admin_client, monkeypatch):
    import os
    client, root = admin_client
    ctx = _seed(client, root)
    plan = _preview(client, ctx)
    final = str(root / FINAL)
    real_replace, real_link = os.replace, os.link

    def denied(real):
        def call(source, destination, *args, **kwargs):
            if str(destination).startswith(final):
                raise PermissionError(13, "synthetic denied", str(destination))
            return real(source, destination, *args, **kwargs)
        return call

    monkeypatch.setattr(case_finalization.os, "replace", denied(real_replace))
    staged = _upload(client, ctx, plan["operation_id"], "html", HTML)
    assert staged.status_code == 503 and staged.json()["detail"]["code"] == "FINALIZATION_WRITE_FAILED", staged.text
    staging = root / plan["metadata_relative_path"] / "reports"
    assert not staging.exists() or not any(staging.iterdir())
    assert not (root / plan["metadata_relative_path"] / "reports.json").exists()
    monkeypatch.setattr(case_finalization.os, "replace", real_replace)
    assert _upload(client, ctx, plan["operation_id"], "html", HTML).status_code == 200
    monkeypatch.setattr(case_finalization.os, "link", denied(real_link))
    failed = _confirm(client, ctx, plan["operation_id"], ["html"])
    assert failed.status_code == 503 and failed.json()["detail"]["code"] == "FINALIZATION_WRITE_FAILED", failed.text
    monkeypatch.setattr(case_finalization.os, "link", real_link)
    assert not (root / plan["metadata_relative_path"] / "complete.json").exists()
    assert [item["operation_id"] for item in _status(client, ctx)["retryable_operations"]] == [plan["operation_id"]]
    assert not [path for path in (root / FINAL).rglob(".codex-partial-*")]
    assert _confirm(client, ctx, plan["operation_id"], ["html"]).status_code == 200


def test_status_hash_verifies_only_shown_records_within_budget(admin_client, monkeypatch):
    client, root = admin_client
    ctx = _seed(client, root)
    records = []
    for body in (HTML, HTML + b"<!-- second -->"):
        plan = _preview(client, ctx)
        assert _upload(client, ctx, plan["operation_id"], "html", body).status_code == 200
        done = _confirm(client, ctx, plan["operation_id"], ["html"])
        assert done.status_code == 200, done.text
        records.append(done.json())
    older, newer = records
    per_operation = sum(item["size"] for item in newer["files"]) + newer["reports"][0]["size"]
    # Enough for the shown record only: older history no longer exhausts the budget.
    monkeypatch.setattr(case_finalization, "MAX_STATUS_VERIFY_BYTES", per_operation + 64)
    state = _status(client, ctx)
    assert state["selected_case_latest"]["operation_id"] == newer["operation_id"] and state["unverified_records"] == 0
    # Same-size damage to history that is not shown is only caught when it would be shown.
    old_report = root / older["reports"][0]["relative_path"]
    old_bytes = old_report.read_bytes()
    old_report.write_bytes(old_bytes[:-1] + b"#")
    assert _status(client, ctx)["unverified_records"] == 0
    old_report.write_bytes(old_bytes)
    monkeypatch.setattr(case_finalization, "MAX_STATUS_VERIFY_BYTES", 1024 * 1024 * 1024)
    # Damaging the shown record: it is never shown as normal; the older verified record is.
    new_report = root / newer["reports"][0]["relative_path"]
    new_bytes = new_report.read_bytes()
    new_report.write_bytes(new_bytes[:-1] + b"#")
    damaged = _status(client, ctx)
    assert damaged["selected_case_latest"]["operation_id"] == older["operation_id"]
    assert damaged["latest"]["operation_id"] == older["operation_id"] and damaged["unverified_records"] == 1
    # A size change is caught by the existence/size check of every record.
    old_report.write_bytes(old_bytes + b"x")
    both = _status(client, ctx)
    assert both["selected_case_latest"] is None and both["unverified_records"] == 2


def _clone_capture(conn, source_id: str, *, hours: int, scene: str | None = None,
                   drop_schema: bool = False, changed: bytes | None = None) -> str:
    row = conn.execute("SELECT case_id,recipe_version,manifest_json,payload_json,created_by,created_at "
                       "FROM dashboard_captures WHERE id=?", [source_id]).fetchone()
    manifest = [dict(item) for item in (json.loads(row[2]) if isinstance(row[2], str) else row[2])
                if scene is None or f"/{scene}/" in item["relative_path"]]
    if changed is not None:
        for item in manifest:
            if item["relative_path"].endswith(CSV):
                item["sha256"], item["size"] = hashlib.sha256(changed).hexdigest(), len(changed)
    payload = json.loads(row[3]) if isinstance(row[3], str) else row[3]
    if scene is not None:
        for run in payload.get("runs", []):
            run["scenes"] = [item for item in run["scenes"] if item.get("label") == scene]
    if drop_schema:
        payload.get("context", {}).pop("folder_schema_locations", None)
    clone = f"capture-synthetic-{uuid4().hex}"
    conn.execute("INSERT INTO dashboard_captures(id,case_id,fingerprint,recipe_version,manifest_json,payload_json,created_by,created_at) "
                 "VALUES(?,?,?,?,?,?,?,?)",
                 [clone, row[0], uuid4().hex, row[1], json.dumps(manifest), json.dumps(payload), row[4], row[5] + timedelta(hours=hours)])
    return clone


def test_latest_basis_never_falls_back_when_newest_capture_lacks_schema(admin_client):
    client, root = admin_client
    ctx = _seed(client, root)
    changed = CSV_BYTES + b"BOTTOM,40,40,40,40\n"
    (root / OPTION / "3_Face" / CSV).write_bytes(changed)
    with connect() as conn:
        newest = _clone_capture(conn, ctx["_stored"], hours=1, scene="3_Face", drop_schema=True, changed=changed)
        merged = dashboard_capture.get_latest_capture(conn, ctx["case_id"])
    shown = {scene["label"]: scene["source_capture_id"] for scene in merged["payload"]["scenes"]}
    assert shown == {"2_Face": ctx["_stored"], "3_Face": newest}
    plan = _preview(client, ctx)
    # The screen shows 3_Face from the newest capture; Final must not silently copy the older one.
    assert {item["scene_path"].rsplit("/", 1)[-1]: item["source_capture_id"] for item in plan["scene_sources"]} == {"2_Face": ctx["_stored"]}
    assert [(item["scene_path"].rsplit("/", 1)[-1], item["source_capture_id"], item["reason"]) for item in plan["excluded_scenes"]] == [
        ("3_Face", newest, "CAPTURE_SCHEMA_MISSING")]
    assert not any("/3_Face/" in item["source_relative_path"] and item.get("source_basis") == "SOURCE_CAPTURE" for item in plan["files"])


def test_usage_latest_basis_uses_the_newest_capture_like_the_screen(admin_client):
    from tests.legacy_environment_profiles import legacy_profiles_active
    # Legacy Usage layout (no Working, EVALUATION roles): run as a pre-0034 registration (D8/D9).
    with legacy_profiles_active():
        _usage_latest_basis(admin_client)


def _usage_latest_basis(admin_client):
    from tests.test_environment_folder_flow_api import BASE, usage_files
    client, root = admin_client
    case_path = "Project_9911_Final/WR_9911_SimType1/Assy_RES_Model_SetCase1"
    usage_files(root / case_path)
    scan = client.post(BASE + "/scan", json={"environment": "USAGE", "relative_path": ""}).json()
    preview = client.post(BASE + "/previews", json={"scan_id": scan["id"], "assignments": []}).json()
    registered = client.post(BASE + "/registrations", json={"preview_id": preview["id"], "idempotency_key": f"usage-final-{uuid4()}", "capture": True}).json()
    job = registered["capture_jobs"][0]
    assert job["status"] == "COMPLETED", registered
    ctx = {"project_id": registered["project_id"], "request_id": registered["request_id"], "environment": "USAGE",
           "case_id": job["case_id"], "capture_id": dashboard_capture.latest_capture_id(job["case_id"])}
    with connect() as conn:
        newest = _clone_capture(conn, job["capture_id"], hours=1)
        shown = dashboard_capture.get_latest_capture(conn, job["case_id"])
    assert shown["payload"]["merged_capture_ids"] == [newest]
    plan = _preview(client, ctx)
    assert plan["basis"] == "LATEST" and plan["scene_sources"]
    assert {item["source_capture_id"] for item in plan["scene_sources"]} == {newest}
    assert {item["source_capture_id"] for item in plan["files"] if item["source_basis"] == "SOURCE_CAPTURE"} == {newest}


def test_viewer_upload_is_denied_before_the_service_runs(admin_client, monkeypatch):
    from datetime import datetime, timezone
    from fastapi.testclient import TestClient
    from app.main import app
    from app.security import hash_password
    client, root = admin_client
    ctx = _seed(client, root)
    plan = _preview(client, ctx)
    called = []
    monkeypatch.setattr(case_finalization, "stage_report", lambda *a, **k: called.append(1))
    monkeypatch.setattr(case_finalization, "check_report_target", lambda *a, **k: called.append(1))
    viewer_id, now = f"viewer-{uuid4().hex}", datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute("INSERT INTO users (id,username,password_hash,display_name,legacy_role,is_active,created_at,updated_at,account_status,is_global_admin) "
                     "VALUES (?,?,?,?,'viewer',true,?,?,'ACTIVE',false)", [viewer_id, viewer_id, hash_password("synthetic-viewer-password"), viewer_id, now, now])
        conn.execute("INSERT INTO project_memberships (id,project_id,user_id,role,created_by,created_at,updated_by,updated_at) VALUES (?,?,?,'general',?,?,?,?)",
                     [f"m-{uuid4().hex}", ctx["project_id"], viewer_id, "synthetic", now, "synthetic", now])
    with TestClient(app) as viewer:
        token = viewer.post("/api/auth/login", json={"username": viewer_id, "password": "synthetic-viewer-password"}).json()["access_token"]
        viewer.headers["Authorization"] = f"Bearer {token}"
        assert _upload(viewer, ctx, plan["operation_id"], "html", HTML).status_code == 403
    assert not called


def test_chunked_body_over_limit_and_tampered_staged_copy_are_refused(admin_client, monkeypatch):
    client, root = admin_client
    ctx = _seed(client, root)
    plan = _preview(client, ctx)
    with monkeypatch.context() as patcher:
        patcher.setitem(case_finalization.MAX_REPORT_BYTES, "html", 1000)

        def chunks():
            yield HTML
            for _ in range(10):
                yield b"a" * 500

        chunked = client.put(f"{API}/{plan['operation_id']}/reports/html", params=_body(ctx), content=chunks())
        assert chunked.status_code == 413, chunked.text
    assert _upload(client, ctx, plan["operation_id"], "html", HTML).status_code == 200
    staged = root / plan["metadata_relative_path"] / "reports" / plan["report_files"]["html"]
    staged.write_bytes(HTML + b"<script>x</script>")
    refused = _confirm(client, ctx, plan["operation_id"], ["html"])
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "FINALIZATION_REPORT_STAGE_INVALID"
    assert not (root / FINAL / "Reports").exists()


def test_completion_marker_temp_cleanup_failure_is_best_effort(admin_client, monkeypatch):
    """After complete.json is linked, a failed temporary-name cleanup must not fail the operation."""
    client, root = admin_client
    ctx = _seed(client, root)
    plan = _preview(client, ctx)
    assert _upload(client, ctx, plan["operation_id"], "html", HTML).status_code == 200
    original_unlink = Path.unlink

    def refuse_marker_temp(path, *args, **kwargs):
        if path.name.startswith(".complete-"):
            raise PermissionError("synthetic cleanup failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse_marker_temp)
    done = _confirm(client, ctx, plan["operation_id"], ["html"])
    assert done.status_code == 200, done.text
    operation_dir = root / plan["metadata_relative_path"]
    assert (operation_dir / "complete.json").is_file()
    assert [path.name for path in operation_dir.iterdir() if path.name.startswith(".complete-")]
