"""Administrator deletion of folder environment registrations (contract §13).

Deletes only database rows: the registration's folder role mapping and the
business data the registration created. SPDM folders and files are never
read for writing, moved or removed (D14) -- this module performs no
filesystem access at all.

Ownership (§13.2)
    * ``created_targets`` (recorded by ``register`` since 0035) lists the
      project/request/case ids the registration actually inserted.
    * Older registrations are inferred: a registry/registration target is
      owned when the entity was created no earlier than the registration
      minus five seconds and no other live registration references it.
    * Entities referenced by another live registration are preserved
      (projects/requests) or block the whole request (cases, §13.8-13).
    * Rows created by other features that hang off an owned entity are
      either registration-derived (deleted) or independent user data
      (``BLOCKERS``, the whole request is rejected with 409).

All requested registrations are planned together and deleted all-or-nothing.
PostgreSQL runs one transaction with ``SELECT ... FOR UPDATE``. DuckDB cannot
delete a parent row in the same transaction that deleted its foreign-key
children, so the embedded development backend commits FK levels in order
(assets, captures, cases, rest); the plan is recomputed from the registry on
a retry, so an interrupted run converges.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from fastapi import HTTPException

from . import folder_discovery as legacy

MAX_IDS = 200
OWNERSHIP_SLACK = timedelta(seconds=5)
LIVE_STATUSES = ("REGISTERED", "CAPTURING", "COMPLETED", "FAILED")
COUNT_KEYS = ("projects", "requests", "cases", "captures", "assets", "finalizations", "other")

# Registration-derived child rows of an owned request: (table, parent column, identity columns).
REQUEST_CHILDREN = (
    ("request_steps", "request_id", ("id",)),
    ("request_work_items", "request_id", ("id",)),
    ("request_work_plans", "request_id", ("request_id",)),
    ("analysis_request_type_assignments", "request_id", ("request_id",)),
    ("request_result_layout_snapshots", "request_id", ("request_id",)),
    ("result_registration_paths", "request_id", ("id",)),
    ("result_registration_location_links", "request_id", ("id",)),
)
# Defaults written when a project is created (projects.materialize / SPDM discovery).
PROJECT_CHILDREN = (
    ("product_information", "project_id", ("id",)),
    ("project_memberships", "project_id", ("id",)),
    ("project_invitations", "project_id", ("id",)),
    ("quality_thresholds", "project_id", ("criterion_key",)),
    ("project_workspace_layout_versions", "project_id", ("layout_kind", "version")),
    ("project_workspace_layouts", "project_id", ("layout_kind",)),
    ("project_request_type_result_profiles", "project_id", ("request_type_id", "request_type_version", "binding_version")),
)
# Independent data that must survive: a reference from these blocks the delete (D15).
REQUEST_BLOCKERS = (
    ("load_cases", "request_id", "id", "LOAD_CASE_DATA"),
    ("workflow_runs", "request_id", "id", "WORKFLOW_RUN"),
    ("managed_device_grants", "request_id", "id", "MANAGED_EXECUTION"),
    ("managed_local_runs", "request_id", "id", "MANAGED_EXECUTION"),
    ("validations", "request_id", "id", "VALIDATION"),
    ("dashboards", "request_id", "id", "ANALYSIS_PAGE"),
    ("spdm_storage_bindings", "request_id", "load_case_id", "SPDM_STORAGE_LINK"),
    ("spdm_storage_request_parents", "request_id", "request_folder", "SPDM_STORAGE_LINK"),
    ("semantic_folder_bindings", "request_id", "id", "LEGACY_MAPPING"),
    ("folder_discovery_registry", "target_id", "id", "LEGACY_MAPPING"),
)
PROJECT_BLOCKERS = (
    ("dashboards", "project_id", "id", "ANALYSIS_PAGE"),
    ("validations", "project_id", "id", "VALIDATION"),
    ("analysis_template_versions", "project_id", "template_id", "PROJECT_TEMPLATE"),
    ("semantic_vocabulary_entries", "scope_project_id", "id", "PROJECT_VOCABULARY"),
    ("semantic_folder_bindings", "project_id", "id", "LEGACY_MAPPING"),
    ("spdm_storage_bindings", "project_id", "load_case_id", "SPDM_STORAGE_LINK"),
    ("spdm_storage_project_parents", "project_id", "project_folder", "SPDM_STORAGE_LINK"),
    ("spdm_storage_request_parents", "project_id", "request_folder", "SPDM_STORAGE_LINK"),
    ("folder_discovery_registry", "target_id", "id", "LEGACY_MAPPING"),
)
WORK_ITEM_BLOCKERS = (
    ("batch_execution_attempts", "work_item_id", "id", "BATCH_EXECUTION"),
    ("batch_dispatches", "work_item_id", "id", "BATCH_EXECUTION"),
    ("managed_device_grants", "work_item_id", "id", "MANAGED_EXECUTION"),
    ("managed_local_runs", "work_item_id", "id", "MANAGED_EXECUTION"),
)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _postgres(conn) -> bool:
    return getattr(conn, "backend", "duckdb") == "postgresql"


def _marks(values) -> str:
    return ",".join("?" for _ in values)


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is None else value.astimezone(timezone.utc).replace(tzinfo=None)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return _as_datetime(parsed)
    return None


class _Schema:
    """Existing tables/columns; optional features may be absent on DuckDB."""

    def __init__(self, conn) -> None:
        if _postgres(conn):
            found = conn.execute("SELECT table_name,column_name FROM information_schema.columns "
                                 "WHERE table_schema='public'").fetchall()
        else:
            found = conn.execute("SELECT table_name,column_name FROM information_schema.columns").fetchall()
        self.columns: dict[str, set[str]] = {}
        for table, column in found:
            self.columns.setdefault(str(table), set()).add(str(column))

    def has(self, table: str, *columns: str) -> bool:
        return table in self.columns and all(column in self.columns[table] for column in columns)


def _select(conn, sql: str, args: list) -> list[tuple]:
    return [tuple(row) for row in conn.execute(sql, args).fetchall()]


def _validated_ids(registration_ids) -> list[str]:
    if not isinstance(registration_ids, list) or not 1 <= len(registration_ids) <= MAX_IDS:
        raise ValueError(f"registration_ids는 1~{MAX_IDS}개여야 합니다.")
    cleaned = []
    for value in registration_ids:
        if not isinstance(value, str) or not value or len(value) > 128:
            raise ValueError("등록 id 형식이 올바르지 않습니다.")
        if value not in cleaned:
            cleaned.append(value)
    return cleaned


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

class _Plan:
    def __init__(self) -> None:
        self.deletes: dict[str, set[tuple]] = {}      # table -> identity tuples (for the token and counts)
        self.by_parent: list[tuple[str, str, tuple]] = []  # (table, column, ids) bulk deletes
        self.nulls: list[tuple[str, str, tuple]] = []  # (table, column, ids) set to NULL

    def add(self, table: str, identities) -> None:
        self.deletes.setdefault(table, set()).update(identities)


def _live_references(conn, schema: _Schema, batch: list[str]) -> dict[str, set]:
    marks = _marks(batch)
    live = _select(conn, "SELECT id,project_id,request_id FROM folder_environment_registrations "
                         f"WHERE status<>'DELETED' AND id NOT IN ({marks})", batch)
    refs = {"targets": set(), "case_paths": set(), "cases": set(), "registrations": {str(r[0]) for r in live}}
    for _id, project_id, request_id in live:
        refs["targets"].update(str(v) for v in (project_id, request_id) if v)
    if live:
        ids = [str(r[0]) for r in live]
        for root_key, path, role, target in _select(
                conn, "SELECT root_key,relative_path,role_kind,target_id FROM folder_environment_registry "
                      f"WHERE registration_id IN ({_marks(ids)})", ids):
            refs["targets"].add(str(target))
            if role == "SIMULATION_CASE":
                refs["case_paths"].add((str(root_key), str(path).casefold()))
        refs["cases"].update(str(r[0]) for r in _select(
            conn, f"SELECT case_id FROM folder_environment_capture_jobs WHERE registration_id IN ({_marks(ids)})", ids))
    return refs


def _registration_rows(conn, ids: list[str], *, lock: bool) -> dict[str, dict]:
    clause = " FOR UPDATE" if lock and _postgres(conn) else ""
    found = {}
    for row in _select(conn, "SELECT id,project_id,request_id,environment,status,created_at,created_targets "
                             f"FROM folder_environment_registrations WHERE id IN ({_marks(ids)}) ORDER BY id{clause}", ids):
        targets = None
        if row[6]:
            try:
                targets = json.loads(row[6]) if isinstance(row[6], str) else row[6]
            except (TypeError, ValueError):
                targets = None
        found[str(row[0])] = {"id": str(row[0]), "project_id": row[1] and str(row[1]), "request_id": row[2] and str(row[2]),
                              "environment": str(row[3]), "status": str(row[4]), "created_at": _as_datetime(row[5]),
                              "created_targets": targets if isinstance(targets, dict) else None}
    missing = [value for value in ids if value not in found]
    if missing:
        legacy.fail("ENVIRONMENT_REGISTRATION_NOT_FOUND", "환경 등록을 찾을 수 없습니다.", 404)
    return found


def _owned_entities(conn, registration: dict, registry: list[tuple], refs: dict) -> tuple[set[str], set[str]]:
    """Owned project and request ids of one registration (§13.2 1, 2, 4)."""
    candidates = {"PROJECT": set(), "REQUEST": set()}
    created = registration["created_targets"]
    if created is not None:
        candidates["PROJECT"].update(str(v) for v in created.get("project_ids") or [])
        candidates["REQUEST"].update(str(v) for v in created.get("request_ids") or [])
    else:
        for _root, _path, role, target in registry:
            if role in candidates:
                candidates[role].add(str(target))
        if registration["project_id"]:
            candidates["PROJECT"].add(registration["project_id"])
        if registration["request_id"]:
            candidates["REQUEST"].add(registration["request_id"])
    threshold = registration["created_at"] - OWNERSHIP_SLACK if registration["created_at"] else None
    owned = {"PROJECT": set(), "REQUEST": set()}
    for role, table, stamp in (("PROJECT", "projects", "created_at"), ("REQUEST", "analysis_requests", "requested_at")):
        ids = sorted(candidates[role])
        if not ids:
            continue
        for entity_id, created_at in _select(conn, f"SELECT id,{stamp} FROM {table} WHERE id IN ({_marks(ids)})", ids):
            entity_id = str(entity_id)
            if entity_id in refs["targets"]:
                continue  # another live registration uses it: preserve (§13.2-2, D15)
            if created is None:
                when = _as_datetime(created_at)
                if threshold is None or when is None or when < threshold:
                    continue  # existed before this registration (LINK): preserve (§13.2-4)
            owned[role].add(entity_id)
    # §13.2-5: the last live registration of a project/request also owns what an already
    # DELETED registration of the same project/request created, so deleting registrations
    # one by one leaves no empty project or request behind.
    for role, table, stamp, key in (("PROJECT", "projects", "created_at", "project_id"),
                                    ("REQUEST", "analysis_requests", "requested_at", "request_id")):
        entity_id = registration[key]
        if not entity_id or entity_id in owned[role] or entity_id in refs["targets"]:
            continue
        if _created_by_deleted_registration(conn, key, entity_id, table, stamp):
            owned[role].add(entity_id)
    return owned["PROJECT"], owned["REQUEST"]


def _created_by_deleted_registration(conn, key: str, entity_id: str, table: str, stamp: str) -> bool:
    found = _select(conn, f"SELECT {stamp} FROM {table} WHERE id=?", [entity_id])
    if not found:
        return False
    entity_created = _as_datetime(found[0][0])
    list_key = "project_ids" if key == "project_id" else "request_ids"
    for created_at, raw in _select(conn, "SELECT created_at,created_targets FROM folder_environment_registrations "
                                         f"WHERE status='DELETED' AND {key}=?", [entity_id]):
        targets = None
        if raw:
            try:
                targets = json.loads(raw) if isinstance(raw, str) else raw
            except (TypeError, ValueError):
                targets = None
        if isinstance(targets, dict):
            if entity_id in {str(v) for v in targets.get(list_key) or []}:
                return True
            continue
        registered = _as_datetime(created_at)
        if registered is not None and entity_created is not None and entity_created >= registered - OWNERSHIP_SLACK:
            return True
    return False


def _plan(conn, ids: list[str], *, lock: bool = False) -> dict:
    schema = _Schema(conn)
    registrations = _registration_rows(conn, ids, lock=lock)
    active = [rid for rid in ids if registrations[rid]["status"] != "DELETED"]
    refs = _live_references(conn, schema, ids)
    plan = _Plan()
    items: dict[str, dict] = {}
    owned_projects: set[str] = set()
    owned_requests: set[str] = set()
    owned_cases: set[str] = set()
    case_owner: dict[str, str] = {}
    blockers: dict[str, list[dict]] = {rid: [] for rid in ids}
    for rid in ids:
        items[rid] = {"registration_id": rid, "deletable": True, "counts": dict.fromkeys(COUNT_KEYS, 0), "blockers": blockers[rid]}
        if rid not in active:
            items[rid]["already_deleted"] = True
    per_item_projects: dict[str, set[str]] = {}
    per_item_requests: dict[str, set[str]] = {}
    for rid in active:
        registration = registrations[rid]
        registry = _select(conn, "SELECT root_key,relative_path,role_kind,target_id FROM folder_environment_registry "
                                 "WHERE registration_id=?", [rid])
        projects, requests = _owned_entities(conn, registration, registry, refs)
        per_item_projects[rid], per_item_requests[rid] = projects, requests
        owned_projects |= projects
        owned_requests |= requests
        # Cases (§13.2-3): registry SIMULATION_CASE rows, capture jobs, recorded case ids.
        case_ids = {str(target) for _r, _p, role, target in registry if role == "SIMULATION_CASE"}
        case_ids |= {str(r[0]) for r in _select(conn, "SELECT case_id FROM folder_environment_capture_jobs WHERE registration_id=?", [rid])}
        if registration["created_targets"]:
            case_ids |= {str(v) for v in registration["created_targets"].get("case_ids") or []}
        case_paths = {(str(root), str(path).casefold()) for root, path, role, _t in registry if role == "SIMULATION_CASE"}
        existing = _select(conn, f"SELECT id FROM dashboard_cases WHERE id IN ({_marks(sorted(case_ids))})", sorted(case_ids)) if case_ids else []
        for (case_id,) in existing:
            case_owner.setdefault(str(case_id), rid)
            owned_cases.add(str(case_id))
        for root, path in case_paths:
            if (root, path) in refs["case_paths"]:
                blockers[rid].append({"table": "folder_environment_registry", "id": path, "reason": "CASE_SHARED_WITH_LIVE_REGISTRATION"})
    # Cases under owned requests belong to the request that is being removed.
    if owned_requests:
        for (case_id, request_id) in _select(conn, f"SELECT id,request_id FROM dashboard_cases WHERE request_id IN ({_marks(sorted(owned_requests))})", sorted(owned_requests)):
            owned_cases.add(str(case_id))
            case_owner.setdefault(str(case_id), next((rid for rid in active if str(request_id) in per_item_requests[rid]), active[0]))
    for case_id in sorted(owned_cases):
        if case_id in refs["cases"] or case_id in refs["targets"]:
            blockers[case_owner[case_id]].append({"table": "dashboard_cases", "id": case_id, "reason": "CASE_SHARED_WITH_LIVE_REGISTRATION"})

    def owner_of(entity: str, mapping: dict[str, set[str]]) -> str:
        return next((rid for rid in active if entity in mapping[rid]), active[0] if active else ids[0])

    case_list = sorted(owned_cases)
    captures = _select(conn, f"SELECT id,case_id FROM dashboard_captures WHERE case_id IN ({_marks(case_list)})", case_list) if case_list else []
    capture_ids = sorted(str(r[0]) for r in captures)
    capture_case = {str(r[0]): str(r[1]) for r in captures}
    assets = _select(conn, f"SELECT id,capture_id FROM dashboard_assets WHERE capture_id IN ({_marks(capture_ids)})", capture_ids) if capture_ids else []
    plan.add("dashboard_cases", {(c,) for c in case_list})
    plan.add("dashboard_captures", {(c,) for c in capture_ids})
    plan.add("dashboard_assets", {(str(a[0]),) for a in assets})
    for rid in active:
        mine = {c for c in case_list if case_owner.get(c) == rid}
        items[rid]["counts"]["cases"] = len(mine)
        mine_captures = {c for c in capture_ids if capture_case[c] in mine}
        items[rid]["counts"]["captures"] = len(mine_captures)
        items[rid]["counts"]["assets"] = sum(1 for a in assets if str(a[1]) in mine_captures)
    # Capture jobs: this batch's own jobs, and jobs of DELETED registrations that point at owned cases.
    jobs = _select(conn, f"SELECT id,registration_id FROM folder_environment_capture_jobs WHERE registration_id IN ({_marks(active)})", active) if active else []
    if case_list:
        jobs += _select(conn, "SELECT j.id,j.registration_id FROM folder_environment_capture_jobs j "
                              "JOIN folder_environment_registrations r ON r.id=j.registration_id "
                              f"WHERE r.status='DELETED' AND j.case_id IN ({_marks(case_list)})", case_list)
    if capture_ids:
        for job_id, owner in _select(conn, "SELECT j.id,j.registration_id FROM folder_environment_capture_jobs j "
                                           "JOIN folder_environment_registrations r ON r.id=j.registration_id "
                                           f"WHERE r.status<>'DELETED' AND j.capture_id IN ({_marks(capture_ids)})", capture_ids):
            if str(owner) not in active:
                blockers[active[0]].append({"table": "folder_environment_capture_jobs", "id": str(job_id), "reason": "CAPTURE_SHARED_WITH_LIVE_REGISTRATION"})
    plan.add("folder_environment_capture_jobs", {(str(j[0]),) for j in jobs})
    registry_rows = _select(conn, f"SELECT id,registration_id FROM folder_environment_registry WHERE registration_id IN ({_marks(active)})", active) if active else []
    plan.add("folder_environment_registry", {(str(r[0]),) for r in registry_rows})
    # Result registration drafts tied to owned cases/captures or owned requests (+ files, events).
    if schema.has("result_registration_drafts"):
        clauses, args = [], []
        for column, values in (("case_id", case_list), ("capture_id", capture_ids), ("request_id", sorted(owned_requests))):
            if values:
                clauses.append(f"{column} IN ({_marks(values)})")
                args.extend(values)
        drafts = sorted({str(r[0]) for r in _select(conn, "SELECT id FROM result_registration_drafts WHERE " + " OR ".join(clauses), args)}) if clauses else []
        plan.add("result_registration_drafts", {(d,) for d in drafts})
        if drafts:
            plan.add("result_registration_files", set(_select(conn, f"SELECT draft_id,relative_path FROM result_registration_files WHERE draft_id IN ({_marks(drafts)})", drafts)))
            plan.add("result_registration_events", {(str(r[0]),) for r in _select(conn, f"SELECT id FROM result_registration_events WHERE draft_id IN ({_marks(drafts)})", drafts)})
    # Owned requests: derived child rows are deleted, independent data blocks.
    request_list = sorted(owned_requests)
    if request_list:
        for table, column, identity in REQUEST_CHILDREN:
            if not schema.has(table, column, *identity):
                continue
            found = _select(conn, f"SELECT {','.join(identity)} FROM {table} WHERE {column} IN ({_marks(request_list)})", request_list)
            plan.add(table, {tuple(str(v) for v in row) for row in found})
            plan.by_parent.append((table, column, tuple(request_list)))
        for table, column, key, reason in REQUEST_BLOCKERS:
            if not schema.has(table, column, key):
                continue
            for value, ref in _select(conn, f"SELECT {key},{column} FROM {table} WHERE {column} IN ({_marks(request_list)})", request_list):
                blockers[owner_of(str(ref), per_item_requests)].append({"table": table, "id": str(value), "reason": reason})
        work_items = sorted(i[0] for i in plan.deletes.get("request_work_items", set()))
        for table, column, key, reason in WORK_ITEM_BLOCKERS:
            if work_items and schema.has(table, column, key):
                for (value,) in _select(conn, f"SELECT {key} FROM {table} WHERE {column} IN ({_marks(work_items)})", work_items):
                    blockers[active[0]].append({"table": table, "id": str(value), "reason": reason})
    # Owned projects.
    project_list = sorted(owned_projects)
    if project_list:
        for request_id, project_id in _select(conn, f"SELECT id,project_id FROM analysis_requests WHERE project_id IN ({_marks(project_list)})", project_list):
            if str(request_id) not in owned_requests:
                blockers[owner_of(str(project_id), per_item_projects)].append({"table": "analysis_requests", "id": str(request_id), "reason": "FOREIGN_REQUEST_UNDER_PROJECT"})
        for case_id, project_id in _select(conn, f"SELECT id,project_id FROM dashboard_cases WHERE project_id IN ({_marks(project_list)})", project_list):
            if str(case_id) not in owned_cases:
                blockers[owner_of(str(project_id), per_item_projects)].append({"table": "dashboard_cases", "id": str(case_id), "reason": "FOREIGN_CASE_UNDER_PROJECT"})
        for table, column, identity in PROJECT_CHILDREN:
            if not schema.has(table, column, *identity):
                continue
            found = _select(conn, f"SELECT {','.join(identity)} FROM {table} WHERE {column} IN ({_marks(project_list)})", project_list)
            plan.add(table, {tuple(str(v) for v in row) for row in found})
            plan.by_parent.append((table, column, tuple(project_list)))
        for table, column, key, reason in PROJECT_BLOCKERS:
            if not schema.has(table, column, key):
                continue
            for value, ref in _select(conn, f"SELECT {key},{column} FROM {table} WHERE {column} IN ({_marks(project_list)})", project_list):
                blockers[owner_of(str(ref), per_item_projects)].append({"table": table, "id": str(value), "reason": reason})
    plan.add("analysis_requests", {(r,) for r in request_list})
    plan.add("projects", {(p,) for p in project_list})
    # Scan/preview history keeps its join but loses the business link (§13.3-4).
    for column, values in (("project_id", project_list), ("request_id", request_list)):
        if values:
            plan.nulls.append(("folder_environment_scans", column, tuple(values)))
            plan.add(f"folder_environment_scans.{column}", {(str(r[0]),) for r in _select(
                conn, f"SELECT id FROM folder_environment_scans WHERE {column} IN ({_marks(values)})", list(values))})
    # Per-item counts.
    counted = {"dashboard_cases", "dashboard_captures", "dashboard_assets", "analysis_requests", "projects"}
    other_total = sum(len(rows) for table, rows in plan.deletes.items() if table not in counted and "." not in table)
    for rid in active:
        counts = items[rid]["counts"]
        counts["projects"] = len(per_item_projects[rid])
        counts["requests"] = len(per_item_requests[rid])
    if active:
        # Shared child rows are attributed to the first registration so the totals stay exact.
        items[active[0]]["counts"]["other"] = other_total
    for rid in ids:
        unique, seen = [], set()
        for blocker in blockers[rid]:
            key = (blocker["table"], blocker["id"], blocker["reason"])
            if key not in seen:
                seen.add(key)
                unique.append(blocker)
        blockers[rid][:] = sorted(unique, key=lambda b: (b["table"], b["id"], b["reason"]))
        items[rid]["deletable"] = not blockers[rid]
    # Any other live registration on the same project/request changes the state the admin confirmed.
    scope = sorted({str(v) for r in registrations.values() for v in (r["project_id"], r["request_id"]) if v})
    touching = sorted(str(r[0]) for r in _select(
        conn, "SELECT id FROM folder_environment_registrations WHERE status<>'DELETED' "
              f"AND (project_id IN ({_marks(scope)}) OR request_id IN ({_marks(scope)}))", [*scope, *scope])) if scope else []
    token = _token(ids, plan, blockers, touching)
    totals = dict.fromkeys(COUNT_KEYS, 0)
    totals.update({"projects": len(project_list), "requests": len(request_list), "cases": len(case_list),
                   "captures": len(capture_ids), "assets": len(plan.deletes.get("dashboard_assets", ())), "other": other_total})
    return {"items": [items[rid] for rid in ids], "confirm_token": token, "plan": plan, "active": active,
            "totals": totals, "blocked": any(blockers[rid] for rid in ids), "registrations": registrations}


def _token(ids: list[str], plan: _Plan, blockers: dict[str, list[dict]], touching: list[str]) -> str:
    state = {
        "targets": sorted(ids),
        "live_in_scope": touching,
        "rows": {table: sorted([list(row) for row in rows]) for table, rows in sorted(plan.deletes.items())},
        "blockers": {rid: [[b["table"], b["id"], b["reason"]] for b in blockers[rid]] for rid in sorted(ids)},
    }
    return hashlib.sha256(json.dumps(state, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _scope_locks(conn, registrations: dict, ids: list[str]) -> list:
    """Auto-sync scope locks of the affected requests (§13.3), in a stable order."""
    try:
        from . import folder_auto_sync
        from .folder_discovery_scan import root_identity
        root_key = root_identity(legacy.configured_root(conn))
    except Exception:  # noqa: BLE001 - no storage root: no auto-sync can be running either
        return []
    keys = sorted({(root_key, str(r["project_id"]), str(r["request_id"]), str(r["environment"]))
                   for rid, r in registrations.items() if rid in ids and r["project_id"] and r["request_id"]})
    return [folder_auto_sync._scope_lock(key) for key in keys]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _public(result: dict) -> dict:
    return {"items": result["items"], "confirm_token": result["confirm_token"]}


def delete_preview(conn, registration_ids) -> dict:
    """Read-only plan: per-registration counts and blockers plus a confirm token."""
    ids = _validated_ids(registration_ids)
    return _public(_plan(conn, ids))


_STAGES = (  # FK-safe order, children first (§13.3).
    ("folder_environment_capture_jobs", "result_registration_files", "result_registration_events",
     "result_registration_drafts", "dashboard_assets"),
    ("dashboard_captures",),
    ("dashboard_cases",),
    ("folder_environment_registry", "*children", "*nulls", "analysis_requests", "projects"),
)
_SINGLE_KEYS = {
    "folder_environment_capture_jobs": "id", "result_registration_events": "id", "result_registration_drafts": "id",
    "dashboard_assets": "id", "dashboard_captures": "id", "dashboard_cases": "id", "folder_environment_registry": "id",
    "analysis_requests": "id", "projects": "id",
}
_CHUNK = 500


def _delete_ids(conn, table: str, column: str, values: list) -> None:
    for start in range(0, len(values), _CHUNK):
        chunk = values[start:start + _CHUNK]
        conn.execute(f"DELETE FROM {table} WHERE {column} IN ({_marks(chunk)})", chunk)


def _execute_stage(conn, stage: tuple, plan: _Plan) -> None:
    for step in stage:
        if step == "*children":
            # Request children before project children; work items before work plans.
            for table, column, parents in plan.by_parent:
                _delete_ids(conn, table, column, list(parents))
        elif step == "*nulls":
            for table, column, values in plan.nulls:
                for start in range(0, len(values), _CHUNK):
                    chunk = list(values[start:start + _CHUNK])
                    conn.execute(f"UPDATE {table} SET {column}=NULL WHERE {column} IN ({_marks(chunk)})", chunk)
        elif step == "result_registration_files":
            drafts = sorted({row[0] for row in plan.deletes.get(step, set())})
            _delete_ids(conn, step, "draft_id", drafts)
        else:
            values = sorted(row[0] for row in plan.deletes.get(step, set()))
            if values:
                _delete_ids(conn, step, _SINGLE_KEYS[step], values)


def delete(conn, registration_ids, confirm_token: str, actor: str,
           audit: Callable[[Any, dict], None] | None = None) -> dict:
    """Delete the registrations atomically; see module docstring.

    Raises 409 ``DELETE_PREVIEW_STALE`` when ``confirm_token`` does not match
    the current plan and 409 ``REGISTRATION_DELETE_BLOCKED`` (with ``items``)
    when any registration is blocked. Nothing is changed in either case.
    """
    ids = _validated_ids(registration_ids)
    if not isinstance(confirm_token, str) or not confirm_token:
        raise ValueError("confirm_token이 필요합니다.")
    first = _plan(conn, ids)
    with ExitStack() as stack:
        for lock in _scope_locks(conn, first["registrations"], ids):
            stack.enter_context(lock)
        stack.enter_context(legacy.WRITE_LOCK)
        postgres = _postgres(conn)
        conn.execute("BEGIN TRANSACTION")
        try:
            current = _plan(conn, ids, lock=True)
            if current["confirm_token"] != confirm_token:
                raise HTTPException(409, {"code": "DELETE_PREVIEW_STALE",
                                          "message": "미리보기 이후 등록 상태가 바뀌었습니다. 다시 확인하세요."})
            if current["blocked"]:
                raise HTTPException(409, {"code": "REGISTRATION_DELETE_BLOCKED", "items": current["items"],
                                          "message": "다른 업무가 참조하는 데이터가 있어 삭제할 수 없습니다."})
            plan, active = current["plan"], current["active"]
            for index, stage in enumerate(_STAGES):
                _execute_stage(conn, stage, plan)
                if not postgres and index < len(_STAGES) - 1:
                    # DuckDB: a parent row cannot be deleted in the transaction that deleted its FK children.
                    conn.execute("COMMIT")
                    conn.execute("BEGIN TRANSACTION")
            stamp = _now()
            if active:
                conn.execute(f"UPDATE folder_environment_registrations SET status='DELETED',deleted_at=?,deleted_by=? "
                             f"WHERE id IN ({_marks(active)}) AND status<>'DELETED'", [stamp, actor, *active])
            from .folder_discovery_environment import iso_utc
            result = {"deleted": active, "counts": current["totals"], "deleted_at": iso_utc(stamp) if active else None}
            if audit is not None and active:
                audit(conn, {"registration_ids": active, "counts": current["totals"],
                             "skipped": [rid for rid in ids if rid not in active]})
            conn.execute("COMMIT")
            return result
        except BaseException:
            conn.execute("ROLLBACK")
            raise
