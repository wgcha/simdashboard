"""Stage 6 Final designation: CAE mirror, uploaded PPTX/HTML reports, latest basis.

Synthetic SPDM tree in an isolated temp root (DEPTH_V1 Working layout).
"""
from __future__ import annotations

import hashlib
import io
import json
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
    assignments = [{"node_id": n["id"], "role_kind": "SCENE", "confirm": True}
                   for n in scan["nodes"] if n["relative_path"] in {f"{OPTION}/2_Face", f"{OPTION}/3_Face"}]
    preview = _post(client, ENV + "/previews", {"scan_id": scan["id"], "assignments": assignments})
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
