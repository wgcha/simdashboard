"""Global administrator 「폴더 등록 전체 초기화」: reset every folder registration (user request 2026-10-23).

Why: after 등록 이력 [전체 삭제] and 프로젝트 정리 [강제 삭제] a production SCX drive server still showed old
folder spellings (``PROJ-000099_R95J 개발 과제`` next to the drive's ``PROJ-000099_R95J개발과제``, stale
``…/Drop/85qn80h_ref_organized/INDIVIDUAL`` paths) and never discovered ``…/WR-00000800_유통환경시뮬레이션``.
Both deletes keep path state on purpose (tombstones, scan history, SCX drive source versions, legacy links);
``docs/contracts/depth-schema.md`` §16.8 lists every such table and which reader brought the old paths back.
This operation removes all of it in one step so that the next [저장 폴더 새로고침] registers exactly the
folders that exist now.

Scope (preview counts every table; the delete recomputes the plan and compares a confirm token)
    * Folder-derived projects -- every project that a folder registration, folder link or folder-derived row
      references (live or DELETED registrations and their ``created_targets``, registry targets, project
      folder links, link reservations, scans, SPDM parent bindings, the old folder registry, old result
      mappings, Cases, result registration paths) or whose id is a folder-derived id
      (``environment-project-…``) -- are deleted with everything below them exactly as 프로젝트 정리 does
      (``project_cleanup._STAGES``: requests, load cases, runs and results, Cases/captures/assets, analysis
      pages, workflow history, notifications, media rows and blobs referenced only by them). A project
      without any folder link (made by hand, demo) is kept.
    * Folder-path tables are emptied entirely, tombstones included (``WIPED_TABLES``): registrations,
      registry, capture jobs, scans/previews/refresh snapshots, project folder links, link reservations,
      result registration drafts/files/events/paths/location links, SCX drive source versions, the old
      folder registry with its scans/previews, SPDM storage bindings/files/parents, old result mappings
      (``semantic_folder_bindings`` and their review items).
    * ``folder_discovery_exclusions`` (administrator exclusions) is kept unless ``keep_exclusions`` is false.
    * Kept: accounts, roles, permissions, the depth schema (``folder_environment_profiles``), drive
      credentials/settings, ``spdm_storage_settings``, legacy folder rules, global catalogs, audit events,
      the drive upload queue (write log, D3), Final operations (drive metadata), blob files on disk
      (content-addressed cache).
    * In-memory state of this process is forgotten after the commit: the discovery result and its
      needs-review/retry memos, the auto-sync memos, the folder progress memo, the drive spelling memo and
      the last rescan result. Other worker processes forget theirs at their next run (their memos only
      throttle; every decision re-reads the database).

Safety
    * Refused (409 ``FOLDER_RESET_BUSY``) while a rescan, a discovery run or a capture pass runs, while a
      drive upload is pending/running/blocked or a Final operation runs. The discovery and capture run locks
      are held for the whole delete, so neither starts meanwhile.
    * ``PROJECT_TEMPLATE_IN_USE`` (a project template bound by a profile that is kept) blocks
      (409 ``FOLDER_RESET_BLOCKED``); executions left "running" are overridden as in a force cleanup.
    * The administrator types ``초기화`` (``CONFIRM_TEXT``); 409 ``DELETE_PREVIEW_STALE`` when anything
      changed after the preview. PostgreSQL: one transaction with table locks. DuckDB commits the FK levels
      in order as the other deletes do; a retry recomputes the plan and converges.
    * Files on the SPDM storage / SCX drive are never touched: this module does no file system or drive access.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import ExitStack, contextmanager
from typing import Any, Callable, Iterator

from fastapi import HTTPException

from . import folder_discovery as legacy
from . import project_cleanup as cleanup
from .folder_environment_deletion import _Schema, _marks, _postgres, _select

CONFIRM_TEXT = "초기화"
# Tables emptied entirely, by stage (child-first for every PostgreSQL foreign key). Stage i runs together
# with ``project_cleanup._STAGES[i]`` (leaves, captures, Cases); ``_LATE`` runs before the last project stage.
WIPED_TABLES: tuple[tuple[str, ...], ...] = (
    ("folder_environment_capture_jobs", "folder_environment_registry", "result_registration_events",
     "result_registration_files", "result_registration_drafts", "result_registration_location_links",
     "result_registration_paths", "drive_source_versions", "folder_link_reservations", "folder_project_links",
     "folder_discovery_registry", "folder_discovery_previews", "spdm_storage_files", "spdm_storage_bindings",
     "spdm_storage_request_parents", "materials_preparations"),
    ("folder_environment_registrations", "spdm_storage_project_parents", "folder_discovery_scans"),
    ("folder_environment_previews",),
)
_LATE = ("folder_environment_scans",)
EXCLUSIONS_TABLE = "folder_discovery_exclusions"
# Kept on purpose (documented in the preview so the screen can say what stays).
KEPT = ("users/roles/permissions", "folder_environment_profiles (깊이 스키마)", "drive_credentials", "drive_connected_account",
        "spdm_storage_settings", "folder_discovery_rules", "folder_discovery_catalog", "audit_events",
        "drive_upload_queue", "finalization_operations", "blob store files")
# Rows whose folder-derived id prefix marks a project made by folder registration/discovery.
_PROJECT_ID_PREFIXES = ("environment-project-",)
# (sql, kind): every query returns project ids (``project``) or request ids (``request``) that a folder row references.
_REFERENCES: tuple[tuple[str, str, str], ...] = (
    ("folder_environment_registrations", "SELECT project_id FROM folder_environment_registrations WHERE project_id IS NOT NULL", "project"),
    ("folder_environment_registrations", "SELECT request_id FROM folder_environment_registrations WHERE request_id IS NOT NULL", "request"),
    ("folder_environment_registry", "SELECT target_id FROM folder_environment_registry WHERE role_kind='PROJECT'", "project"),
    ("folder_environment_registry", "SELECT target_id FROM folder_environment_registry WHERE role_kind='REQUEST'", "request"),
    ("folder_environment_scans", "SELECT project_id FROM folder_environment_scans WHERE project_id IS NOT NULL", "project"),
    ("folder_environment_scans", "SELECT request_id FROM folder_environment_scans WHERE request_id IS NOT NULL", "request"),
    ("folder_project_links", "SELECT project_id FROM folder_project_links", "project"),
    ("folder_link_reservations", "SELECT project_id FROM folder_link_reservations", "project"),
    ("spdm_storage_project_parents", "SELECT project_id FROM spdm_storage_project_parents", "project"),
    ("spdm_storage_request_parents", "SELECT project_id FROM spdm_storage_request_parents", "project"),
    ("spdm_storage_bindings", "SELECT project_id FROM spdm_storage_bindings", "project"),
    ("semantic_folder_bindings", "SELECT project_id FROM semantic_folder_bindings", "project"),
    ("folder_discovery_registry", "SELECT target_id FROM folder_discovery_registry WHERE role_kind='PROJECT'", "project"),
    ("folder_discovery_registry", "SELECT parent_target_id FROM folder_discovery_registry WHERE role_kind='REQUEST' "
                                  "AND parent_target_id IS NOT NULL", "project"),
    ("folder_discovery_registry", "SELECT target_id FROM folder_discovery_registry WHERE role_kind='REQUEST'", "request"),
    ("dashboard_cases", "SELECT project_id FROM dashboard_cases WHERE project_id IS NOT NULL", "project"),
    ("result_registration_paths", "SELECT project_id FROM result_registration_paths WHERE project_id IS NOT NULL", "project"),
    ("result_registration_location_links", "SELECT project_id FROM result_registration_location_links "
                                           "WHERE project_id IS NOT NULL", "project"),
)
BUSY_LABELS = {
    "RESCAN_RUNNING": "저장 폴더 새로고침이 실행 중입니다.",
    "DISCOVERY_RUNNING": "새 폴더 확인이 실행 중입니다.",
    "CAPTURE_RUNNING": "결과 받기(캡처)가 실행 중입니다.",
    "UPLOAD_PENDING": "드라이브 업로드 대기열에 끝나지 않은 항목이 있습니다.",
    "FINAL_RUNNING": "Final 지정 작업이 실행 중입니다.",
}


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

def _folder_projects(conn, schema: _Schema) -> set[str]:
    """Existing projects that any folder row references (module docstring)."""
    projects: set[str] = set()
    requests: set[str] = set()
    for table, sql, kind in _REFERENCES:
        column = sql.split()[1]
        if not schema.has(table, column):
            continue
        (projects if kind == "project" else requests).update(str(row[0]) for row in _select(conn, sql, []) if row[0])
    if schema.has("folder_environment_registrations", "created_targets"):
        for (raw,) in _select(conn, "SELECT created_targets FROM folder_environment_registrations "
                                    "WHERE created_targets IS NOT NULL", []):
            try:
                targets = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
            except (TypeError, ValueError):
                continue
            if isinstance(targets, dict):
                projects.update(str(value) for value in targets.get("project_ids") or [] if value)
                requests.update(str(value) for value in targets.get("request_ids") or [] if value)
    existing = {str(row[0]): str(row[1] or "") for row in _select(conn, "SELECT id,project_id FROM analysis_requests", [])}
    projects.update(existing[request] for request in requests if request in existing and existing[request])
    found = {str(row[0]) for row in _select(conn, "SELECT id FROM projects", [])}
    projects.update(project for project in found if project.startswith(_PROJECT_ID_PREFIXES))
    return projects & found


def _template_blockers(conn, schema: _Schema, projects: set[str]) -> list[dict]:
    """Project templates of deleted projects still bound by a profile that is kept (PROJECT_TEMPLATE_IN_USE)."""
    if not projects or not schema.has("analysis_template_versions", "project_id", "template_id"):
        return []
    owners: dict[str, str] = {}
    for chunk in cleanup._chunks(projects):
        for template_id, project_id in _select(conn, "SELECT DISTINCT template_id,project_id FROM analysis_template_versions "
                                                     f"WHERE project_id IN ({_marks(chunk)})", chunk):
            owners[str(template_id)] = str(project_id)
    found: list[dict] = []
    for table, column in (("request_type_result_profiles", None), ("project_request_type_result_profiles", "project_id")):
        if not owners or not schema.has(table, "template_id"):
            continue
        for chunk in cleanup._chunks(owners):
            rows = _select(conn, f"SELECT template_id{',' + column if column else ''} FROM {table} "
                                 f"WHERE template_id IN ({_marks(chunk)})", chunk)
            for row in rows:
                if column is None or str(row[1]) not in projects:
                    found.append({"table": table, "id": str(row[0]), "project_id": owners.get(str(row[0])),
                                  "reason": "PROJECT_TEMPLATE_IN_USE"})
    unique = {(item["table"], item["id"]): item for item in found}
    return [unique[key] for key in sorted(unique)]


def _entities(conn, schema: _Schema, projects: set[str]) -> dict[str, set[str]]:
    """``project_cleanup`` entities of the projects plus every row of the wiped folder tables they key on."""
    e = cleanup._entities(conn, schema, projects)
    if schema.has("semantic_folder_bindings", "id"):
        e["binding"] |= {str(row[0]) for row in _select(conn, "SELECT id FROM semantic_folder_bindings", [])}
        e["review_item"] |= cleanup._ids(conn, schema, "semantic_import_review_items", "id", [("binding_id", e["binding"])])
    return e


def _count_all(conn, schema: _Schema, table: str) -> int:
    return int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]) if schema.has(table) else 0


def _project_counts(conn, schema: _Schema, e: dict[str, set[str]]) -> tuple[dict[str, int], bool]:
    """Rows the project stages delete, by table; ``approximate`` when the id lists are too long for one query."""
    if sum(len(values) for values in e.values()) <= cleanup.MAX_PARAMS:
        return cleanup._counts(conn, schema, e), False
    counts: dict[str, int] = {}
    for stage in cleanup._STAGES:
        for table, pairs in stage:
            if table.startswith("*"):
                continue
            for column, kind in pairs:   # summed per column: a row matching two columns counts twice
                if schema.has(table, column):
                    n = cleanup._count_in(conn, schema, table, column, e.get(kind, set()))
                    if n:
                        counts[table] = counts.get(table, 0) + n
    return counts, True


def _busy(conn, schema: _Schema) -> list[dict]:
    """Running work that the reset must not race (module docstring); no lock is taken here."""
    from . import capture_jobs, case_finalization_jobs, folder_auto_discovery, storage_rescan
    from .drive import upload_queue

    found = []
    if storage_rescan.running():
        found.append("RESCAN_RUNNING")
    if folder_auto_discovery._run_lock.locked():
        found.append("DISCOVERY_RUNNING")
    if capture_jobs._run_lock.locked():
        found.append("CAPTURE_RUNNING")
    pending = 0
    if schema.has("drive_upload_queue", "state"):
        states = list(upload_queue.OPEN_STATES)
        pending = int(conn.execute(f"SELECT count(*) FROM drive_upload_queue WHERE state IN ({_marks(states)})",
                                   states).fetchone()[0])
    if pending or upload_queue._executing:
        found.append("UPLOAD_PENDING")
    with case_finalization_jobs._lock:
        if case_finalization_jobs._running or case_finalization_jobs._queued:
            found.append("FINAL_RUNNING")
    return [{"code": code, "message": BUSY_LABELS[code]} for code in found]


def _plan(conn, keep_exclusions: bool) -> dict:
    schema = _Schema(conn)
    projects = _folder_projects(conn, schema)
    e = _entities(conn, schema, projects)
    counts, approximate = _project_counts(conn, schema, e)
    wiped = [table for stage in WIPED_TABLES for table in stage] + list(_LATE)
    if not keep_exclusions:
        wiped.append(EXCLUSIONS_TABLE)
    # Wiped tables lose every row; NULL updates of their columns are moot.
    counts = {key: value for key, value in counts.items() if key.split(".", 1)[0] not in wiped}
    for table in wiped:
        n = _count_all(conn, schema, table)
        if n:
            counts[table] = n
        else:
            counts.pop(table, None)
    names = {str(row[0]): str(row[1] or "") for row in _select(conn, "SELECT id,name FROM projects", [])}
    blockers = _template_blockers(conn, schema, projects)
    # Executions left "running" (a stopped PC) are overridden as in a force cleanup: not a blocker here.
    state = {"projects": sorted(projects), "counts": counts, "keep_exclusions": bool(keep_exclusions),
             "blockers": [[b["table"], b["id"], b["reason"]] for b in blockers]}
    token = hashlib.sha256(json.dumps(state, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return {
        "projects": [{"project_id": project_id, "name": names.get(project_id, project_id)}
                     for project_id in sorted(projects, key=lambda value: (names.get(value, value).casefold(), value))],
        "counts": counts, "counts_approximate": approximate,
        "exclusions": _count_all(conn, schema, EXCLUSIONS_TABLE), "keep_exclusions": bool(keep_exclusions),
        "kept": list(KEPT), "blockers": blockers, "confirm_token": token, "confirm_text": CONFIRM_TEXT,
        "entities": e, "schema": schema, "wiped": wiped,
    }


def _public(plan: dict, busy: list[dict]) -> dict:
    return {key: plan[key] for key in ("projects", "counts", "counts_approximate", "exclusions", "keep_exclusions",
                                       "kept", "blockers", "confirm_token", "confirm_text")} | {"busy": busy}


def preview(conn, keep_exclusions: bool = True) -> dict:
    """Read-only plan: folder-derived projects, row counts per table, blockers, running work, confirm token."""
    plan = _plan(conn, keep_exclusions)
    return _public(plan, _busy(conn, plan["schema"]))


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

@contextmanager
def _quiet_workers() -> Iterator[None]:
    """Hold the discovery and capture run locks (non-blocking): neither starts while the reset deletes."""
    from . import capture_jobs, folder_auto_discovery

    with ExitStack() as stack:
        for code, lock in (("DISCOVERY_RUNNING", folder_auto_discovery._run_lock), ("CAPTURE_RUNNING", capture_jobs._run_lock)):
            if not lock.acquire(blocking=False):
                raise HTTPException(409, {"code": "FOLDER_RESET_BUSY", "busy": [{"code": code, "message": BUSY_LABELS[code]}],
                                          "message": "실행 중인 작업이 있어 초기화하지 않았습니다. 끝난 뒤 다시 시도하세요."})
            stack.callback(lock.release)
        yield


def _wipe(conn, schema: _Schema, tables) -> None:
    for table in tables:
        if schema.has(table):
            conn.execute(f"DELETE FROM {table}")


def forget_memory() -> None:
    """Forget the in-memory folder state of this process (module docstring)."""
    from . import folder_auto_discovery, folder_auto_sync, folder_request_progress, storage_rescan
    from .drive import reads as drive_reads

    folder_auto_discovery.forget_all()
    folder_auto_sync.forget_all()
    folder_request_progress.clear_memo()
    storage_rescan.forget()
    drive_reads.forget_spellings()


def reset(conn, confirm_token: str, confirmation_text: str, *, keep_exclusions: bool = True,
          audit: Callable[[Any, dict], None] | None = None) -> dict:
    """Delete every folder registration and folder-derived row (module docstring) in one operation."""
    if not isinstance(confirm_token, str) or not confirm_token:
        raise ValueError("confirm_token이 필요합니다.")
    if str(confirmation_text or "").strip() != CONFIRM_TEXT:
        raise HTTPException(409, {"code": "FOLDER_RESET_CONFIRM_REQUIRED",
                                  "message": f"확인 문구 '{CONFIRM_TEXT}'를 입력해야 초기화합니다. 아무것도 삭제하지 않았습니다."})
    postgres = _postgres(conn)
    with ExitStack() as stack:
        busy = _busy(conn, _Schema(conn))
        if busy:
            raise HTTPException(409, {"code": "FOLDER_RESET_BUSY", "busy": busy,
                                      "message": "실행 중인 작업이 있어 초기화하지 않았습니다. 끝난 뒤 다시 시도하세요."})
        stack.enter_context(_quiet_workers())
        stack.enter_context(legacy.WRITE_LOCK)
        conn.execute("BEGIN TRANSACTION")
        try:
            if postgres:
                conn.execute("LOCK TABLE projects, analysis_requests, load_cases, folder_environment_registrations, "
                             "folder_environment_scans, drive_source_versions, drive_upload_queue IN SHARE ROW EXCLUSIVE MODE")
            current = _plan(conn, keep_exclusions)
            if current["confirm_token"] != confirm_token:
                raise HTTPException(409, {"code": "DELETE_PREVIEW_STALE",
                                          "message": "미리보기 이후 데이터가 바뀌었습니다. 다시 확인하세요."})
            if current["blockers"]:
                raise HTTPException(409, {"code": "FOLDER_RESET_BLOCKED", "blockers": current["blockers"],
                                          "message": "다른 곳에서 쓰는 프로젝트 템플릿이 있어 아무것도 삭제하지 않았습니다."})
            schema, e = current["schema"], current["entities"]
            stages = list(cleanup._STAGES)
            for index, stage in enumerate(stages):
                if index == len(stages) - 1:
                    _wipe(conn, schema, _LATE)
                    if not postgres:
                        conn.execute("COMMIT")
                        conn.execute("BEGIN TRANSACTION")
                cleanup._execute(conn, schema, stage, e)
                if index < len(WIPED_TABLES):
                    _wipe(conn, schema, WIPED_TABLES[index])
                if index == 0 and not keep_exclusions:
                    _wipe(conn, schema, (EXCLUSIONS_TABLE,))
                if not postgres and index < len(stages) - 1:
                    # DuckDB: a parent row cannot be deleted in the transaction that deleted its FK children.
                    conn.execute("COMMIT")
                    conn.execute("BEGIN TRANSACTION")
            deleted = sorted(e["project"])
            cleanup._remember_demo_removed(conn, deleted)
            result = {"deleted_projects": deleted, "counts": current["counts"],
                      "counts_approximate": current["counts_approximate"], "keep_exclusions": bool(keep_exclusions)}
            if audit is not None:
                audit(conn, {"project_ids": deleted, "counts": current["counts"], "keep_exclusions": bool(keep_exclusions)})
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    forget_memory()
    return result
