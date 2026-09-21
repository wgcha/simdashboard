"""Environment-aware, parent-first discovery plans.

This module deliberately does not reinterpret a saved plan during capture.  It
keeps the original names and a stable context id for every optional Run Option.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid4, uuid5

from ..database_connection import rows
from . import dashboard_capture
from . import folder_discovery as legacy
from .folder_discovery_scan import root_identity, scan
from .environment_folder_profiles import resolve_role
from . import usage_source_review

ENVIRONMENTS = ("USAGE", "DISTRIBUTION")
ROLES = {
    "USAGE": ("PROJECT", "REQUEST", "SIMULATION_CASE", "EVALUATION", "RESULTS", "INPUT", "CONTAINER"),
    "DISTRIBUTION": ("PROJECT", "REQUEST", "SIMULATION_CASE", "LOAD_CASE", "EXECUTION_RUN", "RUN_OPTION", "SCENE", "RESULTS", "INPUT", "CONTAINER"),
}
_EVALUATIONS = {"settle", "wobble", "horizontal_force_angle", "slope_angle", "slope_angle_360"}
_SCENE = re.compile(r"(scene|result|contour|animation)", re.I)


def now(): return datetime.now(timezone.utc).replace(tzinfo=None)
def ident(prefix): return f"{prefix}-{uuid4().hex}"
def decoded(value): return json.loads(value) if isinstance(value, str) else value
def stable(prefix, root_key, path, role): return f"{prefix}-{uuid5(NAMESPACE_URL, root_key + ':' + path.casefold() + ':' + role).hex}"
def preview_data(value):
    value = decoded(value)
    return value if isinstance(value, dict) else {"rows": value, "usage_reviews": {}}


def profiles(conn):
    records = rows(conn.execute("SELECT id,environment,name,revision,rules_json,created_at,updated_at FROM folder_environment_profiles ORDER BY environment,name"))
    items = []
    for record in records:
        item = dict(record)
        item["rules"] = decoded(item.pop("rules_json"))
        items.append(item)
    return {"items": items}


def default_profile(conn, environment: str):
    row = conn.execute("SELECT id,revision,rules_json FROM folder_environment_profiles WHERE environment=? ORDER BY created_at LIMIT 1", [environment]).fetchone()
    if not row: raise ValueError("환경 기본 규칙이 없습니다. 스키마 migration을 적용하세요.")
    return {"id": str(row[0]), "revision": int(row[1]), "rules": decoded(row[2])}


def save_scan(conn, root, relative_path: str, environment: str, profile_id: str | None, project_id: str | None, request_id: str | None, actor: str):
    if environment not in ENVIRONMENTS: raise ValueError("지원하지 않는 환경입니다.")
    if request_id and not project_id:
        found = conn.execute("SELECT project_id FROM analysis_requests WHERE id=?", [request_id]).fetchone()
        if not found: raise ValueError("의뢰를 찾을 수 없습니다.")
        project_id = str(found[0])
    if request_id:
        found = conn.execute("SELECT 1 FROM analysis_requests WHERE id=? AND project_id=?", [request_id, project_id]).fetchone()
        if not found: raise ValueError("프로젝트와 의뢰의 연결이 일치하지 않습니다.")
    profile = default_profile(conn, environment) if not profile_id else _profile(conn, profile_id, environment)
    result = scan(root, relative_path)
    root_key = root_identity(root)
    nodes = _interpret(result["nodes"], root_key, environment, project_id, request_id, profile["rules"])
    scan_id = ident("environment-scan")
    conn.execute("INSERT INTO folder_environment_scans(id,root_key,relative_path,environment,profile_id,profile_revision,project_id,request_id,status,tree_json,issues_json,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 [scan_id, root_key, relative_path, environment, profile["id"], profile["revision"], project_id, request_id, result["status"], json.dumps(nodes, ensure_ascii=False), json.dumps(result["issues"], ensure_ascii=False), actor, now()])
    return {"id": scan_id, "environment": environment, "profile_id": profile["id"], "profile_revision": profile["revision"], "usage_sources": profile["rules"].get("usage_sources"), "relative_path": relative_path, "status": result["status"], "nodes": nodes, "issues": result["issues"]}


def _profile(conn, profile_id, environment):
    row = conn.execute("SELECT id,revision,rules_json,environment FROM folder_environment_profiles WHERE id=?", [profile_id]).fetchone()
    if not row or str(row[3]) != environment: raise ValueError("환경 규칙 프로파일을 찾을 수 없습니다.")
    return {"id": str(row[0]), "revision": int(row[1]), "rules": decoded(row[2])}


def _interpret(raw, root_key, environment, project_id, request_id, rules=None):
    context, out = {}, []
    for source in raw:
        node = dict(source); parent = context.get(node["parent_path"], {})
        name = node["name"]; folded = name.casefold()
        role, option_status = _role(environment, name, parent, node["depth"], rules)
        # A node id represents a filesystem node, not its current role.  This
        # keeps an explicit assignment valid when its suggested role changes.
        node_id = stable("environment-node", root_key, node["relative_path"], "NODE")
        status = "CONFIRMED" if role else ("UNRESOLVED" if option_status == "UNRESOLVED" else "CONTAINER")
        item = {**node, "id": node_id, "environment": environment, "role_kind": role, "allowed_roles": ROLES[environment], "status": status, "parent_context": parent.get("target_id"), "project_id": project_id or parent.get("project_id"), "request_id": request_id or parent.get("request_id"), "option_status": option_status, "option_label": name if role == "RUN_OPTION" else None}
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
            item["target_id"] = stable("environment-request", root_key, node["relative_path"], role)
            item["request_id"] = item["target_id"]
        if role == "SIMULATION_CASE":
            item["target_id"] = dashboard_capture._case_id("dashboard-root-" + root_key, node["relative_path"])
        elif role in {"EXECUTION_RUN", "LOAD_CASE"}:
            item["target_id"] = stable("environment-" + role.casefold(), root_key, node["relative_path"], role)
        if role == "RUN_OPTION": item["run_option_id"] = stable("environment-option", root_key, node["relative_path"], role)
        out.append(item)
        next_context = dict(parent)
        if role in {"PROJECT", "REQUEST", "SIMULATION_CASE", "LOAD_CASE", "EXECUTION_RUN"}: next_context.update(item)
        if role == "PROJECT": next_context.pop("request_id", None)
        context[node["relative_path"]] = next_context
    return out


def _role(environment, name, parent, depth=0, rules=None):
    lowered = name.casefold()
    if rules:
        resolved, matched, conflict = resolve_role(name, depth, parent.get("role_kind"), rules, environment)
        if conflict:
            return None, "UNRESOLVED"
        if matched:
            return (resolved, "PRESENT" if resolved == "RUN_OPTION" else None)
    # Containers intentionally pass their context through.  Only recognizable
    # semantic folders receive an automatic role; ambiguous folders stay for
    # explicit confirmation in the preview.
    if re.match(r"^(project|prj|p)[_-]", name, re.I): return "PROJECT", None
    match = re.match(r"^WR_[A-Za-z0-9._-]+_SimType([12])$", name, re.I)
    if match:
        expected = "1" if environment == "USAGE" else "2"
        return ("REQUEST", None) if match.group(1) == expected else (None, "UNRESOLVED")
    if re.match(r"^(assy_res|package)[_-]", name, re.I): return "SIMULATION_CASE", None
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


def preview(conn, scan_id, assignments, actor, require_usage_review=False):
    records = rows(conn.execute("SELECT id,root_key,environment,project_id,request_id,status,tree_json FROM folder_environment_scans WHERE id=?", [scan_id]))
    if not records: legacy.fail("ENVIRONMENT_SCAN_NOT_FOUND", "환경 조사 결과를 찾을 수 없습니다.", 404)
    saved = records[0]; nodes = decoded(saved["tree_json"]); by_id = {node["id"]: node for node in nodes}
    for assignment in assignments:
        node = by_id.get(assignment.get("node_id")); role = assignment.get("role_kind")
        if not node or role not in {*ROLES[saved["environment"]], "EXCLUDE"}: raise ValueError("조사 트리에 없는 역할 지정입니다.")
        node["role_kind"], node["status"] = role, "EXCLUDED" if role == "EXCLUDE" else ("CONFIRMED" if assignment.get("confirm", True) else "UNRESOLVED")
        if assignment.get("target_mode") == "LINK": node["target_id"] = assignment.get("target_id")
        if assignment.get("target_mode") == "LINK":
            target = assignment.get("target_id")
            if not target: raise ValueError("기존 대상 연결에는 대상 식별자가 필요합니다.")
            table = {"PROJECT": "projects", "REQUEST": "analysis_requests", "SIMULATION_CASE": "dashboard_cases"}.get(role)
            if table and not conn.execute(f"SELECT 1 FROM {table} WHERE id=?", [target]).fetchone():
                raise ValueError("연결할 기존 대상을 찾을 수 없습니다.")
    excluded = [node["relative_path"] for node in nodes if node["status"] == "EXCLUDED"]
    for node in nodes:
        if any(node["relative_path"].startswith(path.rstrip("/") + "/") for path in excluded):
            node["status"] = "EXCLUDED"
    _recompute_context(nodes, saved["root_key"], saved["environment"], saved.get("project_id"), saved.get("request_id"))
    by_path = {node["relative_path"]: node for node in nodes}
    required_parent = {"REQUEST": "PROJECT", "SIMULATION_CASE": "REQUEST", "EVALUATION": "SIMULATION_CASE",
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
            continue
        if not required:
            continue
        ancestor = by_path.get(node.get("parent_path"))
        while ancestor and ancestor.get("role_kind") in {None, "CONTAINER"}:
            ancestor = by_path.get(ancestor.get("parent_path"))
        # A Case-root scan can deliberately attach to an already selected
        # request/project without repeating their folders in the subtree.
        seeded_parent = node.get("role_kind") == "SIMULATION_CASE" and bool(saved.get("request_id"))
        if (not ancestor or ancestor.get("role_kind") != required) and not seeded_parent:
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
    can_apply = not unresolved and saved["status"] == "COMPLETE" and case_count > 0
    if case_count == 0:
        unresolved.append({"message": "등록할 Simulation Case가 없습니다."})
    preview_id = ident("environment-preview")
    review_required = bool(require_usage_review and saved["environment"] == "USAGE")
    stored = {"rows": plan, "usage_reviews": {}, "usage_review_snapshots": {}, "require_usage_review": review_required} if review_required else plan
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
        if role in {"PROJECT", "REQUEST", "SIMULATION_CASE", "LOAD_CASE", "EXECUTION_RUN"}:
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


def register(conn, preview_id, idempotency_key, capture, principal, root):
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
    fresh = scan(root, str(scan_row[1]))
    saved_paths = [item["relative_path"] for item in decoded(scan_row[5])]
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
                legacy.materialize(conn, material, principal)
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
        for entry in [r for r in plan_rows if r["role_kind"] == "SIMULATION_CASE"]:
            case_id = dashboard_capture._case_id(dashboard_capture._root_id(root), entry["relative_path"])
            job_id = conn.execute("SELECT id FROM folder_environment_capture_jobs WHERE registration_id=? AND case_id=?", [registration_id, case_id]).fetchone()[0]
            conn.execute("BEGIN TRANSACTION")
            try:
                conn.execute("UPDATE folder_environment_capture_jobs SET status='RUNNING',updated_at=? WHERE id=?", [now(), job_id])
                case_prefix = entry["relative_path"].rstrip("/") + "/"
                option_labels = [row["name"] for row in plan_rows if row["role_kind"] == "RUN_OPTION" and row.get("status") == "CONFIRMED" and row["relative_path"].startswith(case_prefix)]
                hierarchy = [{"relative_path": row["relative_path"], "role_kind": row["role_kind"], "target_id": row.get("target_id"), "parent_context_id": row.get("parent_context"), "raw_name": row["name"], "option_status": row.get("option_status")} for row in plan_rows if row["relative_path"].startswith(case_prefix)]
                outcome = dashboard_capture.create_capture(conn, {"project_id": entry.get("project_id") or project_id, "request_id": entry.get("request_id") or request_id, "root_relative_path": entry["relative_path"], "environment": scan_row[2], "storage_root_id": dashboard_capture._root_id(root), "simulation_case_id": case_id, "recipe_version": "dashboard-v1", "run_option_labels": option_labels, "hierarchy_assignments": hierarchy, "rule_profile_id": scan_row[6], "rule_profile_version": scan_row[7], "usage_source_review": preview_saved.get("usage_reviews", {}).get(entry["relative_path"])}, actor=actor)
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
        replay = conn.execute("""SELECT p.rows_json,s.environment,s.profile_id,s.profile_revision,s.root_key
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
                prefix = entry["relative_path"].rstrip("/") + "/"
                option_labels = [row["name"] for row in plan_rows if row.get("role_kind") == "RUN_OPTION" and row.get("status") == "CONFIRMED" and row["relative_path"].startswith(prefix)]
                hierarchy = [{"relative_path": row["relative_path"], "role_kind": row["role_kind"], "target_id": row.get("target_id"), "parent_context_id": row.get("parent_context"), "raw_name": row["name"], "option_status": row.get("option_status")} for row in plan_rows if row["relative_path"].startswith(prefix)]
                payload = {"project_id": case[0], "request_id": case[1], "root_relative_path": case[2], "environment": case[3], "storage_root_id": case[4], "simulation_case_id": job["case_id"], "recipe_version": "dashboard-v1", "run_option_labels": option_labels, "hierarchy_assignments": hierarchy, "rule_profile_id": profile_id, "rule_profile_version": profile_revision, "usage_source_review": saved_preview.get("usage_reviews", {}).get(entry["relative_path"])}
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
