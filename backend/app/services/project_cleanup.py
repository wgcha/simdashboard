"""Administrator project cleanup: demo projects and projects without a live registration.

Contract: ``docs/contracts/depth-schema.md`` §16 (프로젝트 정리) and the D15
exception. Plan: ``docs/plans/workspace-cleanup.md`` §1.

Candidates
    * ``DEMO``: the fixed example project ids in ``DEMO_PROJECT_IDS`` (never by name).
    * ``EMPTY``: no live (non-DELETED) folder-environment registration references
      the project, its requests or its Cases.
    * ``REGISTERED``: a live registration references it. Not selectable; the
      administrator uses registration delete (§13) instead.

Scope
    Everything hanging off the selected projects is deleted child-first in
    ``_STAGES`` (no cascade): requests, load cases, runs and their results,
    steps/work items/plans, workflow/batch/managed execution history,
    validations, analysis pages and versions, Cases/captures/assets, result
    registration drafts/files/events and path links, project-scoped settings,
    vocabulary, legacy links (``folder_discovery_registry``, ``spdm_storage_*``,
    ``semantic_folder_bindings`` = D15 exception) and media blobs referenced
    only by deleted rows. Scan history and DELETED registration tombstones keep
    their row but lose the project/request link (NULL). Accounts, audit events
    and global catalogs are kept. ``KEEP_COLUMNS`` documents every other
    project/request/load-case reference column with the reason it is kept.

    SPDM folders and files are never touched (D14): this module performs no
    filesystem access at all.

Safety
    Preview returns a ``confirm_token`` over the planned rows and blockers. The
    delete recomputes the plan under ``folder_discovery.WRITE_LOCK`` (PostgreSQL:
    one transaction with table locks) and rejects a stale token (409
    ``DELETE_PREVIEW_STALE``) or any blocker (409 ``PROJECT_CLEANUP_BLOCKED``)
    without changing anything. DuckDB cannot delete a parent row in the
    transaction that deleted its foreign-key children, so the embedded
    development backend commits the dashboard FK levels in order (same as
    §13); a retry recomputes the plan and converges.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import ExitStack
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import HTTPException

from . import folder_discovery as legacy
from .folder_environment_deletion import _Schema, _marks, _postgres, _select

MAX_IDS = 200
MAX_PARAMS = 30000
DEMO_PROJECT_IDS = ("project-tv-001", "project-feature-showcase", "project-d2f8298b56ce")
# Stored in the existing key/value table ``spdm_storage_settings`` (no migration):
# a JSON list of demo project ids an administrator removed. ``database.ensure_default_content``
# does not recreate those examples (DuckDB development bootstrap and the explicit seed command).
DEMO_REMOVED_SETTING = "demo_projects_removed"
SYSTEM_ANALYSIS_PAGE_IDS = frozenset({"dashboard-drop-default", "dashboard-chassis-default", "dashboard-run-comparison-default"})
# Rows written by the example seed; they are not counted as "직접 만든 데이터".
DEMO_SEED_ROW_IDS = frozenset({
    *(f"validation-showcase-{key}" for key in ("compare", "trust", "review", "multitype")),
    *(f"annotation-showcase-review-{n}" for n in (1, 2, 3)),
    *(f"bookmark-showcase-review-{n}" for n in (1, 2, 3)),
    "note-drop-001",
})
ACTIVE_STATUSES = frozenset({"QUEUED", "PENDING", "RUNNING", "IN_PROGRESS", "AWAITING_COMPLETION", "DISPATCHED",
                             "STARTING", "CANCELLING", "CAPTURING"})
VOCABULARY_TARGETS = {"PROJECT": "project", "REQUEST": "request", "LOAD_CASE": "load_case"}

# (table, [(column, entity kind)]) rows matching any pair are deleted. Order is child-first for
# every PostgreSQL foreign key; "*null:<n>" entries clear a reference instead (see _NULLS).
_STAGE_LEAVES = (
    ("semantic_import_review_events", (("review_item_id", "review_item"),)),
    ("semantic_import_review_items", (("id", "review_item"),)),
    ("semantic_import_provenance", (("analysis_run_id", "run"), ("load_case_id", "load_case"))),
    ("semantic_folder_bindings", (("id", "binding"),)),
    ("semantic_vocabulary_terms", (("entry_id", "vocab_entry"),)),
    ("semantic_vocabulary_entries", (("id", "vocab_entry"),)),
    ("spdm_storage_files", (("load_case_id", "load_case"), ("run_id", "run"))),
    ("spdm_storage_bindings", (("project_id", "project"), ("request_id", "request"), ("load_case_id", "load_case"))),
    ("spdm_storage_request_parents", (("project_id", "project"), ("request_id", "request"),
                                      ("project_folder", "spdm_project_folder"))),
    ("spdm_storage_project_parents", (("project_id", "project"),)),
    ("folder_discovery_registry", (("id", "legacy_registry"),)),
    ("folder_environment_capture_jobs", (("id", "capture_job"),)),
    ("folder_environment_registry", (("id", "env_registry"),)),
    ("result_registration_events", (("draft_id", "draft"),)),
    ("result_registration_files", (("draft_id", "draft"),)),
    ("result_registration_drafts", (("id", "draft"),)),
    ("result_registration_location_links", (("project_id", "project"), ("request_id", "request"))),
    ("result_registration_paths", (("project_id", "project"), ("request_id", "request"))),
    ("dashboard_assets", (("capture_id", "capture"),)),
    ("dashboard_versions", (("dashboard_id", "dashboard"),)),
    ("dashboards", (("id", "dashboard"),)),
    ("validations", (("id", "validation"),)),
    ("review_annotations", (("analysis_run_id", "run"), ("bookmark_id", "bookmark"))),
    ("result_bookmarks", (("id", "bookmark"),)),
    ("result_locations", (("analysis_run_id", "run"),)),
    ("qualitative_notes", (("analysis_run_id", "run"),)),
    ("scalar_results", (("analysis_run_id", "run"),)),
    ("time_series_results", (("analysis_run_id", "run"),)),
    ("curve_points", (("curve_id", "curve"),)),
    ("curve_results", (("id", "curve"),)),
    ("analysis_run_metadata", (("analysis_run_id", "run"),)),
    ("media_assets", (("id", "media_asset"),)),
    ("canonical_result_ingestion_source_versions", (("load_case_id", "load_case"), ("analysis_run_id", "run"))),
    ("folder_import_jobs", (("load_case_id", "load_case"), ("analysis_run_id", "run"))),
    ("drop_video_assets", (("video_id", "drop_video"),)),
    ("variable_definitions", (("load_case_id", "load_case"),)),
    ("task_run_events", (("task_run_id", "task_run"),)),
    ("task_runs", (("id", "task_run"),)),
    ("batch_execution_events", (("attempt_id", "attempt"),)),
    ("batch_dispatches", (("id", "dispatch"),)),
    ("*null:workflow_attempt", ()),
    ("*null:work_item_demo_run", ()),
    ("batch_execution_attempts", (("id", "attempt"),)),
    ("managed_device_event_sequences", (("run_id", "managed_run"),)),
    ("managed_local_runs", (("id", "managed_run"),)),
    ("managed_device_grants", (("id", "grant"),)),
    ("workflow_runs", (("id", "workflow_run"),)),
    ("request_work_items", (("request_id", "request"),)),
    ("request_work_plans", (("request_id", "request"),)),
    ("analysis_request_type_assignments", (("request_id", "request"),)),
    ("request_result_layout_snapshots", (("request_id", "request"),)),
    ("request_steps", (("request_id", "request"),)),
    ("analysis_runs", (("id", "run"),)),
    ("template_executions", (("load_case_id", "load_case"),)),
    ("load_cases", (("id", "load_case"),)),
    ("product_information", (("project_id", "project"),)),
    ("project_memberships", (("project_id", "project"),)),
    ("project_invitations", (("project_id", "project"),)),
    ("quality_thresholds", (("project_id", "project"),)),
    ("project_workspace_layout_versions", (("project_id", "project"),)),
    ("project_workspace_layouts", (("project_id", "project"),)),
    ("project_request_type_result_profiles", (("project_id", "project"),)),
    ("analysis_template_versions", (("project_id", "project"),)),
    # Derived, user-facing notifications (migration 0040) go with their project/request.
    ("notifications", (("project_id", "project"), ("request_id", "request"))),
)
_STAGES = (
    _STAGE_LEAVES,
    (("dashboard_captures", (("id", "capture"),)),),
    (("dashboard_cases", (("id", "case"),)),),
    (("*null:scans", ()), ("*null:tombstones", ()),
     ("asset_blob_chunks", (("blob_id", "blob"),)), ("asset_blobs", (("id", "blob"),)),
     ("analysis_requests", (("id", "request"),)), ("projects", (("id", "project"),))),
)
# name -> (table, column set to NULL, [(match column, entity kind)], extra predicate)
_NULLS = {
    "workflow_attempt": ("workflow_runs", "batch_attempt_id", (("id", "workflow_run"), ("batch_attempt_id", "attempt")), ""),
    "work_item_demo_run": ("request_work_items", "demo_run_id", (("demo_run_id", "workflow_run"),), ""),
    "scans": [("folder_environment_scans", "project_id", (("project_id", "project"),), ""),
              ("folder_environment_scans", "request_id", (("request_id", "request"),), "")],
    "tombstones": [("folder_environment_registrations", "project_id", (("project_id", "project"),), "status='DELETED'"),
                   ("folder_environment_registrations", "request_id", (("request_id", "request"),), "status='DELETED'")],
}

# Reference columns (project/request/load-case/run/case/target ids) that cleanup deliberately leaves alone.
KEEP_COLUMNS = {
    ("audit_events", "request_id"): "HTTP request correlation id, not an analysis request; audit is never deleted",
    ("modeling_templates", "load_case_name"): "global modeling template catalog; free-text load case name",
    ("request_type_result_profiles", "request_type_id"): "global request-type catalog",
    ("request_type_result_profiles", "request_type_version"): "global request-type catalog",
    ("analysis_request_type_assignments", "request_type_id"): "catalog reference of a deleted request row",
    ("analysis_request_type_assignments", "request_type_version"): "catalog reference of a deleted request row",
    ("project_request_type_result_profiles", "request_type_id"): "catalog reference of a deleted project row",
    ("project_request_type_result_profiles", "request_type_version"): "catalog reference of a deleted project row",
    ("request_result_layout_snapshots", "source_request_type_id"): "catalog reference of a deleted request row",
    ("request_result_layout_snapshots", "source_request_type_version"): "catalog reference of a deleted request row",
    ("request_work_plans", "request_type_id"): "catalog reference of a deleted request row",
    ("request_work_plans", "request_type_version"): "catalog reference of a deleted request row",
    ("request_work_plans", "requested_by"): "actor name, not an entity id",
    ("workflow_runs", "request_type_id"): "catalog reference of a deleted run row",
    ("workflow_runs", "request_type_version"): "catalog reference of a deleted run row",
    ("analysis_requests", "requested_at"): "timestamp column",
    ("folder_environment_registrations", "created_targets"): "tombstone JSON history (D16); ids there are not live links",
    ("drive_source_versions", "project_id"): "SCX drive file version history keyed by root_key+path; scope label re-assigned by the next sync",
    ("drive_source_versions", "request_id"): "SCX drive file version history keyed by root_key+path; scope label re-assigned by the next sync",
    ("drive_upload_queue", "project_id"): "SCX drive write log (D3); the drive files it wrote are never deleted (W4), so the record stays",
    ("drive_upload_queue", "request_id"): "SCX drive write log (D3); the drive files it wrote are never deleted (W4), so the record stays",
    ("finalization_operations", "project_id"): "SCX drive Final metadata (D3); the Final on the drive is kept (W4: SPDM files unchanged)",
    ("finalization_operations", "request_id"): "SCX drive Final metadata (D3); the Final on the drive is kept (W4: SPDM files unchanged)",
    ("finalization_operations", "case_id"): "SCX drive Final metadata (D3); the Final on the drive is kept (W4: SPDM files unchanged)",
    ("finalization_operations", "capture_id"): "SCX drive Final metadata (D3); the Final on the drive is kept (W4: SPDM files unchanged)",
    ("folder_discovery_registry", "parent_target_id"): "closure in _entities deletes every row below a deleted target",
    ("folder_discovery_registry", "target_id"): "closure in _entities deletes every row targeting a deleted entity",
    ("folder_environment_registry", "target_id"): "rows of DELETED registrations targeting deleted entities are deleted",
    ("folder_environment_capture_jobs", "case_id"): "jobs of DELETED registrations on deleted Cases are deleted",
    ("folder_environment_capture_jobs", "capture_id"): "jobs of DELETED registrations on deleted captures are deleted",
    ("folder_environment_capture_jobs", "load_case_id"): "jobs of DELETED registrations on deleted load cases are deleted",
    ("folder_environment_capture_jobs", "run_case_id"): "folder-derived Case key inside a job row",
    ("semantic_vocabulary_entries", "target_id"): "PROJECT/REQUEST/LOAD_CASE targets of deleted entities are deleted",
    ("semantic_import_review_items", "load_case_id"): "review items of deleted load cases are deleted",
    ("semantic_import_review_items", "previous_confirmed_analysis_run_id"): "same row as its load case",
    ("semantic_import_review_items", "confirmed_analysis_run_id"): "same row as its load case",
    ("semantic_import_review_events", "prior_run_id"): "deleted with its review item",
    ("semantic_import_review_events", "current_run_id"): "deleted with its review item",
    ("canonical_result_ingestion_source_versions", "supersedes_analysis_run_id"): "same row as its load case",
    ("canonical_result_ingestion_source_versions", "source_run_id"): "external source identifier",
    ("folder_import_jobs", "replaced_analysis_run_id"): "same row as its load case",
    ("folder_import_jobs", "source_run_id"): "external source identifier",
    ("validations", "project_id"): "validations of deleted projects/requests/load cases/runs are deleted",
    ("validations", "request_id"): "see validations.project_id",
    ("validations", "load_case_id"): "see validations.project_id",
    ("validations", "analysis_run_id"): "see validations.project_id",
    ("dashboards", "project_id"): "analysis pages of deleted projects/requests/load cases are deleted",
    ("dashboards", "request_id"): "see dashboards.project_id",
    ("dashboards", "load_case_id"): "see dashboards.project_id",
    ("dashboard_cases", "project_id"): "Cases of deleted projects/requests are deleted",
    ("dashboard_cases", "request_id"): "see dashboard_cases.project_id",
    ("dashboard_captures", "case_id"): "captures of deleted Cases are deleted",
    ("result_registration_drafts", "project_id"): "drafts of deleted projects/requests/Cases/captures are deleted",
    ("result_registration_drafts", "request_id"): "see result_registration_drafts.project_id",
    ("result_registration_drafts", "case_id"): "see result_registration_drafts.project_id",
    ("result_registration_drafts", "capture_id"): "see result_registration_drafts.project_id",
    ("semantic_folder_bindings", "project_id"): "bindings of deleted projects/requests/load cases are deleted",
    ("semantic_folder_bindings", "request_id"): "see semantic_folder_bindings.project_id",
    ("semantic_folder_bindings", "load_case_id"): "see semantic_folder_bindings.project_id",
    ("analysis_runs", "load_case_id"): "runs of deleted load cases are deleted",
    ("media_assets", "analysis_run_id"): "media of deleted runs are deleted",
    ("drop_video_assets", "load_case_id"): "videos of deleted load cases are deleted",
    ("batch_execution_attempts", "work_item_id"): "attempts of deleted work items/workflow runs are deleted",
    ("batch_dispatches", "work_item_id"): "dispatches of deleted work items/workflow runs/attempts are deleted",
    ("managed_device_grants", "request_id"): "grants of deleted requests/work items are deleted",
    ("managed_device_grants", "work_item_id"): "see managed_device_grants.request_id",
    ("managed_local_runs", "request_id"): "runs of deleted requests/work items are deleted",
    ("managed_local_runs", "work_item_id"): "see managed_local_runs.request_id",
    ("workflow_runs", "request_id"): "workflow runs of deleted requests are deleted",
    ("load_cases", "request_id"): "load cases of deleted requests are deleted",
    ("analysis_requests", "project_id"): "requests of deleted projects are deleted",
    ("folder_environment_scans", "project_id"): "set to NULL (scan history keeps its row)",
    ("folder_environment_scans", "request_id"): "set to NULL (scan history keeps its row)",
    ("folder_environment_registrations", "project_id"): "DELETED tombstones: set to NULL; live registration blocks",
    ("folder_environment_registrations", "request_id"): "DELETED tombstones: set to NULL; live registration blocks",
    ("managed_device_event_sequences", "run_id"): "sequences of deleted managed runs are deleted",
    ("spdm_storage_request_parents", "project_folder"): "parents under a deleted project folder are deleted",
    ("spdm_storage_project_parents", "project_folder"): "primary key of a deleted parent row",
    ("spdm_storage_request_parents", "request_folder"): "primary key of a deleted parent row",
    ("batch_execution_attempts", "workflow_run_id"): "attempts of deleted workflow runs are deleted (_entities)",
    ("batch_dispatches", "workflow_run_id"): "dispatches of deleted workflow runs are deleted (_entities)",
    ("task_runs", "workflow_run_id"): "task runs of deleted workflow runs are deleted (_entities)",
    ("curve_results", "analysis_run_id"): "curves of deleted runs are deleted (_entities)",
    ("result_bookmarks", "analysis_run_id"): "bookmarks of deleted runs are deleted (_entities)",
    ("semantic_vocabulary_entries", "scope_project_id"): "project-scoped vocabulary is deleted (_entities)",
    ("result_registration_paths", "target_id"): "folder-role mapping row deleted with its project/request",
    ("result_registration_location_links", "schema_target_id"): "location link row deleted with its project/request",
    # Foreign keys into deleted tables (inventory test L2).
    ("analysis_runs", "template_execution_id"): "runs of a deleted load case are deleted before its template executions",
    ("batch_dispatches", "attempt_id"): "dispatches of deleted attempts are deleted first (_entities)",
    ("media_assets", "blob_id"): "a blob is deleted only when no surviving media row references it",
    ("drop_video_assets", "blob_id"): "a blob is deleted only when no surviving video row references it",
    ("managed_local_runs", "grant_id"): "grants of deleted managed runs are deleted after the runs (_entities)",
    ("project_request_type_result_profiles", "template_id"): "deleted with its project; another project's binding blocks (PROJECT_TEMPLATE_IN_USE)",
    ("project_request_type_result_profiles", "template_version"): "see project_request_type_result_profiles.template_id",
    ("request_type_result_profiles", "template_id"): "global binding of a project template blocks (PROJECT_TEMPLATE_IN_USE)",
    ("request_type_result_profiles", "template_version"): "see request_type_result_profiles.template_id",
    ("semantic_import_review_items", "binding_id"): "review items of deleted bindings are deleted (_entities)",
}


def handled_columns() -> set[tuple[str, str]]:
    """Every (table, column) a delete or NULL step matches on (used by the inventory test)."""
    found = {(table, column) for stage in _STAGES for table, pairs in stage for column, _kind in pairs if not table.startswith("*")}
    for value in _NULLS.values():
        for table, _target, pairs, _extra in (value if isinstance(value, list) else [value]):
            found |= {(table, column) for column, _kind in pairs}
    return found


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _validated_ids(project_ids) -> list[str]:
    if not isinstance(project_ids, list) or not 1 <= len(project_ids) <= MAX_IDS:
        raise ValueError(f"project_ids는 1~{MAX_IDS}개여야 합니다.")
    cleaned: list[str] = []
    for value in project_ids:
        if not isinstance(value, str) or not value or len(value) > 200:
            raise ValueError("프로젝트 id 형식이 올바르지 않습니다.")
        if value not in cleaned:
            cleaned.append(value)
    return cleaned


_IN_CHUNK = 500


def _chunks(values) -> list[list[str]]:
    ordered = sorted(values)
    return [ordered[start:start + _IN_CHUNK] for start in range(0, len(ordered), _IN_CHUNK)]


def _select_in(conn, sql: str, values, *, repeat: int = 1) -> list[tuple]:
    """Run ``sql`` (with ``{marks}`` placeholders, ``repeat`` times) once per bounded chunk of ``values``."""
    rows: list[tuple] = []
    for chunk in _chunks(values):
        rows += _select(conn, sql.format(marks=_marks(chunk)), chunk * repeat)
    return rows


def _ids(conn, schema: _Schema, table: str, out: str, filters, where: str = "") -> set[str]:
    """Distinct ``out`` values of rows matching any (column, values) filter; IN lists are chunked."""
    if not schema.has(table, out):
        return set()
    found: set[str] = set()
    for column, values in filters:
        if values and schema.has(table, column):
            sql = f"SELECT DISTINCT {out} FROM {table} WHERE {column} IN ({{marks}})" + (f" AND {where}" if where else "")
            found |= {str(row[0]) for row in _select_in(conn, sql, values) if row[0] is not None}
    return found


def _count_in(conn, schema: _Schema, table: str, column: str, values) -> int:
    if not values or not schema.has(table, column):
        return 0
    return sum(int(conn.execute(f"SELECT count(*) FROM {table} WHERE {column} IN ({_marks(chunk)})", chunk).fetchone()[0])
               for chunk in _chunks(values))


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _live_targets(conn, schema: _Schema) -> set[str]:
    """Ids that a live registration references (projects, requests, Cases, registry targets)."""
    targets: set[str] = set()
    live = _select(conn, "SELECT id,project_id,request_id FROM folder_environment_registrations WHERE status<>'DELETED'", [])
    ids = [str(row[0]) for row in live]
    for _id, project_id, request_id in live:
        targets.update(str(v) for v in (project_id, request_id) if v)
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        targets.update(str(r[0]) for r in _select(conn, f"SELECT target_id FROM folder_environment_registry WHERE registration_id IN ({_marks(chunk)})", chunk))
        targets.update(str(r[0]) for r in _select(conn, f"SELECT case_id FROM folder_environment_capture_jobs WHERE registration_id IN ({_marks(chunk)})", chunk))
    return targets


def _project_rows(conn) -> list[tuple]:
    return _select(conn, "SELECT id,name,created_at FROM projects ORDER BY name,id", [])


def _children(conn, schema: _Schema, projects: set[str]) -> dict[str, set[str]]:
    requests = _ids(conn, schema, "analysis_requests", "id", [("project_id", projects)])
    cases = _ids(conn, schema, "dashboard_cases", "id", [("project_id", projects), ("request_id", requests)])
    return {"project": set(projects), "request": requests, "case": cases}


def _category(project_id: str, children: dict[str, set[str]], live: set[str]) -> str:
    if live & ({project_id} | children["request"] | children["case"]):
        return "REGISTERED"
    return "DEMO" if project_id in DEMO_PROJECT_IDS else "EMPTY"


def _user_data(conn, schema: _Schema, entities: dict[str, set[str]]) -> dict[str, int]:
    """Rows a person probably created (warning only; seeded example rows are excluded)."""
    def count(kind: str, table: str, exclude=frozenset()) -> int:
        return len(entities.get(kind, set()) - exclude) if schema.has(table) else 0
    return {key: value for key, value in {
        "analysis_pages": count("dashboard", "dashboards", exclude=SYSTEM_ANALYSIS_PAGE_IDS),
        "workflow_runs": count("workflow_run", "workflow_runs"),
        "batch_executions": count("attempt", "batch_execution_attempts"),
        "managed_runs": count("managed_run", "managed_local_runs"),
        "validations": count("validation", "validations", exclude=DEMO_SEED_ROW_IDS),
        "review_notes": count("bookmark", "result_bookmarks", exclude=DEMO_SEED_ROW_IDS)
        + len(_ids(conn, schema, "review_annotations", "id", [("analysis_run_id", entities.get("run", set()))]) - DEMO_SEED_ROW_IDS)
        + len(_ids(conn, schema, "qualitative_notes", "id", [("analysis_run_id", entities.get("run", set()))]) - DEMO_SEED_ROW_IDS),
        "result_drafts": count("draft", "result_registration_drafts"),
    }.items() if value}


def _retained(conn, schema: _Schema, e: dict[str, set[str]]) -> dict[str, int]:
    """Imported/registered data kept so far ("보관 데이터"): results, Cases, legacy links and media."""
    results = sum(_count_in(conn, schema, table, "analysis_run_id", e.get("run", set()))
                  for table in ("scalar_results", "time_series_results", "curve_results"))
    legacy_links = len(e.get("legacy_registry", set())) + len(e.get("binding", set()))
    for table in ("spdm_storage_bindings", "spdm_storage_project_parents", "spdm_storage_request_parents"):
        if schema.has(table):
            where, args = _where((("project_id", "project"), ("request_id", "request"), ("load_case_id", "load_case")), e, schema, table)
            if where:
                legacy_links += int(conn.execute(f"SELECT count(*) FROM {table} WHERE {where}", args).fetchone()[0])
    return {key: value for key, value in {
        "runs": len(e.get("run", set())),
        "results": results,
        "cases": len(e.get("case", set())),
        "captures": len(e.get("capture", set())),
        "legacy_links": legacy_links,
        "media": len(e.get("media_asset", set())) + len(e.get("drop_video", set())),
    }.items() if value}


def _system_pages(conn, schema: _Schema, e: dict[str, set[str]]) -> list[dict]:
    """System analysis pages hosted by these projects; deleting the project deletes them for every load case."""
    pages = sorted(e.get("dashboard", set()) & SYSTEM_ANALYSIS_PAGE_IDS)
    found = []
    for page_id in pages:
        name = conn.execute("SELECT name FROM dashboards WHERE id=?", [page_id]).fetchone()
        # Authors 'system' / 'system-…' (seed and backfills such as system-video-grid-backfill) are not users.
        # A NULL author cannot be attributed to a person either, so it counts as system (not "사용자 수정").
        versions = conn.execute("SELECT count(*), count(*) FILTER (WHERE created_by IS NOT NULL AND created_by NOT LIKE 'system%') "
                                "FROM dashboard_versions WHERE dashboard_id=?", [page_id]).fetchone() \
            if schema.has("dashboard_versions", "created_by") else (0, 0)
        found.append({"id": page_id, "name": str(name[0]) if name else page_id,
                      "versions": int(versions[0] or 0), "user_versions": int(versions[1] or 0)})
    return found


def _acknowledgement(category: str, retained: dict, user_data: dict, system_pages: list[dict]) -> list[str]:
    """Why the administrator must type the project name before deleting (empty: no second confirmation)."""
    reasons = []
    if category != "DEMO" and retained:
        reasons.append("RETAINED_DATA")
    if user_data or any(page["user_versions"] for page in system_pages):
        reasons.append("USER_DATA")
    if system_pages:
        reasons.append("SYSTEM_ANALYSIS_PAGES")
    return reasons


def _describe(conn, schema: _Schema, category: str, e: dict[str, set[str]]) -> dict:
    user_data = _user_data(conn, schema, e)
    retained = _retained(conn, schema, e)
    system_pages = _system_pages(conn, schema, e)
    reasons = _acknowledgement(category, retained, user_data, system_pages)
    return {"user_data": user_data, "user_data_total": sum(user_data.values()),
            "retained_data": retained, "retained_total": sum(retained.values()),
            "system_pages": system_pages, "acknowledge_reasons": reasons, "requires_acknowledgement": bool(reasons)}


def _entities(conn, schema: _Schema, projects: set[str]) -> dict[str, set[str]]:
    e: dict[str, set[str]] = _children(conn, schema, projects)
    e["load_case"] = _ids(conn, schema, "load_cases", "id", [("request_id", e["request"])])
    e["run"] = _ids(conn, schema, "analysis_runs", "id", [("load_case_id", e["load_case"])])
    e["curve"] = _ids(conn, schema, "curve_results", "id", [("analysis_run_id", e["run"])])
    e["bookmark"] = _ids(conn, schema, "result_bookmarks", "id", [("analysis_run_id", e["run"])])
    e["media_asset"] = _ids(conn, schema, "media_assets", "id", [("analysis_run_id", e["run"])])
    e["drop_video"] = _ids(conn, schema, "drop_video_assets", "video_id", [("load_case_id", e["load_case"])])
    e["validation"] = _ids(conn, schema, "validations", "id", [("project_id", projects), ("request_id", e["request"]),
                                                              ("load_case_id", e["load_case"]), ("analysis_run_id", e["run"])])
    e["dashboard"] = _ids(conn, schema, "dashboards", "id", [("project_id", projects), ("request_id", e["request"]),
                                                            ("load_case_id", e["load_case"])])
    e["capture"] = _ids(conn, schema, "dashboard_captures", "id", [("case_id", e["case"])])
    e["draft"] = _ids(conn, schema, "result_registration_drafts", "id", [
        ("project_id", projects), ("request_id", e["request"]), ("case_id", e["case"]), ("capture_id", e["capture"])])
    e["workflow_run"] = _ids(conn, schema, "workflow_runs", "id", [("request_id", e["request"])])
    e["task_run"] = _ids(conn, schema, "task_runs", "id", [("workflow_run_id", e["workflow_run"])])
    e["work_item"] = _ids(conn, schema, "request_work_items", "id", [("request_id", e["request"])])
    e["attempt"] = _ids(conn, schema, "batch_execution_attempts", "id", [("work_item_id", e["work_item"]),
                                                                        ("workflow_run_id", e["workflow_run"])])
    e["dispatch"] = _ids(conn, schema, "batch_dispatches", "id", [("work_item_id", e["work_item"]),
                                                                 ("workflow_run_id", e["workflow_run"]), ("attempt_id", e["attempt"])])
    e["managed_run"] = _ids(conn, schema, "managed_local_runs", "id", [("request_id", e["request"]), ("work_item_id", e["work_item"])])
    e["grant"] = _ids(conn, schema, "managed_device_grants", "id", [("request_id", e["request"]), ("work_item_id", e["work_item"])])
    e["grant"] |= _ids(conn, schema, "managed_local_runs", "grant_id", [("id", e["managed_run"])])
    e["binding"] = _ids(conn, schema, "semantic_folder_bindings", "id", [
        ("project_id", projects), ("request_id", e["request"]), ("load_case_id", e["load_case"])])
    e["review_item"] = _ids(conn, schema, "semantic_import_review_items", "id", [("binding_id", e["binding"]),
                                                                                ("load_case_id", e["load_case"])])
    e["vocab_entry"] = _ids(conn, schema, "semantic_vocabulary_entries", "id", [("scope_project_id", projects)])
    for target_kind, kind in VOCABULARY_TARGETS.items():
        e["vocab_entry"] |= _ids(conn, schema, "semantic_vocabulary_entries", "id", [("target_id", e[kind])],
                                 where=f"target_kind='{target_kind}'")
    e["spdm_project_folder"] = _ids(conn, schema, "spdm_storage_project_parents", "project_folder", [("project_id", projects)])
    # Legacy folder-discovery registry: rows targeting a deleted entity and everything registered below them.
    legacy_rows: set[str] = set()
    frontier = projects | e["request"] | e["load_case"] | e["run"]
    seen: set[str] = set()
    while frontier:
        seen |= frontier
        found = _select_in(conn, "SELECT id,target_id FROM folder_discovery_registry WHERE target_id IN ({marks}) "
                                 "OR parent_target_id IN ({marks})", frontier, repeat=2) \
            if schema.has("folder_discovery_registry", "parent_target_id") else []
        legacy_rows |= {str(r[0]) for r in found}
        frontier = {str(r[1]) for r in found if r[1] is not None} - seen
    e["legacy_registry"] = legacy_rows
    # Leftovers of DELETED registrations that point at deleted entities (live ones are blockers).
    e["capture_job"] = _ids(conn, schema, "folder_environment_capture_jobs", "id", [
        ("case_id", e["case"]), ("capture_id", e["capture"]), ("load_case_id", e["load_case"])])
    e["env_registry"] = _ids(conn, schema, "folder_environment_registry", "id", [
        ("target_id", projects | e["request"] | e["case"] | e["load_case"])])
    # Media blobs referenced only by rows that are being deleted.
    blobs = _ids(conn, schema, "media_assets", "blob_id", [("id", e["media_asset"])])
    blobs |= _ids(conn, schema, "drop_video_assets", "blob_id", [("video_id", e["drop_video"])])
    if blobs:
        if schema.has("media_assets", "blob_id"):
            shared = _select_in(conn, "SELECT id,blob_id FROM media_assets WHERE blob_id IN ({marks})", blobs)
            blobs -= {str(blob) for asset, blob in shared if str(asset) not in e["media_asset"]}
        if blobs and schema.has("drop_video_assets", "blob_id"):
            shared = _select_in(conn, "SELECT video_id,blob_id FROM drop_video_assets WHERE blob_id IN ({marks})", blobs)
            blobs -= {str(blob) for video, blob in shared if str(video) not in e["drop_video"]}
    e["blob"] = blobs
    return e


def _blockers(conn, schema: _Schema, project_id: str, category: str, e: dict[str, set[str]]) -> list[dict]:
    """Reasons that reject the whole request (409); computed per project."""
    found: list[dict] = []
    if category == "REGISTERED":
        # Use registration delete (§13) first; row-level registration details would only repeat this.
        return [{"table": "folder_environment_registrations", "id": project_id, "reason": "LIVE_REGISTRATION"}]
    live_jobs = _select_in(conn, "SELECT j.id FROM folder_environment_capture_jobs j JOIN folder_environment_registrations r "
                                 "ON r.id=j.registration_id WHERE r.status<>'DELETED' AND j.id IN ({marks})", e["capture_job"])
    found += [{"table": "folder_environment_capture_jobs", "id": str(r[0]), "reason": "LIVE_REGISTRATION"} for r in live_jobs]
    live_registry = _select_in(conn, "SELECT g.id FROM folder_environment_registry g JOIN folder_environment_registrations r "
                                     "ON r.id=g.registration_id WHERE r.status<>'DELETED' AND g.id IN ({marks})", e["env_registry"])
    found += [{"table": "folder_environment_registry", "id": str(r[0]), "reason": "LIVE_REGISTRATION"} for r in live_registry]
    for table, kind in (("batch_execution_attempts", "attempt"), ("batch_dispatches", "dispatch"),
                        ("workflow_runs", "workflow_run"), ("task_runs", "task_run")):
        if e[kind] and schema.has(table, "status"):
            for row_id, status in _select_in(conn, f"SELECT id,status FROM {table} WHERE id IN ({{marks}})", e[kind]):
                if str(status or "").upper() in ACTIVE_STATUSES:
                    found.append({"table": table, "id": str(row_id), "reason": "RUNNING_EXECUTION"})
    if e["managed_run"] and schema.has("managed_local_runs", "run_json"):
        for row_id, raw in _select_in(conn, "SELECT id,run_json FROM managed_local_runs WHERE id IN ({marks})", e["managed_run"]):
            try:
                status = (json.loads(raw) if isinstance(raw, (str, bytes)) else raw or {}).get("status")
            except (TypeError, ValueError, AttributeError):
                status = None
            if str(status or "").upper() in ACTIVE_STATUSES:
                found.append({"table": "managed_local_runs", "id": str(row_id), "reason": "RUNNING_EXECUTION"})
    # A project-scoped analysis template still bound by a profile that is not being deleted.
    if schema.has("analysis_template_versions", "project_id", "template_id"):
        templates = {str(r[0]) for r in _select(conn, "SELECT template_id FROM analysis_template_versions WHERE project_id=?", [project_id])}
        if templates:
            marks = _marks(sorted(templates))
            for table, column in (("request_type_result_profiles", None), ("project_request_type_result_profiles", "project_id")):
                if not schema.has(table, "template_id"):
                    continue
                rows = _select(conn, f"SELECT template_id{',' + column if column else ''} FROM {table} WHERE template_id IN ({marks})", sorted(templates))
                for row in rows:
                    if column is None or str(row[1]) != project_id:
                        found.append({"table": table, "id": str(row[0]), "reason": "PROJECT_TEMPLATE_IN_USE"})
    unique = {(b["table"], b["id"], b["reason"]): b for b in found}
    return [unique[key] for key in sorted(unique)]


# ---------------------------------------------------------------------------
# Listing, planning and token
# ---------------------------------------------------------------------------

def candidates(conn) -> dict:
    """Every project with its cleanup category, sizes and user-data warning."""
    schema = _Schema(conn)
    live = _live_targets(conn, schema)
    items = []
    for project_id, name, created_at in _project_rows(conn):
        project_id = str(project_id)
        e = _entities(conn, schema, {project_id})
        category = _category(project_id, e, live)
        items.append({
            "project_id": project_id, "name": str(name or project_id), "category": category,
            "selectable": category != "REGISTERED",
            "requests": len(e["request"]), "cases": len(e["case"]), "runs": len(e["run"]),
            **_describe(conn, schema, category, e),
        })
    return {"items": items, "demo_project_ids": list(DEMO_PROJECT_IDS)}


def _where(pairs, e: dict[str, set[str]], schema: _Schema, table: str) -> tuple[str, list]:
    clauses, args = [], []
    for column, kind in pairs:
        values = sorted(e.get(kind, set()))
        if values and schema.has(table, column):
            clauses.append(f"{column} IN ({_marks(values)})")
            args.extend(values)
    return " OR ".join(clauses), args


def _null_specs(name: str) -> list[tuple]:
    value = _NULLS[name]
    return value if isinstance(value, list) else [value]


def _plan(conn, ids: list[str], *, missing_is_stale: bool = False) -> dict:
    schema = _Schema(conn)
    existing = {str(r[0]): r for r in _select(conn, f"SELECT id,name FROM projects WHERE id IN ({_marks(ids)})", ids)}
    missing = [value for value in ids if value not in existing]
    if missing and missing_is_stale:  # removed between preview and delete
        raise HTTPException(409, {"code": "DELETE_PREVIEW_STALE", "ids": missing,
                                  "message": "미리보기 이후 데이터가 바뀌었습니다. 다시 확인하세요."})
    if missing:
        raise HTTPException(404, {"code": "PROJECT_NOT_FOUND", "message": "정리할 프로젝트를 찾을 수 없습니다.", "ids": missing})
    live = _live_targets(conn, schema)
    items = []
    for project_id in ids:
        e = _entities(conn, schema, {project_id})
        category = _category(project_id, e, live)
        items.append({"project_id": project_id, "name": str(existing[project_id][1] or project_id), "category": category,
                      "entities": e})
    merged: dict[str, set[str]] = {}
    for item in items:
        for kind, values in item["entities"].items():
            merged.setdefault(kind, set()).update(values)
    # Media blobs shared between two selected projects are deleted once both owners go.
    merged["blob"] = _entities(conn, schema, set(ids))["blob"] if len(ids) > 1 else merged.get("blob", set())
    if sum(len(values) for values in merged.values()) > MAX_PARAMS:
        raise ValueError("한 번에 정리할 데이터가 너무 많습니다. 프로젝트를 나누어 선택하세요.")
    for item in items:
        e = item.pop("entities")
        item["blockers"] = _blockers(conn, schema, item["project_id"], item["category"], e)
        item["deletable"] = not item["blockers"]
        item["counts"] = _counts(conn, schema, e)
        item.update(_describe(conn, schema, item["category"], e))
    totals = _counts(conn, schema, merged)
    state = {
        "projects": sorted(ids),
        "categories": {item["project_id"]: item["category"] for item in items},
        "entities": {kind: sorted(values) for kind, values in sorted(merged.items())},
        "counts": totals,
        "blockers": {item["project_id"]: [[b["table"], b["id"], b["reason"]] for b in item["blockers"]] for item in items},
        "acknowledge": {item["project_id"]: item["acknowledge_reasons"] for item in items},
    }
    token = hashlib.sha256(json.dumps(state, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return {"items": items, "totals": totals, "confirm_token": token, "entities": merged, "schema": schema,
            "blocked": any(item["blockers"] for item in items)}


def _counts(conn, schema: _Schema, e: dict[str, set[str]]) -> dict[str, int]:
    """Rows each delete step will remove, by table (NULL updates as ``table.column``)."""
    counts: dict[str, int] = {}
    for stage in _STAGES:
        for table, pairs in stage:
            if table.startswith("*null:"):
                for target, column, match, extra in _null_specs(table[6:]):
                    where, args = _where(match, e, schema, target)
                    if where and schema.has(target, column):
                        n = conn.execute(f"SELECT count(*) FROM {target} WHERE ({where})" + (f" AND {extra}" if extra else ""), args).fetchone()[0]
                        if n:
                            counts[f"{target}.{column}"] = counts.get(f"{target}.{column}", 0) + int(n)
                continue
            where, args = _where(pairs, e, schema, table)
            if where:
                n = conn.execute(f"SELECT count(*) FROM {table} WHERE {where}", args).fetchone()[0]
                if n:
                    counts[table] = int(n)
    return counts


def _public(plan: dict) -> dict:
    return {"items": plan["items"], "totals": plan["totals"], "confirm_token": plan["confirm_token"]}


def preview(conn, project_ids) -> dict:
    """Read-only plan: per-project counts by table, blockers and a confirm token."""
    return _public(_plan(conn, _validated_ids(project_ids)))


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

_CHUNK = 500


def _execute(conn, schema: _Schema, stage, e: dict[str, set[str]]) -> None:
    for table, pairs in stage:
        if table.startswith("*null:"):
            for target, column, match, extra in _null_specs(table[6:]):
                if not schema.has(target, column):
                    continue
                for match_column, kind in match:
                    values = sorted(e.get(kind, set()))
                    if not values or not schema.has(target, match_column):
                        continue
                    for start in range(0, len(values), _CHUNK):
                        chunk = values[start:start + _CHUNK]
                        conn.execute(f"UPDATE {target} SET {column}=NULL WHERE {match_column} IN ({_marks(chunk)})"
                                     + (f" AND {extra}" if extra else ""), chunk)
            continue
        for column, kind in pairs:
            values = sorted(e.get(kind, set()))
            if not values or not schema.has(table, column):
                continue
            for start in range(0, len(values), _CHUNK):
                chunk = values[start:start + _CHUNK]
                conn.execute(f"DELETE FROM {table} WHERE {column} IN ({_marks(chunk)})", chunk)


def removed_demo_projects(conn) -> set[str]:
    """Demo project ids an administrator removed (``spdm_storage_settings``); empty when unset."""
    try:
        row = conn.execute("SELECT setting_value FROM spdm_storage_settings WHERE setting_key=?", [DEMO_REMOVED_SETTING]).fetchone()
    except Exception:  # noqa: BLE001 - table absent on a partial schema: nothing was removed
        return set()
    if not row or not row[0]:
        return set()
    try:
        value = json.loads(row[0])
    except (TypeError, ValueError):
        return set()
    return {str(item) for item in value} if isinstance(value, list) else set()


def _remember_demo_removed(conn, deleted: list[str]) -> None:
    demo = {value for value in deleted if value in DEMO_PROJECT_IDS}
    if not demo:
        return
    merged = sorted(removed_demo_projects(conn) | demo)
    stamp = _now()
    conn.execute("DELETE FROM spdm_storage_settings WHERE setting_key=?", [DEMO_REMOVED_SETTING])
    conn.execute("INSERT INTO spdm_storage_settings(setting_key,setting_value,updated_at) VALUES(?,?,?)",
                 [DEMO_REMOVED_SETTING, json.dumps(merged), stamp])


def delete(conn, project_ids, confirm_token: str, audit: Callable[[Any, dict], None] | None = None,
           acknowledged_project_ids=None) -> dict:
    """Delete the selected projects atomically; see module docstring.

    Projects whose preview has ``requires_acknowledgement`` (non-demo data that was imported or
    registered, data a person made, system analysis pages) must be listed in
    ``acknowledged_project_ids`` (the screen asks for the typed project name), else 409
    ``PROJECT_CLEANUP_CONFIRM_REQUIRED``.
    """
    ids = _validated_ids(project_ids)
    acknowledged = {str(value) for value in (acknowledged_project_ids or []) if isinstance(value, str)}
    if not isinstance(confirm_token, str) or not confirm_token:
        raise ValueError("confirm_token이 필요합니다.")
    postgres = _postgres(conn)
    with ExitStack() as stack:
        stack.enter_context(legacy.WRITE_LOCK)
        conn.execute("BEGIN TRANSACTION")
        try:
            if postgres:
                # Serialize against registration, discovery and other writers of these parents until commit.
                conn.execute("LOCK TABLE projects, analysis_requests, load_cases, folder_environment_registrations "
                             "IN SHARE ROW EXCLUSIVE MODE")
            current = _plan(conn, ids, missing_is_stale=True)
            if current["confirm_token"] != confirm_token:
                raise HTTPException(409, {"code": "DELETE_PREVIEW_STALE",
                                          "message": "미리보기 이후 데이터가 바뀌었습니다. 다시 확인하세요."})
            if current["blocked"]:
                raise HTTPException(409, {"code": "PROJECT_CLEANUP_BLOCKED", "items": current["items"],
                                          "message": "등록되었거나 실행 중인 작업이 있어 아무것도 삭제하지 않았습니다."})
            unconfirmed = [item["project_id"] for item in current["items"]
                           if item["requires_acknowledgement"] and item["project_id"] not in acknowledged]
            if unconfirmed:
                raise HTTPException(409, {"code": "PROJECT_CLEANUP_CONFIRM_REQUIRED", "project_ids": unconfirmed,
                                          "items": current["items"],
                                          "message": "보관 데이터가 있는 프로젝트는 이름을 입력해 한 번 더 확인해야 합니다."})
            schema, entities = current["schema"], current["entities"]
            for index, stage in enumerate(_STAGES):
                _execute(conn, schema, stage, entities)
                if not postgres and index < len(_STAGES) - 1:
                    # DuckDB: a parent row cannot be deleted in the transaction that deleted its FK children.
                    conn.execute("COMMIT")
                    conn.execute("BEGIN TRANSACTION")
            _remember_demo_removed(conn, ids)
            result = {"deleted": ids, "counts": current["totals"]}
            if audit is not None:
                audit(conn, {"project_ids": ids, "categories": {item["project_id"]: item["category"] for item in current["items"]},
                             "acknowledged": sorted(acknowledged & set(ids)),
                             "system_pages": sorted(page["id"] for item in current["items"] for page in item["system_pages"]),
                             "counts": current["totals"]})
            conn.execute("COMMIT")
            return result
        except BaseException:
            conn.execute("ROLLBACK")
            raise
