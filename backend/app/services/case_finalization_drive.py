"""Final designation on the SCX drive (stage D3, integration 05 §6, plan §3 "Final 지정").

scx mode cannot rename, replace or delete on the drive, so the local W2/W3 design
(staging folder → rename, ``reports.json``/``current.json`` replaced, lock files,
cleanup) is split as follows.  The plan, signing, report checks and record
formats are the local ones (:mod:`.case_finalization`); only storage differs.

=====================================  ===============================================
local mode                             scx mode
=====================================  ===============================================
``.request.lock``                      ``drive_locks`` row ``final:<project>/<request>``
                                       (held from confirm until the batch finishes)
``plan.json``/``reports.json``         ``finalization_operations.plan_json``/``reports_json``
                                       (plan copy uploaded once at confirm, immutable)
report uploads ``reports/*``           server staging ``<STAGING>/upload-<op>/reports/``
copy to staging + rename               queue ``COPY`` items → ``copy_within`` straight to
                                       ``Final/CAE/<Case>/<op>/…`` (fallback download →
                                       ``upload_new`` when the drive cannot copy, C7)
reports to ``Final/Report/<Case>/<op>``  queue ``FILE`` items (``upload_new``)
``complete.json``                      queue ``COMPLETE_MARKER`` — the batch's **last**
                                       item, built when every earlier item is ``DONE``
                                       (same signed format; copy kept in DB)
``designations.json``/``current.json``  append-only ``Final/.finalizations/designations/
                                       <seq 8 digits>-<Final ID>.json`` (new file per
                                       designation; the largest ``seq`` is current)
status scan of ``.finalizations``      DB (``finalization_operations`` + queue)
=====================================  ===============================================

Nothing on the drive is ever deleted, moved or overwritten.  A failed batch leaves
its partial output without ``complete.json`` (incomplete by definition); a retry
re-queues the stopped items of the same Final ID.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from ..database_connection import ConnectionLike, connect
from . import case_finalization as cf
from . import result_registration_paths, spdm_storage
from .drive import gateway as drive_gateway
from .drive import reads as drive_reads
from .drive import upload_queue
from .storage import provider_for_root, server_local

logger = logging.getLogger(__name__)

STORAGE = "scx"
LOCK_MINUTES = 30
DESIGNATIONS_FOLDER = "designations"
DESIGNATION_SEQ_DIGITS = 8
VERIFICATION_LABEL = "DRIVE_UPLOAD"
_OPERATION_ID = re.compile(r"^[0-9a-f]{32}$")
Error = cf.CaseFinalizationError


def active() -> bool:
    """scx mode with an SPDM root on the drive: Final uses this module instead of the local file design."""
    return drive_reads.active()


def _now_db() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value).isoformat().replace("+00:00", "Z")
    return str(value) if value else None


def _loads(value: Any) -> Any:
    if value is None:
        return None
    return json.loads(value) if isinstance(value, (str, bytes)) else value


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _staging(operation_id: str) -> Path:
    return upload_queue.staging_dir_for(operation_id)


# --- operations -------------------------------------------------------------------------------

_OP_COLUMNS = ("operation_id", "root_key", "project_id", "request_id", "environment", "case_id", "capture_id", "status",
               "plan_json", "reports_json", "report_formats", "upload_batch_id", "complete_json", "complete_sha256",
               "confirmed_at", "designation_seq", "designation_batch_id", "error_code", "error_message", "created_by",
               "confirmed_by", "created_at", "queued_at", "updated_at")


def _operation(conn: ConnectionLike, operation_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT " + ",".join(_OP_COLUMNS) + " FROM finalization_operations WHERE operation_id=?",
                       [operation_id]).fetchone()
    if not row:
        return None
    value = dict(zip(_OP_COLUMNS, row))
    for key in ("plan_json", "reports_json", "report_formats", "complete_json"):
        value[key] = _loads(value[key])
    return value


def _operations(conn: ConnectionLike, project_id: str, request_id: str, environment: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT operation_id FROM finalization_operations WHERE project_id=? AND request_id=?"
    params: list[Any] = [project_id, request_id]
    if environment:
        sql += " AND environment=?"
        params.append(str(environment).upper())
    ids = [str(row[0]) for row in conn.execute(sql + " ORDER BY created_at DESC", params).fetchall()]
    return [operation for operation in (_operation(conn, value) for value in ids) if operation]


def _lock_scope(project_id: str, request_id: str) -> str:
    return f"final:{project_id}/{request_id}"


def _for_update(conn: ConnectionLike) -> str:
    return " FOR UPDATE" if getattr(conn, "backend", "duckdb") == "postgresql" else ""


_LOCKED_MESSAGE = "같은 의뢰의 다른 Final 지정이 드라이브에 반영되고 있습니다. 끝난 뒤 다시 시도하세요."


def _acquire_lock(conn: ConnectionLike, scope: str, owner: str) -> None:
    """Take (or refresh) the request's Final lock for ``owner`` in the caller's transaction.

    Review #6: the lock is held while its owner Final is ``PUBLISHING`` even after ``expires_at``
    (a queue paused for credentials longer than 30 minutes); a takeover is conditional on the row
    still being the one read (PostgreSQL also locks it ``FOR UPDATE``; DuckDB connections are
    serialized per process).
    """
    now = _now_db()
    expires = now + timedelta(minutes=LOCK_MINUTES)
    row = conn.execute("SELECT owner, expires_at FROM drive_locks WHERE scope=?" + _for_update(conn), [scope]).fetchone()
    if row is None:
        conn.execute("INSERT OR IGNORE INTO drive_locks (scope, owner, acquired_at, expires_at) VALUES (?,?,?,?)",
                     [scope, owner, now, expires])
        row = conn.execute("SELECT owner, expires_at FROM drive_locks WHERE scope=?" + _for_update(conn), [scope]).fetchone()
        if row is not None and str(row[0]) == owner:
            return
        if row is None:
            raise Error("FINALIZATION_LOCKED", _LOCKED_MESSAGE)
    holder, held_until = str(row[0]), row[1]
    if holder != owner:
        if isinstance(held_until, datetime) and held_until > now:
            raise Error("FINALIZATION_LOCKED", _LOCKED_MESSAGE)
        publishing = conn.execute("SELECT status FROM finalization_operations WHERE operation_id=?", [holder]).fetchone()
        if publishing is not None and str(publishing[0]) == "PUBLISHING":
            raise Error("FINALIZATION_LOCKED", _LOCKED_MESSAGE)
    taken = conn.execute("UPDATE drive_locks SET owner=?, acquired_at=?, expires_at=? WHERE scope=? AND owner=? "
                         "AND (owner=? OR expires_at<=?) RETURNING scope",
                         [owner, now, expires, scope, holder, owner, now]).fetchone()
    if taken is None:
        raise Error("FINALIZATION_LOCKED", _LOCKED_MESSAGE)


def acquire_retry_lock(conn: ConnectionLike, operation_id: str) -> None:
    """Admin queue retry of a Final batch item: the Final takes (or keeps) its request lock first.

    Re-review #3: a stopped (``FAILED``) Final becomes ``PUBLISHING`` in the same transaction as the lock is
    taken, so the lock is held under the PUBLISHING rule and the batch finish hook settles it (``FAILED`` and
    lock released, or ``COMPLETE``) when the retried batch stops again.  The caller's transaction rolls both
    back together when the retry itself is refused.
    """
    operation = _operation(conn, operation_id)
    if operation is None or operation["status"] == "COMPLETE":
        return
    _acquire_lock(conn, _lock_scope(operation["project_id"], operation["request_id"]), operation_id)
    conn.execute("UPDATE finalization_operations SET status='PUBLISHING', error_code=NULL, error_message=NULL, "
                 "updated_at=? WHERE operation_id=? AND status='FAILED'", [_now_db(), operation_id])


def _lock_designations(conn: ConnectionLike, project_id: str, request_id: str) -> None:
    """Serialize designation ``seq`` allocation of one request (review #5) until the caller commits.

    PostgreSQL: a transaction advisory lock (same pattern as result registration paths); a
    concurrent allocation waits, then reads the committed ``MAX``.  DuckDB: connections of the
    process are serialized already (one ``with connect()`` at a time).
    """
    if getattr(conn, "backend", "duckdb") == "postgresql":
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(?))", [f"final-designation:{project_id}/{request_id}"])


def _next_designation_seq(conn: ConnectionLike, project_id: str, request_id: str) -> int:
    """Next ``designation_seq`` of the request; call after :func:`_lock_designations`."""
    row = conn.execute("SELECT max(designation_seq) FROM finalization_operations WHERE project_id=? AND request_id=?",
                       [project_id, request_id]).fetchone()
    return int(row[0] or 0) + 1


def _release_lock(conn: ConnectionLike, owner: str) -> None:
    conn.execute("DELETE FROM drive_locks WHERE owner=?", [owner])


# --- preview ----------------------------------------------------------------------------------

def _drive_files(scope: dict[str, Any]) -> tuple[list[dict[str, Any]], int, list[str]]:
    """Local plan files plus each file's drive ``version_token``; unapplied drive changes are refused."""
    files, excluded, unchecked = cf._build_files(scope)
    fs = provider_for_root(scope["root"])
    pinned = []
    for item in files:
        source = str(item["source_relative_path"])
        if drive_reads.unapplied(source):
            # A pending/ignored change, a deleted source or a drive change no sync has classified yet:
            # the drive holds other bytes than the registered version the screens show, and
            # copy_within would copy the drive bytes.
            raise Error("FINALIZATION_SOURCE_STALE",
                        f"드라이브 원본 변경 확인이 필요한 파일이 있어 Final을 만들 수 없습니다(원본 변경 확인 후 다시 지정): {source}")
        entry = fs.stat(source)
        if entry is None or entry.kind != "file" or not entry.etag:
            raise Error("FINALIZATION_SOURCE_MISSING", f"드라이브 원본 파일을 찾을 수 없습니다: {source}")
        pinned.append({**item, "version_token": str(entry.etag)})
    return pinned, excluded, unchecked


def preview(conn: ConnectionLike, *, project_id: str, request_id: str, environment: str,
            case_id: str, capture_id: str, actor: str) -> dict[str, Any]:
    """Signed plan in the DB (nothing is written to the drive before confirm)."""
    scope = cf._scope(conn, project_id, request_id, environment, case_id, capture_id)
    final_relative, metadata_relative = cf._final_paths(scope)
    fs = provider_for_root(scope["root"])
    request_root = result_registration_paths._safe_existing(scope["root"], scope["scope"]["request_relative_path"])
    if not fs.is_dir(request_root):
        raise Error("FINALIZATION_REQUEST_FOLDER_INVALID", "확정된 의뢰 폴더를 찾을 수 없습니다.")
    if not spdm_storage._valid_windows_name(scope["case_label"]):
        raise Error("FINALIZATION_CASE_LABEL_INVALID", "Case 이름을 안전한 Windows 폴더 이름으로 사용할 수 없습니다.")
    report_files = cf.report_file_names(scope["case_label"])
    files, excluded_count, include_unchecked = _drive_files(scope)
    operation_id = uuid.uuid4().hex
    counts, missing = cf._counts(files)
    plan = {
        "schema_version": cf.PLAN_VERSION, "storage": STORAGE, "operation_id": operation_id, "status": "PREVIEW",
        "project_id": project_id, "request_id": request_id,
        "environment": str(environment).upper(), "case_id": case_id,
        "case_path": scope["case_path"], "case_label": scope["case_label"],
        "basis": scope["basis"], "capture_id": capture_id, "capture_fingerprint": scope["capture_fingerprint"],
        "scene_sources": scope["scene_sources"], "folder_schema_snapshot_id": scope["snapshot_id"],
        "scene_paths": sorted(set(scope["compatible_scene_paths"]), key=str.casefold),
        "excluded_scenes": scope["excluded_scenes"],
        "metadata_relative_path": f"{metadata_relative}/{operation_id}", "final_relative_path": final_relative,
        "created_by": actor, "previewed_at": cf._now(), "excluded_capture_file_count": excluded_count,
        "include_unchecked": include_unchecked, "counts": counts, "missing": missing, "files": files,
        "report_files": report_files, "root_key": scope["root_key"],
    }
    plan = cf._signed_record(plan, "plan_signature", cf.PLAN_DOMAIN)
    if len(cf._encode(plan)) > cf.MAX_PLAN_BYTES:
        raise Error("FINALIZATION_METADATA_LIMIT", "최종확정 계획이 메타데이터 크기 제한을 초과했습니다.")
    now = _now_db()
    conn.execute(
        "INSERT INTO finalization_operations (operation_id,root_key,project_id,request_id,environment,case_id,capture_id,"
        "status,plan_json,created_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,'PLANNED',?,?,?,?)",
        [operation_id, scope["root_key"], project_id, request_id, str(environment).upper(), case_id, capture_id,
         _dumps(plan), actor, now, now])
    output_paths = cf._expected_output_paths(plan)
    return {**plan, "plan_sha256": cf._plan_hash(plan), "can_confirm": bool(files), "output_paths": output_paths,
            "report_paths": {fmt: f"{output_paths['Reports']}/{name}" for fmt, name in report_files.items()},
            "report_limits": dict(cf.MAX_REPORT_BYTES),
            # The drive reports no free space; the server stages only reports (and fallback copies one file at a time).
            "disk": {"required_bytes": counts["total_bytes"], "margin_bytes": 0, "free_bytes": None, "sufficient": True},
            "drive": {"storage": STORAGE}}


# --- reports ----------------------------------------------------------------------------------

def _scoped_operation(conn: ConnectionLike, *, project_id: str, request_id: str, environment: str, case_id: str,
                      operation_id: str, capture_id: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    if not _OPERATION_ID.fullmatch(operation_id):
        raise Error("FINALIZATION_OPERATION_ID_INVALID", "최종확정 요청 ID가 올바르지 않습니다.")
    base_scope = cf._scope_for_status(conn, project_id, request_id, environment, case_id)
    operation = _operation(conn, operation_id)
    if operation is None:
        raise Error("FINALIZATION_PLAN_NOT_FOUND", "미리보기 계획을 찾을 수 없습니다. 새 미리보기를 만드세요.")
    if ((operation["project_id"], operation["request_id"], operation["environment"], operation["case_id"])
            != (project_id, request_id, str(environment).upper(), case_id)
            or (capture_id is not None and operation["capture_id"] != capture_id)):
        raise Error("FINALIZATION_OPERATION_SCOPE_MISMATCH", "최종확정 계획이 현재 Case 문맥과 일치하지 않습니다.")
    plan = operation["plan_json"]
    if not isinstance(plan, dict) or not cf._verify_signed_record(plan, "plan_signature", cf.PLAN_DOMAIN):
        raise Error("FINALIZATION_PLAN_UNTRUSTED", "저장된 최종확정 계획의 서명을 확인할 수 없습니다. 새 미리보기를 만드세요.")
    if operation["root_key"] != base_scope["root_key"]:
        raise Error("FINALIZATION_ROOT_CHANGED", "저장소 설정이 미리보기를 만든 시점과 다릅니다.")
    if plan.get("case_path") != base_scope["case_path"] or plan.get("case_label") != base_scope["case_label"]:
        raise Error("FINALIZATION_OPERATION_SCOPE_MISMATCH", "최종확정 계획이 현재 Case 문맥과 일치하지 않습니다.")
    return operation, base_scope


def check_report_target(conn: ConnectionLike, *, project_id: str, request_id: str, environment: str,
                        case_id: str, capture_id: str, operation_id: str) -> None:
    operation, _scope = _scoped_operation(conn, project_id=project_id, request_id=request_id, environment=environment,
                                          case_id=case_id, operation_id=operation_id, capture_id=capture_id)
    if operation["status"] == "COMPLETE":
        raise Error("FINALIZATION_ALREADY_COMPLETED", "이미 완료된 Final 지정입니다. 완료된 파일은 바꿀 수 없습니다.")


def stage_report(conn: ConnectionLike, *, project_id: str, request_id: str, environment: str,
                 case_id: str, capture_id: str, operation_id: str, report_format: str,
                 upload: BinaryIO, actor: str) -> dict[str, Any]:
    """Validate one report and keep it in server staging until confirm queues it (replaceable before confirm)."""
    operation, _scope = _scoped_operation(conn, project_id=project_id, request_id=request_id, environment=environment,
                                          case_id=case_id, operation_id=operation_id, capture_id=capture_id)
    if operation["status"] == "COMPLETE":
        raise Error("FINALIZATION_ALREADY_COMPLETED", "이미 완료된 Final 지정입니다. 완료된 파일은 바꿀 수 없습니다.")
    if operation["status"] == "PUBLISHING":
        raise Error("FINALIZATION_JOB_ACTIVE", "Final을 드라이브에 반영하는 중입니다. 끝난 뒤 다시 시도하세요.")
    plan = operation["plan_json"]
    checked = cf.validate_report(report_format, upload)
    file_name = plan["report_files"][report_format]
    reports = dict(operation["reports_json"] or {})
    previous = reports.get(report_format)
    if operation["upload_batch_id"] and previous and previous.get("sha256") != checked["sha256"]:
        # A queued attempt already used the staged report; the drive may hold it already (no overwrite).
        raise Error("FINALIZATION_REPORT_FORMATS_MISMATCH",
                    "이 Final 지정의 보고서는 이미 드라이브 반영을 시작했습니다. 같은 보고서로 다시 시도하세요.")
    folder = server_local.ensure_dir(_staging(operation_id) / "reports")
    temporary = folder / f".{uuid.uuid4().hex}.part"
    try:
        upload.seek(0)
        server_local.write_stream(temporary, upload)
        size, digest = server_local.file_sha256(temporary)
        if (digest, size) != (checked["sha256"], checked["size"]):
            raise Error("FINALIZATION_REPORT_UPLOAD_CHANGED", "보고서 업로드가 검사 중에 바뀌었습니다. 다시 올리세요.")
        server_local.install_file(temporary, folder / file_name)
    except OSError as exc:
        raise Error("FINALIZATION_WRITE_FAILED", "서버 임시 저장소에 보고서를 쓰지 못했습니다. 잠시 후 다시 시도하세요.") from exc
    finally:
        server_local.discard_file(temporary)
    reports[report_format] = {"file_name": file_name, "size": size, "sha256": digest, "staged_at": cf._now(),
                              "staged_by": actor}
    status = "STAGED" if operation["status"] == "PLANNED" else operation["status"]
    conn.execute("UPDATE finalization_operations SET reports_json=?, status=?, updated_at=? WHERE operation_id=?",
                 [_dumps(reports), status, _now_db(), operation_id])
    output_paths = cf._expected_output_paths(plan)
    return {"operation_id": operation_id, "format": report_format, "file_name": file_name, "size": size,
            "sha256": digest, "report_path": f"{output_paths['Reports']}/{file_name}", "status": "STAGED"}


# --- confirm ----------------------------------------------------------------------------------

def _staged_reports(operation: dict[str, Any], formats: list[str], *,
                    uploaded: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    """Staged report records; ``uploaded`` names (queue item already ``DONE``) need no staging file."""
    plan = operation["plan_json"]
    staged = operation["reports_json"] or {}
    reports: list[dict[str, Any]] = []
    for fmt in formats:
        entry = staged.get(fmt)
        if not isinstance(entry, dict) or entry.get("file_name") != plan["report_files"][fmt]:
            raise Error("FINALIZATION_REPORT_NOT_STAGED", f"{fmt.upper()} 보고서가 올라가지 않았습니다. 보고서를 다시 올리세요.")
        path = _staging(operation["operation_id"]) / "reports" / entry["file_name"]
        if entry["file_name"] in uploaded:
            reports.append({"format": fmt, "file_name": entry["file_name"], "size": entry["size"],
                            "sha256": entry["sha256"], "staging_path": str(path)})
            continue
        try:
            actual = server_local.file_sha256(path)
        except OSError as exc:
            raise Error("FINALIZATION_REPORT_STAGE_INVALID", "올린 보고서를 읽을 수 없습니다. 보고서를 다시 올리세요.") from exc
        if actual != (entry.get("size"), entry.get("sha256")):
            raise Error("FINALIZATION_REPORT_STAGE_INVALID", "올린 보고서가 기록과 다릅니다. 보고서를 다시 올리세요.")
        reports.append({"format": fmt, "file_name": entry["file_name"], "size": entry["size"], "sha256": entry["sha256"],
                        "staging_path": str(path)})
    if not cf._valid_report_records(plan, [{key: value for key, value in item.items() if key != "staging_path"}
                                           for item in reports]):
        raise Error("FINALIZATION_REPORT_STAGE_INVALID", "올린 보고서 기록이 올바르지 않습니다. 보고서를 다시 올리세요.")
    return reports


def _queue_items(operation: dict[str, Any], plan: dict[str, Any], reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    outputs = cf._expected_output_paths(plan)
    operation_dir = str(plan["metadata_relative_path"])
    folders = {outputs["CAE"], outputs["Reports"], operation_dir}
    for item in plan["files"]:
        parent = PurePosixPath(outputs["CAE"], str(item["case_relative_path"])).parent.as_posix()
        folders.add(parent)
    items: list[dict[str, Any]] = [{"kind": "MKDIR", "dst_rel_dir": folder}
                                   for folder in sorted(folders, key=lambda value: (value.count("/"), value.casefold()))]
    for item in plan["files"]:
        target = PurePosixPath(outputs["CAE"], str(item["case_relative_path"]))
        items.append({"kind": "COPY", "src_rel": str(item["source_relative_path"]),
                      "src_version_token": item.get("version_token"), "dst_rel_dir": target.parent.as_posix(),
                      "dst_name": target.name, "size_bytes": int(item["size"]), "sha256": item.get("sha256")})
    for report in reports:
        items.append({"kind": "FILE", "staging_path": report["staging_path"], "dst_rel_dir": outputs["Reports"],
                      "dst_name": report["file_name"], "size_bytes": report["size"], "sha256": report["sha256"]})
    plan_path = server_local.ensure_dir(_staging(operation["operation_id"])) / "plan.json"
    if not server_local.file_exists(plan_path):
        server_local.write_file(plan_path, cf._encode(plan))
    plan_size, plan_digest = server_local.file_sha256(plan_path)
    items.append({"kind": "FILE", "staging_path": str(plan_path), "dst_rel_dir": operation_dir, "dst_name": "plan.json",
                  "size_bytes": plan_size, "sha256": plan_digest})
    items.append({"kind": "COMPLETE_MARKER", "dst_rel_dir": operation_dir, "dst_name": "complete.json"})
    return items


def _restore_staged_plan(conn: ConnectionLike, operation: dict[str, Any], plan: dict[str, Any]) -> None:
    """Retry: rewrite a missing staged ``plan.json`` from ``plan_json`` (re-review #2).

    Staging cleanup may remove a stopped Final's folder; ``cf._encode`` is deterministic, so the rewritten file
    must match the sha256 the queue item recorded, otherwise the retry stops with a clear error.
    """
    operation_dir = str(plan["metadata_relative_path"])
    for item in upload_queue.batch_items(conn, operation["upload_batch_id"]):
        if item.kind != "FILE" or item.dst_name != "plan.json" or item.dst_rel_dir != operation_dir or item.state == "DONE":
            continue
        path = Path(item.staging_path) if item.staging_path else _staging(operation["operation_id"]) / "plan.json"
        if server_local.file_exists(path):
            continue
        server_local.ensure_dir(path.parent)
        server_local.write_file(path, cf._encode(plan))
        size, digest = server_local.file_sha256(path)
        if digest != item.sha256 or (item.size_bytes is not None and size != int(item.size_bytes)):
            server_local.discard_file(path)
            raise Error("FINALIZATION_PLAN_STAGE_INVALID",
                        "Final 계획 파일을 다시 만들 수 없습니다(기록과 다름). 새로 미리보기 후 다시 지정하세요.")


def confirm(conn: ConnectionLike, *, project_id: str, request_id: str, environment: str,
            case_id: str, capture_id: str, operation_id: str, actor: str,
            report_formats: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
    """Recheck the plan against the drive, lock the request and queue the whole Final as one batch."""
    if any(fmt not in cf.REPORT_FORMATS for fmt in report_formats):
        raise Error("FINALIZATION_REPORT_FORMAT_INVALID", "보고서 형식은 PPTX 또는 HTML이어야 합니다.")
    formats = [fmt for fmt in cf.REPORT_FORMATS if fmt in set(report_formats)]
    operation, _base = _scoped_operation(conn, project_id=project_id, request_id=request_id, environment=environment,
                                         case_id=case_id, operation_id=operation_id, capture_id=capture_id)
    if operation["status"] == "COMPLETE":
        return job_view(conn, operation)
    if operation["status"] == "PUBLISHING":
        return job_view(conn, operation)
    if not formats:
        raise Error("FINALIZATION_REPORT_REQUIRED", "PPTX 또는 HTML 보고서를 하나 이상 선택하세요.")
    plan = operation["plan_json"]
    if operation["upload_batch_id"]:
        # Retry of a stopped batch (same Final ID): only the stopped items run again.
        if list(operation["report_formats"] or []) != formats:
            raise Error("FINALIZATION_REPORT_FORMATS_MISMATCH",
                        "이 Final 지정은 다른 보고서 형식으로 드라이브 반영을 시작했습니다. 같은 형식으로 다시 시도하세요.")
        # Reports whose upload is already DONE need no staging file (review #2).
        reports_dir = cf._expected_output_paths(plan)["Reports"]
        uploaded = frozenset(str(item.dst_name) for item in upload_queue.batch_items(conn, operation["upload_batch_id"])
                             if item.kind == "FILE" and item.state == "DONE" and item.dst_rel_dir == reports_dir)
        _staged_reports(operation, formats, uploaded=uploaded)
        _restore_staged_plan(conn, operation, plan)
        _acquire_lock(conn, _lock_scope(project_id, request_id), operation_id)
        upload_queue.requeue_batch(conn, operation["upload_batch_id"])
        conn.execute("UPDATE finalization_operations SET status='PUBLISHING', error_code=NULL, error_message=NULL, "
                     "confirmed_by=?, updated_at=? WHERE operation_id=?", [actor, _now_db(), operation_id])
        return job_view(conn, _operation(conn, operation_id))
    scope = cf._scope(conn, project_id, request_id, environment, case_id, capture_id)
    expected_files, expected_excluded, expected_unchecked = _drive_files(scope)
    planned_tokens = {str(item["source_relative_path"]).casefold(): item.get("version_token") for item in plan["files"]}
    for item in expected_files:
        token = planned_tokens.get(str(item["source_relative_path"]).casefold())
        if token is not None and token != item["version_token"]:
            raise Error("FINALIZATION_SOURCE_STALE", f"미리보기 이후 드라이브 원본이 바뀌었습니다: {item['source_relative_path']}")
    cf._verify_plan_scope(plan, scope, operation_id, expected_files, expected_excluded, expected_unchecked)
    if not plan["files"]:
        raise Error("FINALIZATION_NO_FILES", "최종확정할 입력 또는 결과 파일이 없습니다.")
    reports = _staged_reports(operation, formats)
    _acquire_lock(conn, _lock_scope(project_id, request_id), operation_id)
    upload_queue.enqueue(conn, origin="finalization", origin_ref=operation_id, batch_id=operation_id,
                         requested_by=actor, project_id=project_id, request_id=request_id,
                         environment=str(environment).upper(), items=_queue_items(operation, plan, reports))
    now = _now_db()
    conn.execute("UPDATE finalization_operations SET status='PUBLISHING', report_formats=?, upload_batch_id=?, "
                 "confirmed_by=?, queued_at=?, error_code=NULL, error_message=NULL, updated_at=? WHERE operation_id=?",
                 [_dumps(formats), operation_id, actor, now, now, operation_id])
    return job_view(conn, _operation(conn, operation_id))


# --- queue hooks ------------------------------------------------------------------------------

def _next_confirmed_at(conn: ConnectionLike, project_id: str, request_id: str) -> str:
    last = None
    for row in conn.execute("SELECT confirmed_at FROM finalization_operations WHERE project_id=? AND request_id=? "
                            "AND confirmed_at IS NOT NULL", [project_id, request_id]).fetchall():
        parsed = cf._parse_time(row[0])
        if parsed is not None and (last is None or parsed > last):
            last = parsed
    return cf._next_confirmed_at({"last_confirmed_at": cf._format_time(last) if last else None})


def materialize_drive_item(item: upload_queue.Item) -> tuple[str, int, str]:
    """Build ``complete.json`` once every earlier item of the Final batch is DONE (queue COMPLETE_MARKER)."""
    operation_id = str(item.origin_ref)
    path = _staging(operation_id) / "complete.json"
    with connect() as conn:
        operation = _operation(conn, operation_id)
        if operation is None:
            raise Error("FINALIZATION_PLAN_NOT_FOUND", "Final 계획을 찾을 수 없습니다.")
        completed = operation["complete_json"]
        if not isinstance(completed, dict):
            plan = operation["plan_json"]
            done = {(row.dst_rel_dir, row.dst_name): row for row in upload_queue.batch_items(conn, item.batch_id)}
            outputs = cf._expected_output_paths(plan)
            files = []
            for planned in plan["files"]:
                target = PurePosixPath(outputs["CAE"], str(planned["case_relative_path"]))
                row = done.get((target.parent.as_posix(), target.name))
                files.append({**planned, "sha1": row.result_sha1 if row else None,
                              "transfer_method": row.transfer_method if row else None})
            reports = [{"format": fmt, "file_name": operation["reports_json"][fmt]["file_name"],
                        "size": operation["reports_json"][fmt]["size"], "sha256": operation["reports_json"][fmt]["sha256"]}
                       for fmt in operation["report_formats"] or []]
            completed = cf._signed_record({
                "schema_version": cf.PLAN_VERSION, "storage": STORAGE, "operation_id": operation_id, "status": "COMPLETE",
                "plan_sha256": cf._plan_hash(plan), "project_id": plan["project_id"], "request_id": plan["request_id"],
                "environment": plan["environment"], "case_id": plan["case_id"], "capture_id": plan["capture_id"],
                "capture_fingerprint": plan["capture_fingerprint"],
                "folder_schema_snapshot_id": plan["folder_schema_snapshot_id"],
                "output_paths": outputs, "files": files, "reports": reports,
                "counts": plan["counts"], "missing": plan["missing"],
                "created_by": operation["confirmed_by"] or operation["created_by"],
                "queued_at": _iso(operation["queued_at"]),
                "confirmed_at": _next_confirmed_at(conn, operation["project_id"], operation["request_id"]),
            }, "complete_signature", cf.COMPLETE_DOMAIN)
            data = cf._encode(completed)
            conn.execute("UPDATE finalization_operations SET complete_json=?, complete_sha256=?, confirmed_at=?, "
                         "updated_at=? WHERE operation_id=?",
                         [_dumps(completed), cf._digest(data), completed["confirmed_at"], _now_db(), operation_id])
    data = cf._encode(completed)
    if not server_local.file_exists(path) or server_local.file_sha256(path)[1] != cf._digest(data):
        server_local.ensure_dir(path.parent)
        temporary = path.with_name(f".{uuid.uuid4().hex}.part")
        server_local.write_file(temporary, data)
        server_local.install_file(temporary, path)
    return str(path), len(data), cf._digest(data)


def on_drive_batch_finished(origin: str, batch_id: str, summary: dict[str, Any]) -> None:
    if origin == "finalization":
        _finalization_finished(batch_id, summary)


def _finalization_finished(batch_id: str, summary: dict[str, Any]) -> None:
    with connect() as conn:
        operation = _operation(conn, batch_id)
        if operation is None or operation["status"] == "COMPLETE":
            return
        now = _now_db()
        if summary["state"] == "DONE" and isinstance(operation["complete_json"], dict):
            _lock_designations(conn, operation["project_id"], operation["request_id"])
            seq = _next_designation_seq(conn, operation["project_id"], operation["request_id"])
            if conn.execute("UPDATE finalization_operations SET status='COMPLETE', designation_seq=?, error_code=NULL, "
                            "error_message=NULL, updated_at=? WHERE operation_id=? AND status<>'COMPLETE' RETURNING operation_id",
                            [seq, now, batch_id]).fetchone() is None:
                return      # finished concurrently (sweep and worker)
            _release_lock(conn, batch_id)
            _queue_designation(conn, _operation(conn, batch_id), actor=operation["confirmed_by"] or operation["created_by"])
            return
        if operation["status"] != "PUBLISHING":
            return
        error = (summary.get("errors") or [{}])[0]
        conn.execute("UPDATE finalization_operations SET status='FAILED', error_code=?, error_message=?, updated_at=? "
                     "WHERE operation_id=? AND status='PUBLISHING'",
                     [error.get("code") or f"FINALIZATION_DRIVE_{summary['state']}", (error.get("message") or "")[:400],
                      now, batch_id])
        _release_lock(conn, batch_id)


# --- current Final summary: append-only designation files ---------------------------------------

def _current_by_environment(conn: ConnectionLike, project_id: str, request_id: str) -> dict[str, dict[str, Any]]:
    current: dict[str, dict[str, Any]] = {}
    for operation in _operations(conn, project_id, request_id):
        if operation["status"] != "COMPLETE" or operation["designation_seq"] is None:
            continue
        held = current.get(operation["environment"])
        if held is None or int(operation["designation_seq"]) > int(held["designation_seq"]):
            current[operation["environment"]] = operation
    return current


def _previous_final(conn: ConnectionLike, operation: dict[str, Any]) -> str | None:
    best = None
    for other in _operations(conn, operation["project_id"], operation["request_id"], operation["environment"]):
        if (other["status"] == "COMPLETE" and other["designation_seq"] is not None
                and other["operation_id"] != operation["operation_id"]
                and int(other["designation_seq"]) < int(operation["designation_seq"])
                and (best is None or int(other["designation_seq"]) > int(best["designation_seq"]))):
            best = other
    return best["operation_id"] if best else None


def designation_name(seq: int, operation_id: str) -> str:
    return f"{int(seq):0{DESIGNATION_SEQ_DIGITS}d}-{operation_id}.json"


def _queue_designation(conn: ConnectionLike, operation: dict[str, Any], *, actor: str, seq: int | None = None) -> str:
    """Upload a new ``designations/<seq>-<Final ID>.json`` (never replaces an earlier one)."""
    plan = operation["plan_json"]
    sequence = int(seq if seq is not None else operation["designation_seq"])
    current = _current_by_environment(conn, operation["project_id"], operation["request_id"])
    current[operation["environment"]] = operation
    environments = {}
    for environment, held in sorted(current.items()):
        held_plan = held["plan_json"]
        entry = cf._summary_entry(held_plan, held["complete_json"], _previous_final(conn, held), held["complete_sha256"])
        environments[environment] = entry
    record = {
        "format": cf.SUMMARY_FORMAT, "schema_version": cf.SUMMARY_SCHEMA_VERSION, "storage": STORAGE,
        "generated_at": cf._now(), "designation_seq": sequence, "final_id": operation["operation_id"],
        "environment": operation["environment"], "environments": environments,
        "note": "가장 큰 designation_seq 파일이 현재 Final이다(append-only, 경로는 Final 폴더 기준).",
    }
    batch_id = upload_queue.new_batch_id()
    folder = server_local.ensure_dir(upload_queue.staging_dir_for(batch_id))
    name = designation_name(sequence, operation["operation_id"])
    path = folder / name
    server_local.write_file(path, json.dumps(record, ensure_ascii=False, indent=1).encode("utf-8"))
    size, digest = server_local.file_sha256(path)
    designations = f"{plan['final_relative_path']}/.finalizations/{DESIGNATIONS_FOLDER}"
    upload_queue.enqueue(conn, origin="final_designation", origin_ref=operation["operation_id"], batch_id=batch_id,
                         requested_by=actor, project_id=operation["project_id"], request_id=operation["request_id"],
                         environment=operation["environment"], items=[
                             {"kind": "MKDIR", "dst_rel_dir": designations},
                             {"kind": "FILE", "staging_path": str(path), "dst_rel_dir": designations, "dst_name": name,
                              "size_bytes": size, "sha256": digest},
                         ])
    conn.execute("UPDATE finalization_operations SET designation_batch_id=?, updated_at=? WHERE operation_id=?",
                 [batch_id, _now_db(), operation["operation_id"]])
    return batch_id


# --- views ------------------------------------------------------------------------------------

def _record(operation: dict[str, Any]) -> dict[str, Any] | None:
    completed = operation["complete_json"]
    if operation["status"] != "COMPLETE" or not isinstance(completed, dict):
        return None
    plan = operation["plan_json"]
    response = cf._completed_response(plan, completed)
    return {**response, "verification": VERIFICATION_LABEL, "storage": STORAGE}


def job_view(conn: ConnectionLike, operation: dict[str, Any] | None) -> dict[str, Any]:
    if operation is None:
        raise Error("FINALIZATION_JOB_NOT_FOUND", "Final 복사 작업을 찾을 수 없습니다.")
    plan = operation["plan_json"]
    files_total = len(plan.get("files") or [])
    bytes_total = sum(int(item.get("size") or 0) for item in plan.get("files") or [])
    summary = upload_queue.batch_summary(conn, operation["upload_batch_id"]) if operation["upload_batch_id"] else None
    staged = operation["reports_json"] or {}
    reports = [{"format": fmt, "file_name": staged[fmt]["file_name"], "size": staged[fmt]["size"], "sha256": staged[fmt]["sha256"]}
               for fmt in operation["report_formats"] or [] if fmt in staged]
    record = _record(operation)
    copies = files_done = bytes_done = 0
    phase = None
    current = None
    if summary is not None:
        with_items = upload_queue.batch_items(conn, operation["upload_batch_id"])
        copies = [item for item in with_items if item.kind == "COPY"]
        files_done = sum(1 for item in copies if item.state == "DONE")
        bytes_done = sum(int(item.size_bytes or 0) for item in copies if item.state == "DONE")
        current_item = next((item for item in with_items if item.state in {"RUNNING", "PENDING", "BLOCKED"}), None)
        if current_item is not None:
            phase = "COPYING" if current_item.kind in {"MKDIR", "COPY"} else "PUBLISHING"
            current = current_item.target
    if operation["status"] == "COMPLETE":
        state, error = "COMPLETE", None
        files_done, bytes_done, phase, current = files_total, bytes_total, None, None
    elif operation["status"] == "FAILED":
        state = "FAILED"
        error = {"code": operation["error_code"] or "FINALIZATION_DRIVE_FAILED",
                 "message": operation["error_message"] or "드라이브 반영이 멈췄습니다. 다시 시도하거나 관리자에게 문의하세요."}
    elif operation["status"] == "PUBLISHING":
        state = "QUEUED" if summary is None or summary["state"] == "QUEUED" else "RUNNING"
        error = None
        if summary is not None and summary["paused"]:
            error = {"code": "DRIVE_AUTH_REQUIRED", "message": upload_queue.ERROR_MESSAGES["DRIVE_AUTH_REQUIRED"]}
    else:
        state, error = "PLANNED", None
    return {
        "operation_id": operation["operation_id"], "state": state, "phase": phase,
        "files_done": files_done, "files_total": files_total, "bytes_done": bytes_done, "bytes_total": bytes_total,
        "current_file": current, "error": error, "attempt": None, "queued_at": _iso(operation["queued_at"]),
        "started_at": _iso(operation["queued_at"]), "updated_at": _iso(operation["updated_at"]),
        "case_id": operation["case_id"], "capture_id": operation["capture_id"], "reports": reports,
        "output_paths": cf._expected_output_paths(plan), "active": state in {"QUEUED", "RUNNING"},
        "record": record, "storage": STORAGE, "drive": summary,
    }


def job_status(conn: ConnectionLike, *, project_id: str, request_id: str, environment: str,
               case_id: str, operation_id: str) -> dict[str, Any]:
    try:
        operation, _scope = _scoped_operation(conn, project_id=project_id, request_id=request_id,
                                              environment=environment, case_id=case_id, operation_id=operation_id)
    except Error as exc:
        if exc.code in {"FINALIZATION_PLAN_NOT_FOUND", "FINALIZATION_OPERATION_SCOPE_MISMATCH"}:
            raise Error("FINALIZATION_JOB_NOT_FOUND", "Final 복사 작업을 찾을 수 없습니다.") from exc
        raise
    if not operation["upload_batch_id"] and operation["status"] != "COMPLETE":
        raise Error("FINALIZATION_JOB_NOT_FOUND", "Final 복사 작업을 찾을 수 없습니다.")
    return job_view(conn, operation)


def _summary_state(conn: ConnectionLike, current: dict[str, Any] | None, final_relative: str) -> dict[str, Any]:
    path = f"{final_relative}/.finalizations/{DESIGNATIONS_FOLDER}"
    if current is None:
        return {"state": "NONE", "path": path, "final_id": None}
    batch = upload_queue.batch_summary(conn, current["designation_batch_id"]) if current["designation_batch_id"] else None
    name = designation_name(int(current["designation_seq"]), current["operation_id"])
    if batch is None or batch["state"] in {"FAILED", "CONFLICT", "CANCELLED", "PARTIAL"}:
        state = "MISSING"
    elif batch["state"] == "DONE":
        state = "OK"
    else:
        state = "PENDING"
    return {"state": state, "path": f"{path}/{name}", "final_id": current["operation_id"] if state == "OK" else None,
            "designation_seq": current["designation_seq"], "drive": batch}


def status(conn: ConnectionLike, *, project_id: str, request_id: str, environment: str, case_id: str) -> dict[str, Any]:
    """Same response shape as the local status, from the DB (no drive scan, no output hashing)."""
    scope = cf._scope_for_status(conn, project_id, request_id, environment, case_id)
    final_relative, _metadata = cf._final_paths(scope)
    env = str(environment).upper()
    operations = [item for item in _operations(conn, project_id, request_id, env) if item["root_key"] == scope["root_key"]]
    completed = sorted((item for item in operations if _record(item)),
                       key=lambda item: str(item["confirmed_at"] or ""), reverse=True)
    latest = _record(completed[0]) if completed else None
    selected = next((_record(item) for item in completed if item["case_id"] == case_id), None)
    retryable = []
    for item in operations:
        if item["case_id"] != case_id or item["status"] == "COMPLETE":
            continue
        view = job_view(conn, item) if item["upload_batch_id"] else None
        retryable.append({"operation_id": item["operation_id"], "status": "RETRYABLE", "capture_id": item["capture_id"],
                          "previewed_at": item["plan_json"].get("previewed_at"), "job": view})
    retryable.sort(key=lambda row: str(row["previewed_at"] or ""), reverse=True)
    active = sorted((row["job"] for row in retryable if row.get("job") and row["job"]["state"] in {"QUEUED", "RUNNING", "FAILED"}),
                    key=lambda job: str(job.get("queued_at") or ""), reverse=True)
    designated = sorted((item for item in completed if item["designation_seq"] is not None),
                        key=lambda item: int(item["designation_seq"]), reverse=True)
    current_operation = designated[0] if designated else None
    current = None
    if current_operation is not None:
        record = _record(current_operation)
        current = {"operation_id": record["operation_id"], "case_id": record["case_id"], "case_label": record["case_label"],
                   "case_path": record["case_path"], "designated_by": record.get("created_by"),
                   "designated_at": record.get("confirmed_at"), "schema_version": record.get("schema_version"),
                   "output_paths": record.get("output_paths"), "verified": True, "verification": VERIFICATION_LABEL,
                   "missing": False, "designation_seq": current_operation["designation_seq"]}
    history = [{"operation_id": item["operation_id"], "case_id": item["case_id"], "case_label": item["plan_json"]["case_label"],
                "designated_by": (item["complete_json"] or {}).get("created_by"),
                "designated_at": (item["complete_json"] or {}).get("confirmed_at"),
                "schema_version": item["plan_json"].get("schema_version"),
                "role": "CURRENT" if current and item["operation_id"] == current["operation_id"] else "PREVIOUS"}
               for item in designated[:cf.MAX_STATUS_HISTORY]]
    return {"case_id": case_id, "request_id": request_id, "environment": env, "final_relative_path": final_relative,
            "latest": latest, "selected_case_latest": selected, "retryable_operations": retryable,
            "active_operations": active, "current_final": current, "final_history": history,
            "summary": _summary_state(conn, current_operation, final_relative), "unverified_records": 0,
            "storage": STORAGE}


def repair_summary(conn: ConnectionLike, *, project_id: str, request_id: str, environment: str,
                   case_id: str, actor: str, override: bool = False) -> dict[str, Any]:
    """Queue a new designation file (next ``seq``) for the current Final; earlier files stay as they are."""
    del override  # nothing on the drive is replaced in scx mode
    scope = cf._scope_for_status(conn, project_id, request_id, environment, case_id)
    env = str(environment).upper()
    # Review #5: serialize with other allocations first, then read the current state (a concurrent
    # repair of the same Final sees the batch the first one queued and queues nothing).
    _lock_designations(conn, project_id, request_id)
    current = _current_by_environment(conn, project_id, request_id).get(env)
    if current is None or current["root_key"] != scope["root_key"]:
        raise Error("FINALIZATION_NO_CURRENT", "완료된 Final이 없습니다.")
    batch = upload_queue.batch_summary(conn, current["designation_batch_id"]) if current["designation_batch_id"] else None
    if batch is not None and not batch["finished"]:
        return status(conn, project_id=project_id, request_id=request_id, environment=environment, case_id=case_id)
    seq = _next_designation_seq(conn, project_id, request_id)
    conn.execute("UPDATE finalization_operations SET designation_seq=?, updated_at=? WHERE operation_id=?",
                 [seq, _now_db(), current["operation_id"]])
    _queue_designation(conn, _operation(conn, current["operation_id"]), actor=actor, seq=seq)
    return status(conn, project_id=project_id, request_id=request_id, environment=environment, case_id=case_id)


def latest_completed(conn: ConnectionLike, *, project_id: str, request_id: str,
                     environment: str) -> tuple[dict[str, Any] | None, list[str] | None]:
    """Request progress (P5): newest completed Final and its report file names, from the DB."""
    try:
        _scope_info = result_registration_paths._scope(conn, project_id, request_id, environment)
        _root, _root_id, root_key = result_registration_paths.storage_context(conn)
    except result_registration_paths.ResultRegistrationError as exc:
        raise Error(exc.code, str(exc)) from exc
    completed = sorted((item for item in _operations(conn, project_id, request_id, environment)
                        if item["root_key"] == root_key and _record(item)),
                       key=lambda item: str(item["confirmed_at"] or ""), reverse=True)
    if not completed:
        return None, []
    record = _record(completed[0])
    return record, [str(item["file_name"]) for item in (record or {}).get("reports") or []]


__all__ = ["acquire_retry_lock", "active", "check_report_target", "confirm", "designation_name", "job_status", "latest_completed",
           "materialize_drive_item", "on_drive_batch_finished", "preview", "repair_summary", "stage_report", "status"]
