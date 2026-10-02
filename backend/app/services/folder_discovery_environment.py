"""Environment-aware, parent-first discovery plans.

This module deliberately does not reinterpret a saved plan during capture.  It
keeps the original names and a stable context id for every optional Run Option.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import PurePosixPath
from uuid import NAMESPACE_URL, uuid4, uuid5

from ..database_connection import rows
from . import dashboard_capture
from . import environment_folder_profiles
from . import folder_discovery as legacy
from . import spdm_storage
from .folder_discovery_scan import MAX_SECONDS, relevant_content_fingerprint, root_identity, scan, stat_fingerprint
from .environment_folder_profiles import resolve_role
from . import usage_source_review

ENVIRONMENTS = ("USAGE", "DISTRIBUTION")
ROLES = {
    "USAGE": ("PROJECT", "REQUEST", "WORKING", "FINAL", "SIMULATION_CASE", "EVALUATION", "RESULTS", "INPUT", "CONTAINER"),
    "DISTRIBUTION": ("PROJECT", "REQUEST", "WORKING", "FINAL", "SIMULATION_CASE", "LOAD_CASE", "EXECUTION_RUN", "RUN_OPTION", "SCENE", "RESULTS", "INPUT", "CONTAINER"),
}
_EVALUATIONS = {"settle", "wobble", "horizontal_force_angle", "slope_angle", "slope_angle_360"}
_SCENE = re.compile(r"(scene|result|contour|animation)", re.I)
_logger = logging.getLogger(__name__)
# Revision of the refresh role interpretation stored in each snapshot. A
# snapshot from an older revision is re-interpreted once even when the folder
# fingerprints are unchanged, so a rule fix reaches already scanned folders.
# 2: undecided folders that only failed the name pattern may inherit a LEVEL role.
ROLE_RULES_REVISION = 2


def now(): return datetime.now(timezone.utc).replace(tzinfo=None)
def ident(prefix): return f"{prefix}-{uuid4().hex}"
def decoded(value): return json.loads(value) if isinstance(value, str) else value
def stable(prefix, root_key, path, role): return f"{prefix}-{uuid5(NAMESPACE_URL, root_key + ':' + path.casefold() + ':' + role).hex}"
def preview_data(value):
    value = decoded(value)
    return value if isinstance(value, dict) else {"rows": value, "usage_reviews": {}}


def _skip_final_archive(request_path: str | None = None):
    """Skip contents only for a request-direct Final folder."""
    confirmed_request = str(request_path or "").strip("/").casefold()

    def looks_like_request(path: str) -> bool:
        request_name = PurePosixPath(path).name
        if not (re.fullmatch(r"wr_[a-z0-9][a-z0-9._-]*_simtype[12]", request_name, re.I)
                or re.match(r"(?:\[)?wr[-_][a-z0-9]+(?:\])?(?:_|\s|$)", request_name, re.I)):
            return False
        parent_parts = [part for part in path.split("/") if part]
        project_name = parent_parts[-2] if len(parent_parts) > 1 else ""
        return (len(parent_parts) == 1
                or bool(re.match(r"^(?:project|prj|p)[_-]", project_name, re.I))
                or bool(re.fullmatch(r"[a-z0-9]{5}_pv", project_name, re.I)))

    def should_skip(relative_path: str, parent_path: str | None) -> bool:
        parts = [part for part in str(relative_path).strip("/").split("/") if part]
        for index, name in enumerate(parts):
            if name.casefold() != "final" or index == 0:
                continue
            final_parent = "/".join(parts[:index]).casefold()
            if confirmed_request:
                if final_parent == confirmed_request:
                    return True
            elif looks_like_request(final_parent):
                return True
        return False

    return should_skip


def _explicit_registration_roles(preview_value, request_path: str, environment: str,
                                 scan_path: str) -> dict[str, dict[str, Any]]:
    """Extract only explicit preview dispositions, excluding profile defaults."""
    from . import folder_schema_resolver as resolver

    preview = preview_data(preview_value)
    normalized_scan_path = resolver._normal(str(scan_path or ""), allow_root=True)
    explicit_paths = set()
    for item in [*(preview.get("rows") or []), *(preview.get("node_states") or [])]:
        if not isinstance(item, dict):
            continue
        path = str(item.get("relative_path") or "")
        explicit = (item.get("role_source") == "PREVIEW"
                    or item.get("role_basis") == "PREVIEW"
                    or item.get("status") == "EXCLUDED")
        if path and explicit and resolver._is_ancestor(normalized_scan_path, path):
            explicit_paths.add(resolver._fold(path))
    additions = resolver._preview_roles(preview, request_path, environment, "REGISTRATION")
    return {key: role for key, role in additions.items() if key in explicit_paths}


def _case_capture_payload(root, schema: dict, location_projection, case_relative_path: str,
                          usage_review: dict | None = None) -> dict:
    """Build a capture payload from the canonical scoped schema projection."""
    from . import folder_schema_resolver as resolver

    try:
        request_relative_path = resolver._normal(str(schema.get("request_relative_path") or ""))
        normalized_case_path = resolver._normal(str(case_relative_path or ""))
    except resolver.FolderSchemaError as exc:
        raise dashboard_capture.DashboardCaptureError(
            "CAPTURE_CONTEXT_MISMATCH", "수집 Case 경로를 선택한 의뢰와 연결할 수 없습니다. 의뢰별로 다시 조사하세요.",
        ) from exc
    case_node = next((node for node in schema.get("nodes", [])
                      if node.get("role_kind") == "SIMULATION_CASE"
                      and node.get("status") in {"CONFIRMED", "LINKED"}
                      and resolver._fold(str(node.get("relative_path") or ""))
                      == resolver._fold(normalized_case_path)), None)
    if (not resolver._is_ancestor(request_relative_path, normalized_case_path)
            or case_node is None):
        raise dashboard_capture.DashboardCaptureError(
            "CAPTURE_CONTEXT_MISMATCH", "수집 Case가 선택한 의뢰의 확인된 범위에 없습니다. 의뢰별로 다시 조사하세요.",
        )

    scene_ids = {str(item["relative_path"]).casefold(): str(item.get("scene_id") or item["id"])
                 for item in location_projection.locations if item.get("role_kind") == "SCENE"}
    assignments = []
    for node in schema.get("nodes", []):
        if node.get("status") not in {"CONFIRMED", "LINKED"}:
            continue
        relative_path = str(node.get("relative_path") or "")
        if not resolver._is_ancestor(case_relative_path, relative_path):
            continue
        role = str(node.get("role_kind") or "")
        if role not in {"SIMULATION_CASE", "LOAD_CASE", "EXECUTION_RUN", "RUN_OPTION", "SCENE", "RESULTS", "INPUT", "EVALUATION"}:
            continue
        target_id = scene_ids.get(relative_path.casefold(), node.get("target_id")) if role == "SCENE" else node.get("target_id")
        assignments.append({
            "relative_path": relative_path, "role_kind": role,
            "target_id": target_id, "parent_context_id": node.get("parent_context"),
            "raw_name": str(node.get("name") or PurePosixPath(relative_path).name),
            "option_status": node.get("option_status"),
        })
    assignments.sort(key=lambda item: (len(PurePosixPath(item["relative_path"]).parts), item["relative_path"].casefold()))
    run_option_labels = [item["raw_name"] for item in assignments
                         if item["role_kind"] == "RUN_OPTION"]
    case_locations = [dict(item) for item in location_projection.locations
                      if resolver._is_ancestor(case_relative_path, str(item.get("relative_path") or ""))]
    from .folder_schema_locations import blocked_paths_for_case
    blocked_paths = blocked_paths_for_case(schema, case_relative_path)
    return {
        "project_id": str(schema["project_id"]), "request_id": str(schema["request_id"]),
        "root_relative_path": case_relative_path, "environment": str(schema["environment"]),
        "storage_root_id": dashboard_capture._root_id(root),
        "simulation_case_id": dashboard_capture._case_id(dashboard_capture._root_id(root), case_relative_path),
        "run_option_labels": run_option_labels, "hierarchy_assignments": assignments,
        "folder_schema_locations": case_locations,
        "folder_schema_blocked_paths": blocked_paths,
        "folder_schema_scoped": True,
        "folder_schema_snapshot_id": location_projection.snapshot_id,
        "rule_profile_id": (schema.get("profile") or {}).get("id"),
        "rule_profile_version": (schema.get("profile") or {}).get("revision"),
        "usage_source_review": usage_review,
    }


def profiles(conn):
    records = rows(conn.execute("SELECT id,environment,name,revision,rules_json,created_at,updated_at FROM folder_environment_profiles ORDER BY environment,name"))
    items = []
    for record in records:
        item = dict(record)
        item["rules"] = decoded(item.pop("rules_json"))
        metadata = item["rules"].get("profile_metadata", {}) if isinstance(item["rules"], dict) else {}
        if isinstance(metadata, dict) and metadata.get("archived"):
            continue
        items.append(item)
    return {"items": items}


def default_profile(conn, environment: str):
    for row in conn.execute("SELECT id,revision,rules_json FROM folder_environment_profiles WHERE environment=? ORDER BY created_at", [environment]).fetchall():
        definition = decoded(row[2])
        metadata = definition.get("profile_metadata", {}) if isinstance(definition, dict) else {}
        if not (isinstance(metadata, dict) and metadata.get("archived")):
            return {"id": str(row[0]), "revision": int(row[1]), "rules": definition}
    raise ValueError("활성 환경 규칙이 없습니다. 새 규칙을 저장하세요.")


def refresh_scope(conn, root, project_id: str, request_id: str, environment: str,
                  actor: str, *, capture_cases: bool = True) -> dict:
    """Rescan one request and atomically activate its canonical Folder Schema snapshot."""
    from . import folder_schema_resolver as resolver

    environment = str(environment).upper()
    if environment not in ENVIRONMENTS:
        raise resolver.FolderSchemaError("FOLDER_SCHEMA_ENVIRONMENT_INVALID", "지원하지 않는 폴더 환경입니다.")
    root_key = root_identity(root)
    request_path = resolver._request_path(conn, root_key, project_id, request_id, environment)
    previous = resolver.active_refresh_snapshot(conn, root_key, project_id, request_id, environment)
    if previous:
        previous_schema = resolver._decode(previous["schema_json"], code="FOLDER_SCHEMA_SNAPSHOT_INVALID",
                                           message="저장된 폴더 구조를 읽을 수 없습니다.")
        profile_id = str(previous["profile_id"])
    else:
        previous_schema = resolver.resolve_request_schema(conn, root, root_key, project_id, request_id, environment)
        profile_id = str(previous_schema["profile"]["id"])

    profile_row = conn.execute(
        "SELECT id,environment,name,revision,rules_json FROM folder_environment_profiles WHERE id=?",
        [profile_id],
    ).fetchone()
    if not profile_row or str(profile_row[1]) != environment:
        raise resolver.FolderSchemaError("FOLDER_SCHEMA_PROFILE_MISSING", "현재 폴더 규칙을 찾을 수 없습니다.")
    try:
        rules = environment_folder_profiles.validate_rules(environment, decoded(profile_row[4]))
    except (TypeError, ValueError) as exc:
        raise resolver.FolderSchemaError("FOLDER_SCHEMA_PROFILE_INVALID", "저장 규칙 형식이 올바르지 않습니다.") from exc
    profile = {"id": str(profile_row[0]), "revision": int(profile_row[3]),
               "name": str(profile_row[2] or ""), "rules": rules}

    diff_baseline_nodes = list(previous_schema.get("nodes") or []) if previous else []
    registration_roles_changed = False
    if previous and int(previous["profile_revision"]) != profile["revision"]:
        registered = conn.execute(
            "SELECT s.id,s.relative_path,p.rows_json FROM folder_environment_scans s "
            "JOIN folder_environment_previews p ON p.scan_id=s.id "
            "JOIN folder_environment_registrations r ON r.preview_id=p.id "
            "WHERE s.root_key=? AND s.environment=? AND s.profile_id=? AND s.profile_revision=? "
            "AND s.status='COMPLETE' AND r.project_id=? AND r.request_id=? AND r.environment=? "
            "AND r.status IN ('REGISTERED','CAPTURING','COMPLETED','FAILED') AND r.created_at>? "
            "ORDER BY r.created_at DESC,r.id DESC LIMIT 1",
            [root_key, environment, profile_id, profile["revision"], project_id, request_id,
             environment, previous["created_at"]],
        ).fetchone()
        if not registered:
            raise resolver.FolderSchemaError(
                "FOLDER_SCHEMA_PROFILE_REVISION_CHANGED",
                "폴더 규칙이 변경되었습니다. 새 규칙으로 스키마를 다시 등록한 뒤 새로고침하세요.",
            )
        # A completed registration with the new revision is the explicit
        # approval boundary for changing the active profile. Keep the previous
        # snapshot active until this subsequent refresh succeeds.
        previous_schema = resolver.resolve_request_schema(
            conn, root, root_key, project_id, request_id, environment,
            registered_scan_id=str(registered[0]),
        )
        profile = previous_schema["profile"]
    elif previous:
        registered = conn.execute(
            "SELECT s.id,s.relative_path,p.rows_json FROM folder_environment_scans s "
            "JOIN folder_environment_previews p ON p.scan_id=s.id "
            "JOIN folder_environment_registrations r ON r.preview_id=p.id "
            "WHERE s.root_key=? AND s.environment=? AND s.profile_id=? AND s.profile_revision=? "
            "AND s.status='COMPLETE' AND r.project_id=? AND r.request_id=? AND r.environment=? "
            "AND r.status IN ('REGISTERED','CAPTURING','COMPLETED','FAILED') AND r.created_at>? "
            "ORDER BY r.created_at,r.id",
            [root_key, environment, profile_id, profile["revision"], project_id, request_id,
             environment, previous["created_at"]],
        ).fetchall()
        if registered:
            original_confirmed = dict(previous_schema.get("confirmed_roles") or {})
            prior_confirmed = dict(original_confirmed)
            for _scan_id, scan_path, saved_preview in registered:
                additions = _explicit_registration_roles(
                    saved_preview, request_path, environment, str(scan_path or ""),
                )
                for key, role in additions.items():
                    prior_confirmed[key] = role
            role_fields = ("relative_path", "role_kind", "status", "target_id", "name",
                           "option_status", "source", "role_basis")
            all_role_keys = set(original_confirmed) | set(prior_confirmed)
            registration_roles_changed = any(
                tuple((original_confirmed.get(key) or {}).get(field) for field in role_fields)
                != tuple((prior_confirmed.get(key) or {}).get(field) for field in role_fields)
                for key in all_role_keys
            )
            previous_schema = {**previous_schema, "confirmed_roles": prior_confirmed}

    refresh_deadline = time.monotonic() + MAX_SECONDS
    try:
        fresh = scan(root, request_path, skip_descendants=_skip_final_archive(request_path))
    except (OSError, ValueError, spdm_storage.SpdmStorageError) as exc:
        raise resolver.FolderSchemaError("FOLDER_SCHEMA_SCAN_UNAVAILABLE", "현재 의뢰 폴더를 안전하게 조사할 수 없습니다.", 422) from exc
    if fresh.get("status") != "COMPLETE":
        raise resolver.FolderSchemaError("FOLDER_SCHEMA_SCAN_INCOMPLETE", "현재 의뢰 폴더를 모두 확인할 수 없습니다.", 422)
    content_entries: list = []
    structure_fingerprint, content_fingerprint = resolver.scan_fingerprints(
        fresh, root, deadline=refresh_deadline, content_entries=content_entries,
    )
    quick_fingerprint = stat_fingerprint(fresh)
    result_content_fingerprint = relevant_content_fingerprint(content_entries)

    if (previous and not registration_roles_changed
            and int(previous["profile_revision"]) == profile["revision"]
            and str(previous["request_relative_path"]) == request_path
            and str(previous["structure_fingerprint"]) == structure_fingerprint
            and str(previous["content_fingerprint"]) == content_fingerprint
            and isinstance(previous_schema, dict)
            and previous_schema.get("role_rules_revision") == ROLE_RULES_REVISION):
        snapshot_id = str(previous["id"])
        location_projection = resolver.resolve_request_locations(
            conn, project_id, request_id, environment, schema=previous_schema,
        )
        return {**_refresh_result(snapshot_id, "UNCHANGED", False, previous_schema,
                                  structure_fingerprint, content_fingerprint,
                                  {"added": 0, "removed": 0, "changed": 0}, location_projection),
                "stat_fingerprint": quick_fingerprint}

    prior_nodes = list(previous_schema.get("nodes") or []) if isinstance(previous_schema, dict) else []
    prior_by_path = {resolver._fold(str(node.get("relative_path") or "")): node for node in prior_nodes}
    # The legacy resolver reconstructs current roles from the latest registered
    # preview, but its node list is based on a fresh scan. On the first refresh,
    # use that registered scan's frozen tree to tell genuinely new siblings
    # from old, still-unassigned containers.
    prior_scanned_paths = set(prior_by_path)
    if previous:
        prior_scanned_paths = {
            resolver._fold(str(node.get("relative_path") or "")) for node in prior_nodes
        }
    else:
        registered_scan = conn.execute(
            "SELECT s.tree_json FROM folder_environment_registrations r "
            "JOIN folder_environment_previews p ON p.id=r.preview_id "
            "JOIN folder_environment_scans s ON s.id=p.scan_id "
            "WHERE s.root_key=? AND r.project_id=? AND r.request_id=? AND r.environment=? "
            "AND r.status IN ('REGISTERED','CAPTURING','COMPLETED','FAILED') "
            "ORDER BY r.created_at DESC,r.id DESC LIMIT 1",
            [root_key, project_id, request_id, environment],
        ).fetchone()
        if registered_scan:
            registered_tree = resolver._decode(
                registered_scan[0], code="FOLDER_SCHEMA_SNAPSHOT_INVALID",
                message="저장된 폴더 조사 결과를 읽을 수 없습니다.",
            )
            if isinstance(registered_tree, list):
                prior_scanned_paths = {
                    resolver._fold(str(node.get("relative_path") or ""))
                    for node in registered_tree if isinstance(node, dict)
                    and node.get("relative_path")
                }
    current_nodes = _interpret(
        fresh["nodes"], root_key, environment, project_id, request_id, rules,
        seed_request_path=request_path,
    )
    for node in current_nodes:
        if node.get("role_kind") and node.get("role_basis") == "DEFAULT":
            node["role_basis"] = "PATTERN"
    scoped_nodes = [node for node in current_nodes
                    if resolver._is_ancestor(request_path, str(node.get("relative_path") or ""))]
    current_by_path = {resolver._fold(str(node.get("relative_path") or "")): node for node in scoped_nodes}

    # Reapply confirmed dispositions to paths that still exist. Pattern rules
    # are evaluated first; a saved manual assignment or exclusion is durable.
    confirmed = previous_schema.get("confirmed_roles", {}) if isinstance(previous_schema, dict) else {}
    if not isinstance(confirmed, dict):
        confirmed = {}
    for key, saved in confirmed.items():
        node = current_by_path.get(str(key))
        if node is None or not isinstance(saved, dict):
            continue
        status = str(saved.get("status") or "")
        role = str(saved.get("role_kind") or "")
        if status == "EXCLUDED" or role in {"EXCLUDE", "EXCLUDED"}:
            node.update(role_kind=None, status="EXCLUDED", confirmed=True,
                        role_basis="EXCLUDED", role_source="MANUAL")
            continue
        if status not in {"CONFIRMED", "CONTAINER", "LINKED"} or not role:
            continue
        previous_basis = str(saved.get("role_basis") or "")
        if previous_basis == "RULE":
            previous_basis = "PATTERN"
        node.update(role_kind=role, status="CONFIRMED" if role != "CONTAINER" else "CONTAINER",
                    target_id=saved.get("target_id") or node.get("target_id"),
                    name=saved.get("name") or node.get("name"), confirmed=True,
                    role_basis=previous_basis if previous_basis in {"LEVEL", "PATTERN"} else "MANUAL",
                    role_source="MANUAL", role_evidence_source=saved.get("source") or "REGISTRATION")

    # New sibling nodes inherit only when all confirmed prior nodes at the
    # same semantic parent role and request-relative depth agree on one role.
    def nearest_parent_role(node: dict, lookup: dict[str, dict]) -> str | None:
        parent = str(node.get("parent_path") or "")
        while parent and resolver._is_ancestor(request_path, parent):
            found = lookup.get(resolver._fold(parent))
            if found and found.get("role_kind") and found.get("status") not in {"EXCLUDED", "UNRESOLVED"}:
                return str(found["role_kind"])
            if resolver._fold(parent) == resolver._fold(request_path):
                break
            parent = str(found.get("parent_path") or "") if found else PurePosixPath(parent).parent.as_posix()
            if parent == ".":
                parent = ""
        return None

    request_depth = len(PurePosixPath(request_path).parts)
    inherited_roles: dict[tuple[str | None, int], set[str]] = {}
    for old_node in prior_nodes:
        if old_node.get("status") not in {"CONFIRMED", "LINKED"}:
            continue
        role = str(old_node.get("role_kind") or "")
        if not role:
            continue
        path = str(old_node.get("relative_path") or "")
        relative_depth = len(PurePosixPath(path).parts) - request_depth
        parent_role = nearest_parent_role(old_node, prior_by_path)
        inherited_roles.setdefault((parent_role, relative_depth), set()).add(role)

    for node in scoped_nodes:
        path_key = resolver._fold(str(node.get("relative_path") or ""))
        # A folder that only failed the name pattern (DEFAULT basis, no role,
        # no saved decision) is still undecided. It may inherit the single
        # role used at its parent role/depth even when an earlier scan already
        # saw it, so a Scene created empty and refreshed later is not stuck.
        undecided = (not node.get("role_kind") and node.get("status") == "UNRESOLVED"
                     and node.get("role_basis") == "DEFAULT")
        if (path_key in prior_scanned_paths and not undecided) or node.get("role_basis") == "RULE":
            if node.get("role_basis") == "RULE" and node.get("status") == "UNRESOLVED":
                node["role_basis"] = "CONFLICT"
            elif node.get("role_basis") == "RULE":
                node["role_basis"] = "PATTERN"
                node["confirmed"] = True
            continue
        if (node.get("status") != "CONTAINER" and not undecided) or node.get("role_kind"):
            continue
        relative_depth = len(PurePosixPath(str(node["relative_path"])).parts) - request_depth
        parent_role = nearest_parent_role(node, current_by_path)
        possibilities = inherited_roles.get((parent_role, relative_depth), set())
        if undecided and (len(possibilities) != 1 or "SCENE" not in possibilities):
            # Undecided folders only gain the Scene role (a user adding a Scene
            # beside existing Scenes, with or without a Run option level). Any
            # other or ambiguous level keeps its prior UNRESOLVED state and must
            # never turn into a refresh-blocking CONFLICT.
            continue
        if len(possibilities) == 1:
            role = next(iter(possibilities))
            node.update(role_kind=role, status="CONFIRMED", confirmed=True,
                        role_basis="LEVEL", role_source="INHERITED")
            if undecided:
                node.pop("message", None)
                if node.get("option_status") == "UNRESOLVED":
                    node["option_status"] = "PRESENT" if role == "RUN_OPTION" else None
            if role == "SCENE" and not node.get("target_id"):
                node["target_id"] = stable("environment-scene", root_key, node["relative_path"], role)
        elif len(possibilities) > 1:
            node.update(role_kind=None, status="UNRESOLVED", confirmed=False,
                        role_basis="CONFLICT", role_source="INHERITED",
                        message="같은 상위 역할·깊이에 서로 다른 역할이 확인되어 새 폴더의 역할을 정할 수 없습니다.")

    # A previously excluded path also excludes newly created descendants.
    excluded_paths = [str(node["relative_path"]) for node in scoped_nodes if node.get("status") == "EXCLUDED"]
    for node in scoped_nodes:
        if any(resolver._is_ancestor(path, str(node["relative_path"])) for path in excluded_paths):
            node.update(role_kind=None, status="EXCLUDED", confirmed=True,
                        role_basis="EXCLUDED", role_source="MANUAL")
    _recompute_context(scoped_nodes, root_key, environment, project_id, request_id)
    for node in scoped_nodes:
        if node.get("role_kind") == "SCENE" and not node.get("target_id"):
            node["target_id"] = stable("environment-scene", root_key, node["relative_path"], "SCENE")
    resolver._add_hierarchy(scoped_nodes)

    confirmed_roles_now = {}
    for node in scoped_nodes:
        if node.get("role_kind") and node.get("status") in {"CONFIRMED", "LINKED", "CONTAINER"}:
            confirmed_roles_now[resolver._fold(str(node["relative_path"]))] = {
                "relative_path": node["relative_path"], "role_kind": node["role_kind"],
                "status": node["status"], "target_id": node.get("target_id"),
                "name": node.get("name"), "source": node.get("role_source"),
                "role_basis": node.get("role_basis"),
            }
        elif node.get("status") == "EXCLUDED":
            confirmed_roles_now[resolver._fold(str(node["relative_path"]))] = {
                "relative_path": node["relative_path"], "role_kind": "EXCLUDE",
                "status": "EXCLUDED", "name": node.get("name"),
                "source": node.get("role_source"), "role_basis": "EXCLUDED",
            }
    schema = {
        "project_id": project_id, "request_id": request_id, "environment": environment,
        "request_relative_path": request_path, "profile": profile,
        "scan": {"id": "pending", "relative_path": request_path, "profile_id": profile["id"],
                 "profile_revision": profile["revision"], "status": "COMPLETE"},
        "nodes": scoped_nodes, "confirmed_roles": confirmed_roles_now,
        "role_rules_revision": ROLE_RULES_REVISION,
        # Quick auto-sync check (names, sizes, mtimes only); see folder_auto_sync.
        "stat_fingerprint": quick_fingerprint,
        # Result-relevant files by content; unread files (logs) do not count.
        "result_content_fingerprint": result_content_fingerprint,
        "issues": fresh.get("issues", []),
        "structure_fingerprint": structure_fingerprint,
        "content_fingerprint": content_fingerprint,
    }
    diff = _refresh_diff(diff_baseline_nodes if previous else prior_nodes, scoped_nodes, resolver)
    if any(node.get("role_basis") == "CONFLICT" and node.get("status") == "UNRESOLVED"
           for node in scoped_nodes):
        location_projection = resolver.resolve_request_locations(
            conn, project_id, request_id, environment, schema=schema,
        )
        return {**_refresh_result(
            str(previous["id"]) if previous else None, "CONFLICT", False, schema,
            structure_fingerprint, content_fingerprint, diff, location_projection,
            activated=False,
        ), "stat_fingerprint": quick_fingerprint}
    # Results are unchanged when no node changed and the result-relevant files
    # kept their size/mtime (only logs or other unread files changed), or the
    # whole content fingerprint is identical (a role-rule revision re-read).
    revision_only_unchanged = bool(
        previous and not registration_roles_changed
        and int(previous["profile_revision"]) == profile["revision"]
        and str(previous["request_relative_path"]) == request_path
        and str(previous["structure_fingerprint"]) == structure_fingerprint
        and (str(previous["content_fingerprint"]) == content_fingerprint
             or (isinstance(previous_schema, dict)
                 and previous_schema.get("result_content_fingerprint") == result_content_fingerprint))
        and not any(diff.values())
    )
    snapshot_id = ident("folder-refresh")
    schema["scan"]["id"] = snapshot_id
    snapshot = {
        "kind": "FOLDER_SCHEMA_REFRESH", "version": 1,
        "structure_fingerprint": structure_fingerprint,
        "content_fingerprint": content_fingerprint,
        "schema": schema,
    }
    encoded_snapshot = json.dumps(snapshot, ensure_ascii=False)
    created_at = now()
    if previous and previous.get("created_at") and previous["created_at"] >= created_at:
        from datetime import timedelta
        created_at = previous["created_at"] + timedelta(microseconds=1)
    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute(
            "INSERT INTO folder_environment_scans(id,root_key,relative_path,environment,profile_id,profile_revision,"
            "project_id,request_id,status,tree_json,issues_json,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [snapshot_id, root_key, request_path, environment, profile["id"], profile["revision"],
             project_id, request_id, "COMPLETE", encoded_snapshot,
             json.dumps(fresh.get("issues", []), ensure_ascii=False), actor, created_at],
        )
        location_projection = resolver.resolve_request_locations(
            conn, project_id, request_id, environment, schema=schema,
        )
        capture_errors = []
        if capture_cases and not revision_only_unchanged:
            storage_root_id = dashboard_capture._root_id(root)
            for case_node in scoped_nodes:
                if case_node.get("role_kind") != "SIMULATION_CASE" or case_node.get("status") != "CONFIRMED":
                    continue
                case_path = str(case_node.get("relative_path") or "")
                case_id = dashboard_capture._case_id(storage_root_id, case_path)
                if not conn.execute(
                    "SELECT 1 FROM dashboard_cases WHERE id=? AND project_id=? AND request_id=? AND environment=?",
                    [case_id, project_id, request_id, environment],
                ).fetchone():
                    continue
                try:
                    dashboard_capture.create_capture(
                        conn,
                        _case_capture_payload(root, schema, location_projection, case_path),
                        actor=actor,
                    )
                except dashboard_capture.DashboardCaptureError as exc:
                    capture_errors.append(exc.code)
        if capture_errors:
            raise resolver.FolderSchemaError(
                "FOLDER_SCHEMA_CAPTURE_FAILED",
                "폴더 새로고침 중 결과 캡처를 완료하지 못해 이전 스냅샷을 유지했습니다.",
                422,
            )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {**_refresh_result(snapshot_id, "REFRESHED", not revision_only_unchanged, schema,
                              structure_fingerprint, content_fingerprint, diff, location_projection),
            "stat_fingerprint": quick_fingerprint}


def _refresh_diff(previous_nodes: list[dict], current_nodes: list[dict], resolver) -> dict[str, int]:
    old = {resolver._fold(str(item.get("relative_path") or "")): item for item in previous_nodes}
    new = {resolver._fold(str(item.get("relative_path") or "")): item for item in current_nodes}
    added = set(new) - set(old)
    removed = set(old) - set(new)
    changed = 0
    for key in set(old) & set(new):
        before, after = old[key], new[key]
        if any(before.get(field) != after.get(field) for field in (
                "role_kind", "status", "target_id", "identity", "name")):
            changed += 1
    return {"added": len(added), "removed": len(removed), "changed": changed}


def _refresh_result(snapshot_id, status, changed, schema, structure_fingerprint,
                    content_fingerprint, diff, location_projection, *, activated=None):
    return {
        "snapshot_id": snapshot_id,
        "project_id": schema["project_id"], "request_id": schema["request_id"],
        "environment": schema["environment"], "status": status, "changed": changed,
        "activated": status != "CONFLICT" if activated is None else activated,
        "structure_fingerprint": structure_fingerprint,
        "content_fingerprint": content_fingerprint, "diff": diff,
        "nodes": [{key: node.get(key) for key in (
            "relative_path", "parent_path", "name", "depth", "role_kind", "status",
            "role_basis", "target_id", "hierarchy", "message") if key in node}
            for node in schema.get("nodes", [])],
        "locations": [dict(item) for item in location_projection.locations],
    }


def _skip_with_boundaries(base, skip_paths):
    """Also skip the descendants of explicit boundary folders (e.g. sibling requests)."""
    folded = {str(path).strip("/").casefold() for path in (skip_paths or ()) if str(path).strip("/")}
    if not folded:
        return base

    def should_skip(relative_path: str, parent_path: str | None) -> bool:
        return str(relative_path).strip("/").casefold() in folded or base(relative_path, parent_path)

    return should_skip


def save_scan(conn, root, relative_path: str, environment: str, profile_id: str | None, project_id: str | None, request_id: str | None, actor: str,
              *, skip_paths=None):
    if environment not in ENVIRONMENTS: raise ValueError("지원하지 않는 환경입니다.")
    if request_id and not project_id:
        found = conn.execute("SELECT project_id FROM analysis_requests WHERE id=?", [request_id]).fetchone()
        if not found: raise ValueError("의뢰를 찾을 수 없습니다.")
        project_id = str(found[0])
    if request_id:
        found = conn.execute("SELECT 1 FROM analysis_requests WHERE id=? AND project_id=?", [request_id, project_id]).fetchone()
        if not found: raise ValueError("프로젝트와 의뢰의 연결이 일치하지 않습니다.")
    profile = default_profile(conn, environment) if not profile_id else _profile(conn, profile_id, environment)
    root_key = root_identity(root)
    request_path = None
    if request_id and project_id:
        try:
            from . import folder_schema_resolver as resolver
            request_path = resolver._request_path(conn, root_key, project_id, request_id, environment)
        except (ValueError, KeyError):
            request_path = None
    result = scan(root, relative_path, skip_descendants=_skip_with_boundaries(_skip_final_archive(request_path), skip_paths))
    nodes = _interpret(result["nodes"], root_key, environment, project_id, request_id,
                       profile["rules"], seed_request_path=request_path)
    scan_id = ident("environment-scan")
    conn.execute("INSERT INTO folder_environment_scans(id,root_key,relative_path,environment,profile_id,profile_revision,project_id,request_id,status,tree_json,issues_json,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 [scan_id, root_key, relative_path, environment, profile["id"], profile["revision"], project_id, request_id, result["status"], json.dumps(nodes, ensure_ascii=False), json.dumps(result["issues"], ensure_ascii=False), actor, now()])
    return {"id": scan_id, "environment": environment, "profile_id": profile["id"], "profile_revision": profile["revision"], "usage_sources": profile["rules"].get("usage_sources"), "relative_path": relative_path, "status": result["status"], "nodes": nodes, "issues": result["issues"]}


def _profile(conn, profile_id, environment):
    row = conn.execute("SELECT id,revision,rules_json,environment FROM folder_environment_profiles WHERE id=?", [profile_id]).fetchone()
    if not row or str(row[3]) != environment: raise ValueError("환경 규칙 프로파일을 찾을 수 없습니다.")
    return {"id": str(row[0]), "revision": int(row[1]), "rules": decoded(row[2])}


def _interpret(raw, root_key, environment, project_id, request_id, rules=None, *, seed_request_path=None):
    context, out = {}, []
    seeded_request = {"role_kind": "REQUEST", "target_id": request_id,
                      "project_id": project_id, "request_id": request_id}
    if seed_request_path and raw:
        scan_root = str(raw[0].get("relative_path") or "").casefold()
        request_path = str(seed_request_path).casefold()
        if request_path != scan_root and (not request_path or scan_root.startswith(request_path.rstrip("/") + "/")):
            # A scan may start at a Case or deeper folder inside an existing
            # request. Seed its missing parent context so parent_role rules
            # still resolve against the selected Request.
            context[None] = seeded_request
            context[""] = seeded_request
            request_parts = [part for part in str(seed_request_path).split("/") if part]
            scan_parts = [part for part in str(raw[0].get("relative_path") or "").split("/") if part]
            if len(scan_parts) > len(request_parts):
                boundary = scan_parts[len(request_parts)]
                if boundary.casefold() == "working":
                    anchor = "/".join(scan_parts[:len(request_parts) + 1])
                    context[None]["working_relative_path"] = anchor
                    context[""]["working_relative_path"] = anchor
                    context[anchor] = {**seeded_request, "role_kind": "WORKING",
                                       "working_relative_path": anchor}
                elif boundary.casefold() == "final":
                    anchor = "/".join(scan_parts[:len(request_parts) + 1])
                    context[None]["final_relative_path"] = anchor
                    context[""]["final_relative_path"] = anchor
                    context[anchor] = {**seeded_request, "role_kind": "FINAL",
                                       "final_relative_path": anchor}
    for source in raw:
        node = dict(source); parent = context.get(node["parent_path"], {})
        name = node["name"]; folded = name.casefold()
        working_path = parent.get("working_relative_path")
        working_level = (len([part for part in str(node.get("relative_path") or "").split("/") if part])
                         - len([part for part in str(working_path or "").split("/") if part])) if working_path else None
        _, profile_matched, profile_conflict = resolve_role(
            name, node["depth"], parent.get("role_kind"), rules, environment,
            working_level=working_level,
        ) if rules else (None, False, False)
        role, option_status = _role(environment, name, parent, node["depth"], rules, working_level=working_level)
        in_final = bool(parent.get("final_relative_path"))
        if in_final:
            role, option_status = "CONTAINER", None
        elif name.casefold() == "working" and parent.get("role_kind") == "REQUEST":
            role, option_status = "WORKING", None
        elif name.casefold() == "final" and parent.get("role_kind") == "REQUEST":
            role, option_status = "FINAL", None
        elif (not parent.get("role_kind") and node.get("relative_path")
              and re.fullmatch(r"[A-Z0-9]{5}_PV", name, re.I)):
            # The storage root has an empty relative path and is never a Project.
            role, option_status = "PROJECT", None
        is_request_boundary = bool(seed_request_path and str(node.get("relative_path", "")).casefold() == str(seed_request_path).casefold())
        if is_request_boundary:
            role, option_status = "REQUEST", None
        # A node id represents a filesystem node, not its current role.  This
        # keeps an explicit assignment valid when its suggested role changes.
        node_id = stable("environment-node", root_key, node["relative_path"], "NODE")
        status = "CONFIRMED" if role else ("UNRESOLVED" if option_status == "UNRESOLVED" else "CONTAINER")
        role_basis = ("REQUEST_BOUNDARY" if is_request_boundary else
                      "RULE" if profile_matched or profile_conflict else "DEFAULT")
        item = {**node, "id": node_id, "environment": environment, "role_kind": role, "allowed_roles": ROLES[environment], "status": status, "parent_context": parent.get("target_id"), "project_id": project_id or parent.get("project_id"), "request_id": request_id or parent.get("request_id"), "option_status": option_status, "option_label": name if role == "RUN_OPTION" else None, "role_source": "PROFILE", "role_basis": role_basis}
        if working_path:
            item["_working_relative_path"] = working_path
        if parent.get("final_relative_path"):
            item["_final_relative_path"] = parent["final_relative_path"]
        if status == "UNRESOLVED":
            item["message"] = "환경 규칙과 일치하지 않는 폴더입니다. 역할을 확인하세요."
            if re.match(r"^WR_[A-Za-z0-9._-]+_SimType[12]$", name, re.I):
                item["message"] = "선택한 환경과 SimType 폴더가 일치하지 않습니다."
        if parent.get("role_kind") == "SIMULATION_CASE":
            item["simulation_case_id"] = parent.get("target_id")
        if role == "PROJECT":
            item["target_id"] = stable("environment-project", root_key, node["relative_path"], role)
            item["project_id"] = item["target_id"]
        if role == "REQUEST":
            item["target_id"] = request_id if is_request_boundary else stable("environment-request", root_key, node["relative_path"], role)
            item["request_id"] = item["target_id"]
        if role == "SIMULATION_CASE":
            item["target_id"] = dashboard_capture._case_id("dashboard-root-" + root_key, node["relative_path"])
        elif role in {"EXECUTION_RUN", "LOAD_CASE"}:
            item["target_id"] = stable("environment-" + role.casefold(), root_key, node["relative_path"], role)
        if role == "RUN_OPTION": item["run_option_id"] = stable("environment-option", root_key, node["relative_path"], role)
        out.append(item)
        next_context = dict(parent)
        if role in {"PROJECT", "REQUEST", "WORKING", "FINAL", "SIMULATION_CASE", "EVALUATION", "LOAD_CASE", "EXECUTION_RUN", "RUN_OPTION"}: next_context.update(item)
        if role == "PROJECT": next_context.pop("request_id", None)
        if role == "WORKING": next_context["working_relative_path"] = node["relative_path"]
        if role == "FINAL": next_context["final_relative_path"] = node["relative_path"]
        context[node["relative_path"]] = next_context
    return out


def _role(environment, name, parent, depth=0, rules=None, *, working_level=None):
    lowered = name.casefold()
    if rules:
        resolved, matched, conflict = resolve_role(name, depth, parent.get("role_kind"), rules, environment, working_level=working_level)
        if conflict:
            return None, "UNRESOLVED"
        if matched:
            return (resolved, "PRESENT" if resolved == "RUN_OPTION" else None)
    if lowered == "working" and parent.get("role_kind") == "REQUEST": return "WORKING", None
    if lowered == "final" and parent.get("role_kind") == "REQUEST": return "FINAL", None
    # Containers intentionally pass their context through.  Only recognizable
    # semantic folders receive an automatic role; ambiguous folders stay for
    # explicit confirmation in the preview.
    if re.match(r"^(project|prj|p)[_-]", name, re.I): return "PROJECT", None
    match = re.match(r"^WR_[A-Za-z0-9._-]+_SimType([12])$", name, re.I)
    if match:
        expected = "1" if environment == "USAGE" else "2"
        return ("REQUEST", None) if match.group(1) == expected else (None, "UNRESOLVED")
    if (re.match(r"^WR[-_][A-Za-z0-9]+(?:_|\s|$)", name, re.I)
            or re.match(r"^\[WR[-_][A-Za-z0-9]+\](?:_|\s|$)", name, re.I)):
        return "REQUEST", None
    if re.match(r"^(assy_res|package)[_-]", name, re.I): return "SIMULATION_CASE", None
    if environment == "DISTRIBUTION" and lowered in {"result", "results"}:
        if parent.get("role_kind") in {"SCENE", "RUN_OPTION", "EXECUTION_RUN"}:
            return "RESULTS", None
        return None, None
    if environment == "DISTRIBUTION" and _SCENE.search(name): return "SCENE", "PRESENT"
    if environment == "USAGE":
        if parent.get("role_kind") == "SIMULATION_CASE" and lowered in _EVALUATIONS: return "EVALUATION", None
        return ("RESULTS", None) if parent.get("role_kind") == "EVALUATION" else (None, None)
    if parent.get("role_kind") == "SIMULATION_CASE": return "LOAD_CASE", None
    if parent.get("role_kind") == "LOAD_CASE": return "EXECUTION_RUN", None
    if parent.get("role_kind") == "EXECUTION_RUN":
        if _SCENE.match(name): return "SCENE", "ABSENT"
        if lowered in {"individual", "cumulative"}: return "RUN_OPTION", "PRESENT"
        return None, "UNRESOLVED"
    if parent.get("role_kind") == "RUN_OPTION": return ("SCENE", "PRESENT") if _SCENE.match(name) else (None, "UNRESOLVED")
    return None, None


def _expanded_preview_assignments(nodes, assignments):
    """Propagate one structural assignment across its Working-relative level.

    A direct assignment is applied after propagation, so an explicitly chosen
    per-folder exception always wins regardless of request order.
    """
    by_id = {node["id"]: node for node in nodes}
    by_path = {node["relative_path"]: node for node in nodes}
    propagated, direct = {}, {}

    def working_anchor(node):
        if node.get("_final_relative_path"):
            return None
        parent = by_path.get(node.get("parent_path"))
        while parent:
            if parent.get("role_kind") == "FINAL":
                return None
            if parent.get("role_kind") == "WORKING":
                return parent
            parent = by_path.get(parent.get("parent_path"))
        if node.get("_working_relative_path"):
            return {"id": "working-anchor:" + str(node["_working_relative_path"]).casefold(),
                    "relative_path": str(node["_working_relative_path"]), "role_kind": "WORKING"}
        return None

    for assignment in assignments:
        node = by_id.get(assignment.get("node_id"))
        if not node:
            continue
        if assignment.get("propagate_same_level", False) and assignment.get("role_kind") in {
            "SIMULATION_CASE", "LOAD_CASE", "EXECUTION_RUN", "RUN_OPTION", "SCENE",
        }:
            anchor = working_anchor(node)
            if anchor:
                anchor_depth = len([part for part in anchor["relative_path"].split("/") if part])
                target_depth = len([part for part in node["relative_path"].split("/") if part])
                level = target_depth - anchor_depth
                for candidate in nodes:
                    if candidate is node or candidate.get("status") == "EXCLUDED":
                        continue
                    candidate_anchor = working_anchor(candidate)
                    if not candidate_anchor or candidate_anchor["id"] != anchor["id"]:
                        continue
                    candidate_depth = len([part for part in candidate["relative_path"].split("/") if part])
                    if candidate_depth - anchor_depth == level:
                        propagated[candidate["id"]] = {**assignment, "node_id": candidate["id"]}
        direct[node["id"]] = assignment
    return [*propagated.values(), *direct.values()]


def preview(conn, scan_id, assignments, actor, require_usage_review=False, *, allow_without_cases=False):
    """Build a registration preview.

    ``allow_without_cases`` lets automatic discovery register a new request whose
    Working folder has no Case yet; every other blocker still applies.
    """
    records = rows(conn.execute("SELECT id,root_key,environment,project_id,request_id,status,tree_json FROM folder_environment_scans WHERE id=?", [scan_id]))
    if not records: legacy.fail("ENVIRONMENT_SCAN_NOT_FOUND", "환경 조사 결과를 찾을 수 없습니다.", 404)
    saved = records[0]; nodes = decoded(saved["tree_json"])
    if not isinstance(nodes, list):
        raise ValueError("새로고침 스냅샷은 조사 미리보기로 사용할 수 없습니다.")
    by_id = {node["id"]: node for node in nodes}
    for assignment in _expanded_preview_assignments(nodes, assignments):
        node = by_id.get(assignment.get("node_id")); role = assignment.get("role_kind")
        if not node or role not in {*ROLES[saved["environment"]], "EXCLUDE"}: raise ValueError("조사 트리에 없는 역할 지정입니다.")
        node["role_kind"], node["status"] = role, "EXCLUDED" if role == "EXCLUDE" else ("CONFIRMED" if assignment.get("confirm", True) else "UNRESOLVED")
        if assignment.get("confirm", True):
            # Scan-time role diagnoses describe the suggested classification.
            # A manual confirmation supersedes that diagnosis; preview below
            # will add a fresh message if the selected role breaks hierarchy.
            node.pop("message", None)
        node["role_source"] = "PREVIEW"
        node["role_basis"] = "PREVIEW"
        if assignment.get("target_mode") == "LINK": node["target_id"] = assignment.get("target_id")
        if assignment.get("target_mode") == "LINK":
            target = assignment.get("target_id")
            if not target: raise ValueError("기존 대상 연결에는 대상 식별자가 필요합니다.")
            table = {"PROJECT": "projects", "REQUEST": "analysis_requests", "SIMULATION_CASE": "dashboard_cases"}.get(role)
            if table and not conn.execute(f"SELECT 1 FROM {table} WHERE id=?", [target]).fetchone():
                raise ValueError("연결할 기존 대상을 찾을 수 없습니다.")
    by_path = {node["relative_path"]: node for node in nodes}
    final_paths = {node["relative_path"] for node in nodes if node.get("role_kind") == "FINAL"}
    for node in nodes:
        ancestor = by_path.get(node.get("parent_path"))
        under_final = False
        while ancestor:
            if ancestor["relative_path"] in final_paths:
                under_final = True
                break
            ancestor = by_path.get(ancestor.get("parent_path"))
        if (under_final or node.get("_final_relative_path")) and node.get("role_kind") != "EXCLUDE":
            node["role_kind"], node["status"] = "CONTAINER", "CONFIRMED"
            node["role_source"], node["role_basis"] = "PROFILE", "FINAL_ARCHIVE"
    excluded = [node["relative_path"] for node in nodes if node["status"] == "EXCLUDED"]
    for node in nodes:
        if any(node["relative_path"].startswith(path.rstrip("/") + "/") for path in excluded):
            node["status"] = "EXCLUDED"
    _recompute_context(nodes, saved["root_key"], saved["environment"], saved.get("project_id"), saved.get("request_id"))
    by_path = {node["relative_path"]: node for node in nodes}
    required_parent = {"REQUEST": "PROJECT", "WORKING": "REQUEST", "FINAL": "REQUEST",
                       "SIMULATION_CASE": "REQUEST", "EVALUATION": "SIMULATION_CASE",
                       "LOAD_CASE": "SIMULATION_CASE", "EXECUTION_RUN": "LOAD_CASE", "RUN_OPTION": "EXECUTION_RUN"}
    for node in nodes:
        if node.get("status") == "EXCLUDED":
            continue
        required = required_parent.get(node.get("role_kind"))
        if node.get("role_kind") == "SCENE":
            ancestor = by_path.get(node.get("parent_path"))
            while ancestor and ancestor.get("role_kind") in {None, "CONTAINER"}:
                ancestor = by_path.get(ancestor.get("parent_path"))
            if not ancestor or ancestor.get("role_kind") not in {"EXECUTION_RUN", "RUN_OPTION"}:
                node["status"] = "UNRESOLVED"
                node["message"] = "상위 EXECUTION_RUN 또는 RUN_OPTION 역할이 필요합니다."
            continue
        if not required:
            continue
        ancestor = by_path.get(node.get("parent_path"))
        while ancestor and ancestor.get("role_kind") in {None, "CONTAINER"}:
            ancestor = by_path.get(ancestor.get("parent_path"))
        # A Case-root scan can deliberately attach to an already selected
        # request/project without repeating their folders in the subtree.
        seeded_parent = node.get("role_kind") == "SIMULATION_CASE" and bool(saved.get("request_id"))
        working_case = False
        if node.get("role_kind") == "SIMULATION_CASE" and ancestor and ancestor.get("role_kind") == "WORKING":
            working_parent = by_path.get(ancestor.get("parent_path"))
            while working_parent and working_parent.get("role_kind") in {None, "CONTAINER"}:
                working_parent = by_path.get(working_parent.get("parent_path"))
            working_case = bool(working_parent and working_parent.get("role_kind") == "REQUEST")
        if (not ancestor or ancestor.get("role_kind") != required) and not seeded_parent and not working_case:
            node["status"] = "UNRESOLVED"
            node["message"] = f"상위 {required} 역할이 필요합니다."
    for node in nodes:
        if node.get("role_kind") == "REQUEST" and node.get("target_id"):
            owner = conn.execute("SELECT project_id FROM analysis_requests WHERE id=?", [node["target_id"]]).fetchone()
            if owner and node.get("project_id") and str(owner[0]) != str(node["project_id"]):
                raise ValueError("연결한 의뢰가 상위 프로젝트에 속하지 않습니다.")
    unresolved = [n for n in nodes if n["status"] == "UNRESOLVED"]
    plan = [n for n in nodes if n["status"] != "EXCLUDED" and n["role_kind"] in {"PROJECT", "REQUEST", "SIMULATION_CASE", "EVALUATION", "LOAD_CASE", "EXECUTION_RUN", "RUN_OPTION", "SCENE"}]
    existing = 0
    for node in plan:
        if node["role_kind"] in {"EVALUATION", "SCENE"}:
            continue
        target = node.get("target_id")
        table = {"PROJECT": "projects", "REQUEST": "analysis_requests"}.get(node["role_kind"])
        if table and target and conn.execute(f"SELECT 1 FROM {table} WHERE id=?", [target]).fetchone():
            existing += 1
        elif target and conn.execute("SELECT 1 FROM folder_environment_registry WHERE root_key=? AND target_id=?", [saved["root_key"], target]).fetchone():
            existing += 1
    work_rows = [n for n in plan if n["role_kind"] not in {"EVALUATION", "SCENE"}]
    case_count = sum(n["role_kind"] == "SIMULATION_CASE" for n in plan)
    has_request = any(n["role_kind"] == "REQUEST" for n in plan)
    can_apply = (not unresolved and saved["status"] == "COMPLETE"
                 and (case_count > 0 or (allow_without_cases and has_request)))
    if case_count == 0 and not (allow_without_cases and has_request):
        unresolved.append({"message": "등록할 Simulation Case가 없습니다."})
    preview_id = ident("environment-preview")
    review_required = bool(require_usage_review and saved["environment"] == "USAGE")
    # Keep the confirmed disposition of every scanned folder alongside the
    # semantic plan. The plan intentionally contains only role-bearing rows,
    # but downstream path creation also needs to distinguish an allowed
    # structural container from one the reviewer explicitly excluded.
    stored = {
        "rows": plan,
        "node_states": [{key: node.get(key) for key in ("relative_path", "parent_path", "name", "role_kind", "status", "role_source", "role_basis")}
                        for node in nodes],
        "usage_reviews": {},
        "usage_review_snapshots": {},
        "require_usage_review": review_required,
    }
    conn.execute("INSERT INTO folder_environment_previews(id,scan_id,rows_json,can_apply,created_by,created_at) VALUES(?,?,?,?,?,?)", [preview_id, scan_id, json.dumps(stored, ensure_ascii=False), can_apply, actor, now()])
    return {"id": preview_id, "scan_id": scan_id, "environment": saved["environment"], "can_apply": can_apply, "require_usage_review": review_required, "rows": plan, "unresolved_count": len(unresolved), "message": "등록할 Simulation Case가 없습니다." if case_count == 0 else None, "summary": {"new": len(work_rows) - existing, "existing": existing, "evaluations": sum(n["role_kind"] == "EVALUATION" for n in plan), "scenes": sum(n["role_kind"] == "SCENE" for n in plan)}}


def _recompute_context(nodes, root_key, environment, seeded_project, seeded_request):
    """Apply confirmed parent assignments to every descendant before saving."""
    contexts = {}
    for node in nodes:
        parent = contexts.get(node.get("parent_path"), {})
        role = node.get("role_kind")
        node["parent_context"] = parent.get("target_id")
        node["project_id"] = seeded_project or parent.get("project_id")
        node["request_id"] = seeded_request or parent.get("request_id")
        if role in {"PROJECT", "REQUEST", "SIMULATION_CASE", "LOAD_CASE", "EXECUTION_RUN"} and not node.get("target_id"):
            node["target_id"] = (dashboard_capture._case_id("dashboard-root-" + root_key, node["relative_path"])
                                 if role == "SIMULATION_CASE" else stable("environment-" + role.casefold(), root_key, node["relative_path"], role))
        if role == "RUN_OPTION":
            node.setdefault("run_option_id", stable("environment-option", root_key, node["relative_path"], "RUN_OPTION"))
            node.setdefault("target_id", node["run_option_id"])
            node["option_label"] = node["name"]
            node["option_status"] = "PRESENT"
        if role == "PROJECT": node["project_id"] = node.get("target_id")
        if role == "REQUEST": node["request_id"] = node.get("target_id")
        if parent.get("role_kind") == "SIMULATION_CASE": node["simulation_case_id"] = parent.get("target_id")
        next_context = dict(parent)
        if role in {"PROJECT", "REQUEST", "WORKING", "FINAL", "SIMULATION_CASE", "EVALUATION", "LOAD_CASE", "EXECUTION_RUN"}:
            next_context.update(node)
        if role == "PROJECT": next_context.pop("request_id", None)
        contexts[node["relative_path"]] = next_context


def _validated_usage_review(root, relative_path, contract):
    if not isinstance(contract, dict):
        legacy.fail("USAGE_SOURCE_REVIEW_REQUIRED", "파일·값 검수를 완료하세요.")
    chosen = usage_source_review.selection(contract.get("selection"))
    files = dashboard_capture._walk(root, relative_path, include_path=lambda path: usage_source_review.include_path(path, chosen))
    checked = usage_source_review.review([(path, data) for path, data, _ in files], selected=chosen,
        selected_sources=contract.get("selected_sources"), metric_paths=contract.get("metric_paths"), excludes=contract.get("excludes"),
        profile_id=contract.get("profile_id"), profile_revision=contract.get("profile_revision"))
    expected = sorted(contract.get("sources") or [], key=lambda item: str(item.get("source", "")).casefold())
    if expected != checked["contract"]["sources"]:
        legacy.fail("USAGE_SOURCE_REVIEW_STALE", "검수 후 선택 원본이 변경되었습니다. 다시 검수하세요.")
    partial = bool(contract.get("acknowledge_partial"))
    if checked["blocking_count"] or ((checked["missing_count"] or contract.get("excludes")) and not partial):
        legacy.fail("USAGE_SOURCE_REVIEW_REQUIRED", "파일·값 검수의 오류 또는 부분 게시 확인을 완료하세요.")
    return contract


def _registration_location_projection(conn, root, project_id: str, request_id: str,
                                      environment: str, scan_id: str,
                                      saved_preview: dict):
    """Resolve this registration's roles over the last active Refresh snapshot."""
    from . import folder_schema_resolver as resolver

    root_key = root_identity(root)
    registered_schema = resolver.resolve_request_schema(
        conn, root, root_key, project_id, request_id, environment,
        registered_scan_id=scan_id,
    )
    active = resolver.active_refresh_snapshot(conn, root_key, project_id, request_id, environment)
    if (active and str(active["profile_id"]) == str(registered_schema["profile"]["id"])
            and int(active["profile_revision"]) == int(registered_schema["profile"]["revision"])):
        active_schema = resolver.resolve_request_schema(conn, root, root_key, project_id, request_id, environment)
        applied = conn.execute(
            "SELECT s.relative_path,p.rows_json FROM folder_environment_scans s "
            "JOIN folder_environment_previews p ON p.scan_id=s.id "
            "JOIN folder_environment_registrations r ON r.preview_id=p.id "
            "WHERE s.root_key=? AND s.environment=? AND s.profile_id=? AND s.profile_revision=? "
            "AND s.status='COMPLETE' AND r.project_id=? AND r.request_id=? AND r.environment=? "
            "AND r.status IN ('REGISTERED','CAPTURING','COMPLETED','FAILED') AND r.created_at>? "
            "ORDER BY r.created_at,r.id",
            [root_key, environment, active["profile_id"], active["profile_revision"],
             project_id, request_id, environment, active["created_at"]],
        ).fetchall()
        later_registration_roles: dict[str, dict[str, Any]] = {}
        for scan_path, preview_value in applied:
            additions = _explicit_registration_roles(
                preview_value, str(active_schema["request_relative_path"]), environment,
                str(scan_path or ""),
            )
            for key, role in additions.items():
                later_registration_roles[key] = role

        active_by_path = {resolver._fold(str(item.get("relative_path") or "")): item
                          for item in active_schema.get("nodes", [])}
        role_fields = ("role_kind", "status", "confirmed", "target_id", "name", "option_status",
                       "role_source", "role_basis", "role_evidence_source", "parent_context",
                       "project_id", "request_id", "simulation_case_id", "run_option_id", "option_label")
        for node in registered_schema.get("nodes", []):
            key = resolver._fold(str(node.get("relative_path") or ""))
            registration_role = later_registration_roles.get(key)
            if registration_role:
                role_kind = str(registration_role.get("role_kind") or "")
                status = str(registration_role.get("status") or "")
                if role_kind in {"EXCLUDE", "EXCLUDED"} or status == "EXCLUDED":
                    node.update(role_kind=None, status="EXCLUDED", confirmed=True,
                                role_source="MANUAL", role_basis="EXCLUDED",
                                role_evidence_source="REGISTRATION")
                else:
                    node.update(role_kind=None if role_kind == "CONTAINER" else role_kind,
                                status=status, confirmed=status in {"CONFIRMED", "CONTAINER"},
                                role_source="MANUAL", role_basis="MANUAL",
                                role_evidence_source="REGISTRATION",
                                target_id=registration_role.get("target_id") or node.get("target_id"),
                                name=registration_role.get("name") or node.get("name"),
                                option_status=registration_role.get("option_status") or node.get("option_status"))
                continue
            previous = active_by_path.get(key)
            if previous is None:
                continue
            for field in role_fields:
                if field in previous:
                    node[field] = previous[field]
        _recompute_context(
            registered_schema.get("nodes", []), root_key, environment, project_id, request_id,
        )
        resolver._add_hierarchy(registered_schema.get("nodes", []))
    return resolver.resolve_request_locations(
        conn, project_id, request_id, environment, schema=registered_schema,
    )


def register(conn, preview_id, idempotency_key, capture, principal, root, *, creator_membership=True):
    actor = principal.user_id
    existing = conn.execute("SELECT id,preview_id FROM folder_environment_registrations WHERE idempotency_key=?", [idempotency_key]).fetchone()
    if existing:
        if str(existing[1]) != preview_id:
            legacy.fail("ENVIRONMENT_IDEMPOTENCY_CONFLICT", "같은 멱등 키가 다른 미리보기에 사용되었습니다.")
        return registration(conn, str(existing[0]))
    preview_row = conn.execute("SELECT scan_id,rows_json,can_apply FROM folder_environment_previews WHERE id=?", [preview_id]).fetchone()
    if not preview_row: legacy.fail("ENVIRONMENT_PREVIEW_NOT_FOUND", "환경 미리보기를 찾을 수 없습니다.", 404)
    if not preview_row[2]: legacy.fail("ENVIRONMENT_PREVIEW_CONFLICT", "확인되지 않은 폴더가 있는 미리보기는 등록할 수 없습니다.")
    scan_row = conn.execute("SELECT root_key,relative_path,environment,project_id,request_id,tree_json,profile_id,profile_revision FROM folder_environment_scans WHERE id=?", [preview_row[0]]).fetchone()
    profile_now = conn.execute("SELECT revision FROM folder_environment_profiles WHERE id=?", [scan_row[6]]).fetchone()
    if not profile_now or int(profile_now[0]) != int(scan_row[7]):
        legacy.fail("ENVIRONMENT_PROFILE_STALE", "저장 규칙이 변경되었습니다. 다시 조사하세요.")
    request_path = None
    if scan_row[3] and scan_row[4]:
        try:
            from . import folder_schema_resolver as resolver
            request_path = resolver._request_path(conn, str(scan_row[0]), str(scan_row[3]), str(scan_row[4]), str(scan_row[2]))
        except (ValueError, KeyError):
            request_path = None
    saved_tree = decoded(scan_row[5])
    # Re-scan with the same boundaries the saved scan used (Final archives and
    # any explicitly skipped sibling folders keep their descendants unread).
    boundaries = [item["relative_path"] for item in saved_tree if item.get("children_skipped")]
    fresh = scan(root, str(scan_row[1]),
                 skip_descendants=_skip_with_boundaries(_skip_final_archive(request_path), boundaries))
    saved_paths = [item["relative_path"] for item in saved_tree]
    if fresh["status"] != "COMPLETE" or [item["relative_path"] for item in fresh["nodes"]] != saved_paths or root_identity(root) != scan_row[0]:
        legacy.fail("ENVIRONMENT_SCAN_STALE", "조사 이후 폴더 구조 또는 저장소가 변경되었습니다. 다시 조사하세요.")
    registration_id = ident("environment-registration")
    preview_saved = preview_data(preview_row[1]); plan_rows = preview_saved["rows"]
    if preview_saved.get("require_usage_review"):
        for entry in (item for item in plan_rows if item["role_kind"] == "SIMULATION_CASE"):
            _validated_usage_review(root, entry["relative_path"], preview_saved.get("usage_reviews", {}).get(entry["relative_path"]))
    # Registration is durable before any file read.  A capture is an
    # independently retryable side effect and must never roll this phase back.
    conn.execute("BEGIN TRANSACTION")
    try:
        # Persist actual Project/Request identities before dashboard Cases.
        for item in plan_rows:
            if item["role_kind"] in {"PROJECT", "REQUEST"} and not ((scan_row[3] and item["role_kind"] == "PROJECT") or (scan_row[4] and item["role_kind"] == "REQUEST")):
                table = "projects" if item["role_kind"] == "PROJECT" else "analysis_requests"
                if conn.execute(f"SELECT 1 FROM {table} WHERE id=?", [item["target_id"]]).fetchone():
                    continue
                material = {"role_kind": item["role_kind"], "target_id": item["target_id"], "name": item["name"], "code": "", "parent_target_id": item.get("parent_context"), "analysis_type": ""}
                legacy.materialize(conn, material, principal, creator_membership=creator_membership)
        project_id = scan_row[3] or next((r.get("target_id") for r in plan_rows if r["role_kind"] == "PROJECT"), None)
        request_id = scan_row[4] or next((r.get("target_id") for r in plan_rows if r["role_kind"] == "REQUEST"), None)
        if not project_id or not request_id: legacy.fail("ENVIRONMENT_CONTEXT_REQUIRED", "프로젝트와 의뢰 연결을 확인하세요.")
        conn.execute("INSERT INTO folder_environment_registrations(id,preview_id,idempotency_key,environment,project_id,request_id,status,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)", [registration_id, preview_id, idempotency_key, scan_row[2], project_id, request_id, "REGISTERED", actor, now()])
        for row in plan_rows:
            if row["role_kind"] in {"EVALUATION", "SCENE"}:
                continue
            target = row.get("target_id") or stable("environment-target", scan_row[0], row["relative_path"], row["role_kind"])
            conn.execute("INSERT INTO folder_environment_registry(id,registration_id,root_key,relative_path,role_kind,parent_context_id,target_id,raw_name,option_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", [ident("environment-registry"), registration_id, scan_row[0], row["relative_path"], row["role_kind"], row.get("parent_context"), target, row["name"], row.get("option_status"), now()])
        if capture:
            for entry in [r for r in plan_rows if r["role_kind"] == "SIMULATION_CASE"]:
                case_id = dashboard_capture._case_id(dashboard_capture._root_id(root), entry["relative_path"])
                conn.execute("INSERT INTO folder_environment_capture_jobs(id,registration_id,case_id,load_case_id,run_case_id,run_option_id,option_label,option_status,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", [ident("environment-capture-job"), registration_id, case_id, None, None, None, None, None, "PENDING", now(), now()])
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    if capture:
        try:
            location_projection = _registration_location_projection(
                conn, root, str(project_id), str(request_id), str(scan_row[2]),
                str(preview_row[0]), preview_saved,
            )
        except Exception as exc:
            error_code = _capture_context_error_code(exc)
            _logger.warning(
                "Environment capture projection failed registration_id=%s error_type=%s error_code=%s",
                registration_id, type(exc).__name__, error_code,
            )
            conn.execute(
                "UPDATE folder_environment_capture_jobs SET status='FAILED',error_code=?,updated_at=? "
                "WHERE registration_id=? AND status IN ('PENDING','RUNNING')",
                [error_code, now(), registration_id],
            )
            return registration(conn, registration_id)
        for entry in [r for r in plan_rows if r["role_kind"] == "SIMULATION_CASE"]:
            case_id = dashboard_capture._case_id(dashboard_capture._root_id(root), entry["relative_path"])
            job_id = conn.execute("SELECT id FROM folder_environment_capture_jobs WHERE registration_id=? AND case_id=?", [registration_id, case_id]).fetchone()[0]
            conn.execute("BEGIN TRANSACTION")
            try:
                conn.execute("UPDATE folder_environment_capture_jobs SET status='RUNNING',updated_at=? WHERE id=?", [now(), job_id])
                capture_payload = _case_capture_payload(
                    root, location_projection.schema, location_projection, entry["relative_path"],
                    preview_saved.get("usage_reviews", {}).get(entry["relative_path"]),
                )
                outcome = dashboard_capture.create_capture(conn, capture_payload, actor=actor)
                conn.execute("UPDATE folder_environment_capture_jobs SET status='COMPLETED',capture_id=?,updated_at=? WHERE id=?", [outcome["id"], now(), job_id])
                conn.execute("COMMIT")
            except dashboard_capture.DashboardCaptureError as exc:
                conn.execute("ROLLBACK")
                conn.execute("BEGIN TRANSACTION")
                conn.execute("UPDATE folder_environment_capture_jobs SET status='FAILED',error_code=?,updated_at=? WHERE id=?", [exc.code, now(), job_id])
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
    return registration(conn, registration_id)


def preview_context(conn, preview_id):
    row = conn.execute("SELECT s.project_id,s.request_id FROM folder_environment_previews p JOIN folder_environment_scans s ON s.id=p.scan_id WHERE p.id=?", [preview_id]).fetchone()
    if not row: legacy.fail("ENVIRONMENT_PREVIEW_NOT_FOUND", "환경 미리보기를 찾을 수 없습니다.", 404)
    return {"project_id": row[0], "request_id": row[1]}


def registration_context(conn, registration_id):
    row = conn.execute("SELECT project_id,request_id FROM folder_environment_registrations WHERE id=?", [registration_id]).fetchone()
    if not row: legacy.fail("ENVIRONMENT_REGISTRATION_NOT_FOUND", "환경 등록을 찾을 수 없습니다.", 404)
    return {"project_id": row[0], "request_id": row[1]}


def _capture_context_error_code(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    return str(code) if isinstance(code, str) and code else "CAPTURE_CONTEXT_UNAVAILABLE"


def registration(conn, registration_id):
    row = conn.execute("""SELECT r.id,r.preview_id,r.environment,r.project_id,r.request_id,r.status,r.created_at,s.relative_path,p.rows_json
        FROM folder_environment_registrations r JOIN folder_environment_previews p ON p.id=r.preview_id
        JOIN folder_environment_scans s ON s.id=p.scan_id WHERE r.id=?""", [registration_id]).fetchone()
    if not row: legacy.fail("ENVIRONMENT_REGISTRATION_NOT_FOUND", "환경 등록을 찾을 수 없습니다.", 404)
    jobs = rows(conn.execute("""SELECT j.id,j.status,j.case_id,j.load_case_id,j.run_case_id,j.run_option_id,j.option_label,j.option_status,j.capture_id,j.error_code,
        COALESCE(dc.project_id,r.project_id) AS project_id,COALESCE(dc.request_id,r.request_id) AS request_id
        FROM folder_environment_capture_jobs j JOIN folder_environment_registrations r ON r.id=j.registration_id
        LEFT JOIN dashboard_cases dc ON dc.id=j.case_id WHERE j.registration_id=? ORDER BY j.created_at""", [registration_id]))
    states = {str(job["status"]) for job in jobs}
    status = "COMPLETED" if states and states == {"COMPLETED"} else ("FAILED" if "FAILED" in states else ("CAPTURING" if states & {"PENDING", "RUNNING"} else row[5]))
    if status != row[5]:
        conn.execute("UPDATE folder_environment_registrations SET status=? WHERE id=?", [status, registration_id])
    saved = preview_data(row[8])
    return {"registration_id": row[0], "preview_id": row[1], "environment": row[2], "project_id": row[3], "request_id": row[4], "status": status, "created_at": row[6], "relative_path": row[7], "capture_jobs": [dict(j) for j in jobs], "usage_source_reviews": saved.get("usage_review_snapshots", {})}


def retry(conn, registration_id, job_ids, principal=None, root=None):
    context = registration(conn, registration_id)
    query = "UPDATE folder_environment_capture_jobs SET status='PENDING',error_code=NULL,updated_at=? WHERE registration_id=? AND status IN ('FAILED','PENDING')"
    args = [now(), registration_id]
    if job_ids:
        marks = ",".join("?" for _ in job_ids); query += f" AND id IN ({marks})"; args.extend(job_ids)
    conn.execute(query, args)
    if principal is not None and root is not None:
        jobs = rows(conn.execute("SELECT id,case_id FROM folder_environment_capture_jobs WHERE registration_id=? AND status='PENDING'", [registration_id]))
        replay = conn.execute("""SELECT p.rows_json,s.environment,s.profile_id,s.profile_revision,s.root_key,s.id
            FROM folder_environment_registrations r JOIN folder_environment_previews p ON p.id=r.preview_id
            JOIN folder_environment_scans s ON s.id=p.scan_id WHERE r.id=?""", [registration_id]).fetchone()
        if not replay:
            legacy.fail("ENVIRONMENT_REGISTRATION_NOT_FOUND", "환경 등록을 찾을 수 없습니다.", 404)
        saved_preview = preview_data(replay[0]); plan_rows, environment, profile_id, profile_revision = saved_preview["rows"], replay[1], replay[2], replay[3]
        if root_identity(root) != replay[4]:
            legacy.fail("ENVIRONMENT_ROOT_CHANGED", "저장소가 변경되었습니다. 다시 조사하세요.")
        if job_ids: jobs = [job for job in jobs if job["id"] in set(job_ids)]
        current_profile = conn.execute("SELECT revision FROM folder_environment_profiles WHERE id=?", [profile_id]).fetchone()
        if not current_profile or int(current_profile[0]) != int(profile_revision):
            if jobs:
                marks = ",".join("?" for _ in jobs)
                conn.execute(f"UPDATE folder_environment_capture_jobs SET status='FAILED',error_code='ENVIRONMENT_PROFILE_STALE',updated_at=? WHERE id IN ({marks})", [now(), *[job["id"] for job in jobs]])
            return registration(conn, registration_id)
        try:
            location_projection = _registration_location_projection(
                conn, root, str(context["project_id"]), str(context["request_id"]),
                str(environment), str(replay[5]), saved_preview,
            ) if jobs else None
        except Exception as exc:
            error_code = _capture_context_error_code(exc)
            _logger.warning(
                "Environment capture retry projection failed registration_id=%s error_type=%s error_code=%s",
                registration_id, type(exc).__name__, error_code,
            )
            if jobs:
                marks = ",".join("?" for _ in jobs)
                conn.execute(
                    f"UPDATE folder_environment_capture_jobs SET status='FAILED',error_code=?,updated_at=? "
                    f"WHERE id IN ({marks}) AND status='PENDING'",
                    [error_code, now(), *[job["id"] for job in jobs]],
                )
            return registration(conn, registration_id)
        for job in jobs:
            case = conn.execute("SELECT project_id,request_id,relative_path,environment,storage_root_id FROM dashboard_cases WHERE id=?", [job["case_id"]]).fetchone()
            if not case:
                registration_row = conn.execute("SELECT project_id,request_id,environment FROM folder_environment_registrations WHERE id=?", [registration_id]).fetchone()
                entry = next((row for row in plan_rows if row.get("role_kind") == "SIMULATION_CASE" and dashboard_capture._case_id(dashboard_capture._root_id(root), row["relative_path"]) == job["case_id"]), None)
                if not registration_row or not entry:
                    conn.execute("UPDATE folder_environment_capture_jobs SET status='FAILED',error_code='CAPTURE_CONTEXT_MISSING',updated_at=? WHERE id=?", [now(), job["id"]]); continue
                case = (entry.get("project_id") or registration_row[0], entry.get("request_id") or registration_row[1], entry["relative_path"], registration_row[2], dashboard_capture._root_id(root))
            else:
                entry = next((row for row in plan_rows if row.get("role_kind") == "SIMULATION_CASE" and row["relative_path"] == case[2]), None)
            if not entry:
                conn.execute("UPDATE folder_environment_capture_jobs SET status='FAILED',error_code='CAPTURE_CONTEXT_MISSING',updated_at=? WHERE id=?", [now(), job["id"]]); continue
            conn.execute("BEGIN TRANSACTION")
            try:
                conn.execute("UPDATE folder_environment_capture_jobs SET status='RUNNING',updated_at=? WHERE id=?", [now(), job["id"]])
                payload = _case_capture_payload(
                    root, location_projection.schema, location_projection,
                    entry["relative_path"],
                    saved_preview.get("usage_reviews", {}).get(entry["relative_path"]),
                )
                result = dashboard_capture.create_capture(conn, payload, actor=principal.user_id)
                conn.execute("UPDATE folder_environment_capture_jobs SET status='COMPLETED',capture_id=?,updated_at=? WHERE id=?", [result["id"], now(), job["id"]])
                conn.execute("COMMIT")
            except dashboard_capture.DashboardCaptureError as exc:
                conn.execute("ROLLBACK")
                conn.execute("BEGIN TRANSACTION")
                conn.execute("UPDATE folder_environment_capture_jobs SET status='FAILED',error_code=?,updated_at=? WHERE id=?", [exc.code, now(), job["id"]])
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                conn.execute("BEGIN TRANSACTION")
                conn.execute("UPDATE folder_environment_capture_jobs SET status='FAILED',error_code=?,updated_at=? WHERE id=?", ["CAPTURE_UNEXPECTED_ERROR", now(), job["id"]])
                conn.execute("COMMIT")
    return registration(conn, registration_id)


def usage_review(conn, preview_id, case_relative_path, selection, selected_sources, metric_paths, excludes, acknowledge_partial, root):
    """Preflight one Usage Case and persist its selected immutable contract."""
    row = conn.execute("""SELECT p.rows_json,s.environment,s.profile_id,s.profile_revision,fp.rules_json
        FROM folder_environment_previews p JOIN folder_environment_scans s ON s.id=p.scan_id
        JOIN folder_environment_profiles fp ON fp.id=s.profile_id WHERE p.id=?""", [preview_id]).fetchone()
    if not row: legacy.fail("ENVIRONMENT_PREVIEW_NOT_FOUND", "환경 미리보기를 찾을 수 없습니다.", 404)
    if row[1] != "USAGE": raise ValueError("사용환경 미리보기에서만 파일·값 검수를 할 수 있습니다.")
    if conn.execute("SELECT 1 FROM folder_environment_registrations WHERE preview_id=?", [preview_id]).fetchone():
        legacy.fail("USAGE_SOURCE_REVIEW_FROZEN", "등록된 미리보기의 검수 계약은 바꿀 수 없습니다. 새 미리보기를 만드세요.")
    saved = preview_data(row[0])
    case = next((item for item in saved["rows"] if item.get("role_kind") == "SIMULATION_CASE" and item.get("relative_path") == case_relative_path), None)
    if not case or case.get("status") == "EXCLUDED": raise ValueError("미리보기의 확인된 Simulation Case를 선택하세요.")
    defaults = decoded(row[4]).get("usage_sources", {})
    chosen = usage_source_review.selection(selection or defaults.get("selection"))
    metric_paths = metric_paths or defaults.get("metric_paths", {})
    excluded_files: list[dict[str, str]] = []
    files = dashboard_capture._walk(root, case_relative_path, include_path=lambda path: usage_source_review.include_path(path, chosen), excluded_files=excluded_files)
    result = usage_source_review.review([(path, data) for path, data, _ in files], selected=chosen, selected_sources=selected_sources, metric_paths=metric_paths, excludes=excludes, profile_id=str(row[2]), profile_revision=int(row[3]))
    result["excluded_count"] = len(excluded_files)
    result["excluded_files"] = excluded_files
    if (result["missing_count"] or excludes) and not acknowledge_partial:
        result["can_publish"] = False
    result["contract"]["acknowledge_partial"] = bool(acknowledge_partial)
    saved.setdefault("usage_reviews", {})[case_relative_path] = result["contract"]
    saved.setdefault("usage_review_snapshots", {})[case_relative_path] = {key: result[key] for key in ("entries", "excluded_count", "excluded_files", "media_paths", "blocking_count", "missing_count", "can_publish")}
    conn.execute("UPDATE folder_environment_previews SET rows_json=? WHERE id=?", [json.dumps(saved, ensure_ascii=False), preview_id])
    return {**result, "case_relative_path": case_relative_path, "simulation_case_id": case.get("target_id")}
