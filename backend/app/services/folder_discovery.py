"""Explicit folder discovery proposals and atomic workload creation."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4

from fastapi import HTTPException

from ..database_connection import rows
from ..repositories import semantic_mapping as mapping_repository
from . import spdm_storage
from .folder_discovery_plan import build_plan, folded
from .folder_discovery_scan import browse, normal, root_identity, scan
from .semantic_mapping import semantic_transaction

WRITE_LOCK = RLock()
KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
ANALYSIS_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
ROLE_KINDS = ("PROJECT", "REQUEST", "LOAD_CASE", "RESULTS", "INPUT")
DEFAULT_CATALOG = {
    "roles": [
        {"key": "PROJECT", "label": "프로젝트", "kind": "PROJECT", "active": True},
        {"key": "REQUEST", "label": "의뢰", "kind": "REQUEST", "active": True},
        {"key": "LOAD_CASE", "label": "하중 경우", "kind": "LOAD_CASE", "active": True},
        {"key": "RESULTS", "label": "결과 폴더", "kind": "RESULTS", "active": True},
        {"key": "INPUT", "label": "입력 폴더", "kind": "INPUT", "active": True},
    ],
    "analysis_types": [
        {"key": key, "label": key, "active": True}
        for key in ("DROP", "SIDE_CLAMP", "SPDM_CMS", "SPDM_MODAL", "SPDM_DEFLECTION", "SPDM_STIFFNESS", "SPDM_VIBRATION")
    ],
}


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def ident(prefix: str):
    return f"{prefix}-{uuid4().hex}"


def decoded(value):
    return json.loads(value) if isinstance(value, str) else value


def fail(code: str, message: str, status: int = 409):
    raise HTTPException(status, {"code": code, "message": message})


def configured_root(conn):
    root = spdm_storage.storage_root(conn).root
    if root is None:
        fail("SPDM_ROOT_UNSET", "먼저 서버 저장소 경로를 설정하세요.")
    return root


def lock_tables(conn):
    mapping_repository.lock_binding_tables(conn)
    if getattr(conn, "backend", "duckdb") == "postgresql":
        conn.execute("LOCK TABLE folder_discovery_catalog, folder_discovery_registry, folder_discovery_rules, folder_discovery_previews, "
                     "spdm_storage_settings, projects, analysis_requests, load_cases IN SHARE ROW EXCLUSIVE MODE")


def save_scan(conn, root, relative: str, result: dict, actor: str) -> str:
    scan_id = ident("folder-scan")
    conn.execute("INSERT INTO folder_discovery_scans(id,root_key,root_path,relative_path,status,tree_json,issues_json,created_by,created_at) "
                 "VALUES(?,?,?,?,?,?,?,?,?)", [scan_id, root_identity(root), str(root), relative, result["status"],
                 json.dumps(result["nodes"], ensure_ascii=False), json.dumps(result["issues"], ensure_ascii=False), actor, now()])
    return scan_id


def load_scan(conn, scan_id: str):
    data = rows(conn.execute("SELECT * FROM folder_discovery_scans WHERE id=?", [scan_id]))
    if not data:
        fail("SCAN_NOT_FOUND", "조사 결과를 찾을 수 없습니다.", 404)
    row = dict(data[0])
    row["nodes"], row["issues"] = decoded(row.pop("tree_json")), decoded(row.pop("issues_json"))
    return row


def rules(conn, root_key: str, relative: str) -> dict:
    row = conn.execute("SELECT rules_json,revision FROM folder_discovery_rules WHERE root_key=? AND relative_path=?",
                       [root_key, relative]).fetchone()
    return {"rules": decoded(row[0]) if row else [], "revision": int(row[1]) if row else 0}


def catalog(conn) -> dict:
    row = conn.execute("SELECT revision,roles_json,analysis_types_json FROM folder_discovery_catalog WHERE id=1").fetchone()
    if not row:
        raise RuntimeError("Folder discovery catalog is missing; run the schema migration before starting the application.")
    return {"revision": int(row[0]), "roles": decoded(row[1]), "analysis_types": decoded(row[2])}


def _validate_catalog_entries(entries: list[dict], *, is_role: bool) -> dict[str, dict]:
    found = {}
    normalized = set()
    for entry in entries:
        key, label = entry["key"].strip(), entry["label"].strip()
        if not (KEY if is_role else ANALYSIS_KEY).fullmatch(key):
            raise ValueError("카탈로그 키는 영문자로 시작하고 영문자·숫자·밑줄만 사용할 수 있습니다.")
        if not label or any(ord(char) < 32 or ord(char) == 127 for char in label):
            raise ValueError("카탈로그 이름을 입력하세요.")
        if key.casefold() in normalized:
            raise ValueError("카탈로그 키가 중복됩니다.")
        item = {"key": key, "label": label, "active": bool(entry["active"])}
        if is_role:
            if entry["kind"] not in ROLE_KINDS:
                raise ValueError("지원하지 않는 폴더 역할 종류입니다.")
            item["kind"] = entry["kind"]
        found[key] = item
        normalized.add(key.casefold())
    return found


def save_catalog(conn, payload: dict, actor: str) -> dict:
    current = catalog(conn)
    if current["revision"] != payload["expected_revision"]:
        fail("CATALOG_REVISION_CONFLICT", "폴더 옵션이 변경되었습니다. 다시 불러오세요.")
    roles = _validate_catalog_entries(payload["roles"], is_role=True)
    types = _validate_catalog_entries(payload["analysis_types"], is_role=False)
    for kind in ROLE_KINDS:
        if not any(item["kind"] == kind for item in roles.values()):
            raise ValueError(f"{kind} 종류의 폴더 역할을 하나 이상 유지하세요.")
    previous_roles = {item["key"]: item for item in current["roles"]}
    previous_types = {item["key"]: item for item in current["analysis_types"]}
    # Keys are durable references in saved rules and previews. Deletion is an
    # active=false transition, never removal from this singleton document.
    if set(previous_roles) - set(roles) or set(previous_types) - set(types):
        raise ValueError("기존 옵션은 삭제하지 말고 비활성화하세요.")
    used_roles = {str(row[0]): str(row[1]) for row in conn.execute("SELECT DISTINCT role, role_kind FROM folder_discovery_registry").fetchall()}
    for key, kind in used_roles.items():
        if key not in roles or roles[key]["kind"] != kind:
            raise ValueError("사용 중인 폴더 역할의 키와 종류는 변경할 수 없습니다.")
    revision = current["revision"] + 1
    result = {"revision": revision, "roles": list(roles.values()), "analysis_types": list(types.values())}
    conn.execute("UPDATE folder_discovery_catalog SET revision=?,roles_json=?,analysis_types_json=?,updated_at=?,updated_by=? WHERE id=1",
                 [revision, json.dumps(result["roles"], ensure_ascii=False), json.dumps(result["analysis_types"], ensure_ascii=False), now(), actor])
    return result


def registry(conn, root_key: str) -> list[dict]:
    result = [dict(row) for row in rows(conn.execute("SELECT * FROM folder_discovery_registry WHERE root_key=?", [root_key]))]
    targets = {}
    for role, table, label, parent in (("PROJECT", "projects", "name", "NULL"),
                                      ("REQUEST", "analysis_requests", "title", "t.project_id"),
                                      ("LOAD_CASE", "load_cases", "name", "t.request_id")):
        records = conn.execute(f"SELECT t.id,t.{label},{parent} FROM {table} t JOIN folder_discovery_registry r ON r.target_id=t.id "
                               "WHERE r.root_key=? AND r.role_kind=?", [root_key, role]).fetchall()
        targets.update({(role, str(row[0])): (str(row[1]), row[2]) for row in records})
    for row in result:
        if row["role_kind"] in ("PROJECT", "REQUEST", "LOAD_CASE"):
            row["target_valid"] = targets.get((row["role_kind"], row["target_id"])) == (row["name"], row["parent_target_id"])
        else:
            row["target_valid"] = bool(conn.execute("SELECT 1 FROM load_cases WHERE id=?", [row["parent_target_id"]]).fetchone())
    types = dict(conn.execute("SELECT t.id,t.analysis_type FROM load_cases t JOIN folder_discovery_registry r ON r.target_id=t.id WHERE r.root_key=? AND r.role_kind='LOAD_CASE'", [root_key]).fetchall())
    for row in result:
        if row["role_kind"] == "LOAD_CASE":
            row["target_valid"] = row["target_valid"] and types.get(row["target_id"]) == row["analysis_type"]
        row["code"] = row["code"] or ""
    return result


def _exclusion_roots(data: dict, baseline: dict, excluded_paths: list[str] | None) -> list[str]:
    """Validate UI selections against this scan and collapse nested roots."""
    if not excluded_paths:
        return []
    scanned = {folded(node["relative_path"]): node["relative_path"] for node in data["nodes"]}
    matched = {folded(row["relative_path"]) for row in baseline["rows"]}
    selected: list[str] = []
    for value in excluded_paths:
        relative = normal(value)
        canonical = scanned.get(folded(relative))
        if canonical is None or folded(canonical) not in matched:
            fail("EXCLUSION_PATH_INVALID", "조사 결과에서 역할이 일치한 폴더만 제외할 수 있습니다.", 422)
        selected.append(canonical)
    roots: list[str] = []
    for path in sorted(set(selected), key=lambda item: (item.count("/"), folded(item))):
        path_key = folded(path)
        if not any(not root or path_key == folded(root) or path_key.startswith(folded(root) + "/") for root in roots):
            roots.append(path)
    return roots


def _is_excluded(relative_path: str, roots: list[str]) -> str | None:
    path_key = folded(relative_path)
    for root in roots:
        root_key = folded(root)
        if not root_key or path_key == root_key or path_key.startswith(root_key + "/"):
            return root
    return None


def proposal(conn, data: dict, rule_list: list[dict], options: dict | None = None,
             excluded_paths: list[str] | None = None) -> dict:
    old_paths = [str(row[0]) for row in conn.execute("SELECT relative_path FROM spdm_storage_bindings").fetchall()]
    old_paths += [str(row[0]) for row in conn.execute("SELECT project_folder FROM spdm_storage_project_parents").fetchall()]
    old_paths += [str(row[0]) for row in conn.execute("SELECT request_folder FROM spdm_storage_request_parents").fetchall()]
    options = options or catalog(conn)
    current_registry = registry(conn, data["root_key"])
    bindings = mapping_repository.semantic_bindings(conn)
    baseline = build_plan(data["nodes"], rule_list, data["root_key"], current_registry, old_paths, bindings, options)
    roots = _exclusion_roots(data, baseline, excluded_paths)
    if not roots:
        return {**baseline, "excluded_paths": [], "summary": {**baseline["summary"], "excluded": 0}}
    active_nodes = [node for node in data["nodes"] if _is_excluded(node["relative_path"], roots) is None]
    active = build_plan(active_nodes, rule_list, data["root_key"], current_registry, old_paths, bindings, options)
    excluded_by_path = {row["relative_path"]: _is_excluded(row["relative_path"], roots)
                        for row in baseline["rows"]}
    excluded_rows = {}
    for row in baseline["rows"]:
        excluded_by = excluded_by_path[row["relative_path"]]
        if excluded_by is not None:
            excluded_rows.setdefault(row["relative_path"], []).append(
                {**row, "status": "EXCLUDED", "message": "선택한 제외 폴더", "excluded_by": excluded_by})
    active_rows = {}
    for row in active["rows"]:
        active_rows.setdefault(row["relative_path"], []).append(row)
    ordered_rows = []
    for node in data["nodes"]:
        key = node["relative_path"]
        ordered_rows.extend(excluded_rows.get(key, []))
        ordered_rows.extend(active_rows.get(key, []))
    excluded_count = sum(len(items) for items in excluded_rows.values())
    return {"rows": ordered_rows, "can_apply": bool(active["rows"]) and not active["summary"]["conflicts"],
            "unmatched_count": active["unmatched_count"], "excluded_paths": roots,
            "summary": {**active["summary"], "excluded": excluded_count}}


def preview(conn, scan_id: str, rule_list: list[dict], actor: str, excluded_paths: list[str] | None = None):
    data = load_scan(conn, scan_id)
    if root_identity(configured_root(conn)) != data["root_key"]:
        fail("ROOT_CHANGED", "저장소가 변경되었습니다. 다시 조사하세요.")
    if data["status"] != "COMPLETE":
        fail("SCAN_INCOMPLETE", "전체 폴더 조사가 완료되지 않아 미리보기를 만들 수 없습니다.")
    options = catalog(conn)
    plan = proposal(conn, data, rule_list, options, excluded_paths)
    preview_id = ident("folder-preview")
    revision = rules(conn, data["root_key"], data["relative_path"])["revision"]
    conn.execute("INSERT INTO folder_discovery_previews(id,scan_id,rules_json,rows_json,can_apply,rules_revision,catalog_revision,created_by,created_at) "
                 "VALUES(?,?,?,?,?,?,?,?,?)", [preview_id, scan_id, json.dumps(rule_list), json.dumps(plan["rows"], ensure_ascii=False),
                 plan["can_apply"], revision, options["revision"], actor, now()])
    return {"id": preview_id, "scan_id": scan_id, **plan}


def materialize(conn, item: dict, principal):
    target_id, stamp = item["target_id"], now()
    if item["role_kind"] == "PROJECT":
        from ..adapters.persistence.projects import SQLProjectUnitOfWork
        unit = SQLProjectUnitOfWork(conn, lambda prefix, length: ident(prefix))
        command = {"name": item["name"], "product_name": item["name"], "description": "폴더 조사로 생성한 프로젝트",
                   "manufacturer": "", "display_size_inch": None,
                   "creator": {"user_id": principal.user_id, "username": principal.username, "role": "admin"}}
        unit.add_project(target_id, command, stamp)
        unit.add_product_information(target_id, command)
        unit.add_quality_thresholds(target_id, stamp)
        unit.add_workspace_layouts(target_id)
        unit.add_admin_membership(ident("membership"), target_id, command, stamp)
    elif item["role_kind"] == "REQUEST":
        conn.execute("INSERT INTO analysis_requests(id,project_id,title,status,owner,owner_user_id,requested_at,due_at,overall_note) "
                     "VALUES(?,?,?,'READY',?,?,?,NULL,?)", [target_id, item["parent_target_id"], item["name"],
                     principal.display_name, principal.user_id, stamp, f"폴더 의뢰번호: {item['code']}"])
    elif item["role_kind"] == "LOAD_CASE":
        conn.execute("INSERT INTO load_cases(id,request_id,name,analysis_type,status,parameters_json,created_at) VALUES(?,?,?,?,'READY',?,?)",
                     [target_id, item["parent_target_id"], item["name"], item["analysis_type"],
                      json.dumps({"source": "folder_discovery", "code": item["code"]}), stamp])


def apply(conn, preview_id: str, principal, audit):
    with WRITE_LOCK, semantic_transaction(conn):
        lock_tables(conn)
        result = rows(conn.execute("SELECT * FROM folder_discovery_previews WHERE id=?", [preview_id]))
        if not result:
            fail("PREVIEW_NOT_FOUND", "미리보기를 찾을 수 없습니다.", 404)
        saved = dict(result[0])
        data = load_scan(conn, saved["scan_id"])
        root = configured_root(conn)
        if root_identity(root) != data["root_key"]:
            fail("ROOT_CHANGED", "저장소가 변경되었습니다. 다시 조사하세요.")
        if saved["applied_json"] is not None:
            return decoded(saved["applied_json"])
        if not saved["can_apply"] or data["status"] != "COMPLETE":
            fail("PREVIEW_CONFLICT", "완료되지 않은 조사나 충돌이 있는 미리보기는 적용할 수 없습니다.")
        if rules(conn, data["root_key"], data["relative_path"])["revision"] != saved["rules_revision"]:
            fail("RULES_CHANGED", "저장된 규칙이 변경되었습니다. 미리보기를 다시 만드세요.")
        if catalog(conn)["revision"] != saved["catalog_revision"]:
            fail("CATALOG_CHANGED", "폴더 옵션이 변경되었습니다. 미리보기를 다시 만드세요.")
        fresh = scan(root, data["relative_path"])
        if fresh["status"] != "COMPLETE" or fresh["nodes"] != data["nodes"]:
            fail("SCAN_STALE", "조사 이후 폴더 구조가 변경되었습니다. 다시 조사하세요.")
        saved_rows = decoded(saved["rows_json"])
        excluded_paths = sorted({row["excluded_by"] for row in saved_rows if row.get("status") == "EXCLUDED" and row.get("excluded_by") is not None},
                                key=lambda item: (item.count("/"), folded(item)))
        current = proposal(conn, data, decoded(saved["rules_json"]), excluded_paths=excluded_paths)
        if not current["can_apply"] or current["rows"] != saved_rows:
            fail("PREVIEW_STALE", "업무 항목이나 폴더 연결이 변경되었습니다. 미리보기를 다시 만드세요.")
        created = {"projects": 0, "requests": 0, "load_cases": 0}
        kept = excluded = 0
        for item in current["rows"]:
            if item["status"] == "KEEP":
                kept += 1
                continue
            if item["status"] == "EXCLUDED":
                excluded += 1
                continue
            materialize(conn, item, principal)
            conn.execute("INSERT INTO folder_discovery_registry(id,root_key,relative_path,role,role_kind,scope_key,code,name,analysis_type,target_id,parent_target_id,created_at) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", [ident("folder-registry"), data["root_key"], item["relative_path"], item["role"], item["role_kind"],
                         item["scope_key"], item["code"] or None, item["name"], item["analysis_type"], item["target_id"], item["parent_target_id"], now()])
            if item["role_kind"] in ("PROJECT", "REQUEST", "LOAD_CASE"):
                created[{"PROJECT": "projects", "REQUEST": "requests", "LOAD_CASE": "load_cases"}[item["role_kind"]]] += 1
        # Concurrent folder changes invalidate the entire transaction, including the audit.
        after = scan(root, data["relative_path"])
        if root_identity(configured_root(conn)) != data["root_key"] or after["status"] != "COMPLETE" or after["nodes"] != data["nodes"]:
            fail("SCAN_STALE", "적용 중 폴더 구조가 변경되었습니다. 다시 조사하세요.")
        outcome = {"status": "APPLIED", "created": created, "kept_count": kept, "excluded_count": excluded}
        audit({"preview_id": preview_id, "scan_id": data["id"], **outcome})
        conn.execute("UPDATE folder_discovery_previews SET applied_json=? WHERE id=?", [json.dumps(outcome), preview_id])
        return outcome
