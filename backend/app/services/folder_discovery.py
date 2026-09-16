"""Explicit folder discovery proposals and atomic workload creation."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4
from types import SimpleNamespace

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


def persist_rules(conn, root_key: str, relative: str, result: dict, actor: str) -> None:
    """Persist a validated revision inside the caller's existing transaction."""
    conn.execute("INSERT INTO folder_discovery_rules(root_key,relative_path,rules_json,revision,updated_at,updated_by) "
                 "VALUES(?,?,?,?,?,?) ON CONFLICT(root_key,relative_path) DO UPDATE SET rules_json=excluded.rules_json,"
                 "revision=excluded.revision,updated_at=excluded.updated_at,updated_by=excluded.updated_by",
                 [root_key, relative, json.dumps(result["rules"]), result["revision"], now(), actor])


def rules(conn, root_key: str, relative: str) -> dict:
    row = conn.execute("SELECT rules_json,revision FROM folder_discovery_rules WHERE root_key=? AND relative_path=?",
                       [root_key, relative]).fetchone()
    return {"rules": decoded(row[0]) if row else [], "revision": int(row[1]) if row else 0}


def saved_rule_locations(conn, root_key: str, *, offset: int = 0, limit: int = 100) -> dict:
    total = int(conn.execute("SELECT count(*) FROM folder_discovery_rules WHERE root_key=?", [root_key]).fetchone()[0])
    records = rows(conn.execute(
        "SELECT relative_path,revision,updated_at FROM folder_discovery_rules WHERE root_key=? "
        "ORDER BY updated_at DESC,relative_path LIMIT ? OFFSET ?",
        [root_key, limit, offset],
    ))
    return {"items": [dict(item) for item in records], "offset": offset, "limit": limit, "total": total}


def applied_history(conn, root_key: str, *, offset: int = 0, limit: int = 100) -> dict:
    total = int(conn.execute(
        "SELECT count(*) FROM folder_discovery_previews p JOIN folder_discovery_scans s ON s.id=p.scan_id "
        "WHERE s.root_key=? AND p.applied_json IS NOT NULL", [root_key],
    ).fetchone()[0])
    records = rows(conn.execute(
        "SELECT p.id,p.scan_id,s.relative_path,p.created_at,p.rules_revision,p.catalog_revision,p.applied_json "
        "FROM folder_discovery_previews p JOIN folder_discovery_scans s ON s.id=p.scan_id "
        "WHERE s.root_key=? AND p.applied_json IS NOT NULL "
        "ORDER BY p.created_at DESC,p.id DESC LIMIT ? OFFSET ?",
        [root_key, limit, offset],
    ))
    items = []
    for record in records:
        item = dict(record)
        outcome = decoded(item.pop("applied_json"))
        # History lists remain bounded even if a future outcome gains a
        # verbose per-row audit trail.  The immutable preview remains the
        # source for rule snapshot loading.
        item["outcome"] = {
            "status": outcome.get("status", "APPLIED"),
            "created": outcome.get("created", {}),
            "kept_count": int(outcome.get("kept_count", 0)),
            "excluded_count": int(outcome.get("excluded_count", 0)),
        }
        items.append(item)
    return {"items": items, "offset": offset, "limit": limit, "total": total}


def history_rules(conn, preview_id: str, root_key: str) -> dict:
    record = conn.execute(
        "SELECT p.rules_json,p.rules_revision,s.relative_path,s.root_key,p.applied_json "
        "FROM folder_discovery_previews p JOIN folder_discovery_scans s ON s.id=p.scan_id WHERE p.id=?",
        [preview_id],
    ).fetchone()
    if not record or record[3] != root_key or record[4] is None:
        fail("APPLIED_HISTORY_NOT_FOUND", "적용된 업무 생성 기록을 찾을 수 없습니다.", 404)
    return {"preview_id": preview_id, "relative_path": record[2], "rules": decoded(record[0]),
            "rules_revision": int(record[1]), "current_rules_revision": rules(conn, root_key, record[2])["revision"]}


def connections(conn, root_key: str, *, offset: int = 0, limit: int = 100) -> dict:
    total = int(conn.execute("SELECT count(*) FROM folder_discovery_registry WHERE root_key=?", [root_key]).fetchone()[0])
    records = rows(conn.execute(
        "SELECT id,relative_path,role,role_kind,code,name,analysis_type,parent_target_id,target_id,created_at "
        "FROM folder_discovery_registry WHERE root_key=? "
        "ORDER BY relative_path,role LIMIT ? OFFSET ?", [root_key, limit, offset],
    ))
    bindings = {str(row["relative_path"]): dict(row) for row in rows(conn.execute("SELECT * FROM semantic_folder_bindings"))}
    items = []
    for record in records:
        item = dict(record)
        binding = bindings.get(str(item["relative_path"]))
        owner = _result_registry_owner(conn, item) if item["role_kind"] == "RESULTS" else None
        if item["role_kind"] == "RESULTS":
            # A metadata registry target is a deterministic folder ID, never
            # the workload target.  Expose only a lineage verified from the
            # persisted load-case parent for result-query callers.
            item["project_id"] = owner[0] if owner else None
            item["request_id"] = owner[1] if owner else None
            item["load_case_id"] = owner[2] if owner else None
        if binding and item["role_kind"] == "RESULTS" and _binding_matches_owner(binding, owner):
            item["binding"] = {"id": binding["id"], "revision": binding["revision"],
                               "recipe_ids": decoded(binding["recipe_ids_json"]), "template_id": binding["template_id"]}
        elif binding and item["role_kind"] == "RESULTS":
            # The path is known to the caller through this registry row, but
            # an unrelated INPUT/parent/other-load-case binding is managed
            # only through the advanced connection screen.
            item["binding"] = {"id": None, "revision": None, "recipe_ids": [], "template_id": None,
                               "status": "ADVANCED_MANAGEMENT_REQUIRED"}
        elif item["role_kind"] == "RESULTS" and item["parent_target_id"]:
            item["binding"] = {"id": None, "revision": None, "recipe_ids": [], "template_id": None,
                               "status": "CONFIG_REQUIRED"}
        items.append(item)
    return {"items": items, "offset": offset, "limit": limit, "total": total}


def _result_registry_owner(conn, row: dict) -> tuple[str, str, str] | None:
    parent = row.get("parent_target_id")
    if not parent:
        return None
    lineage = mapping_repository.binding_lineage(conn, None, str(parent))
    if not lineage:
        return None
    return str(lineage[0]), str(lineage[1]), str(lineage[2])


def _binding_matches_owner(binding: dict, owner: tuple[str, str, str] | None) -> bool:
    return bool(owner and binding.get("role") == "RESULTS" and
                (str(binding.get("project_id") or ""), str(binding.get("request_id") or ""), str(binding.get("load_case_id") or "")) == owner)


def result_config_preview(conn, root_key: str, items: list[dict]) -> dict:
    """Validate a bulk configuration without changing registry ownership."""
    registry_rows = {str(row["id"]): dict(row) for row in rows(conn.execute(
        "SELECT * FROM folder_discovery_registry WHERE root_key=? AND role_kind='RESULTS'", [root_key]))}
    bindings = {str(row["relative_path"]): dict(row) for row in rows(conn.execute("SELECT * FROM semantic_folder_bindings"))}
    results = []
    for entry in items:
        row = registry_rows.get(str(entry.get("registry_id") or ""))
        if not row:
            results.append({"registry_id": entry.get("registry_id"), "status": "NOT_FOUND"}); continue
        binding = bindings.get(str(row["relative_path"]))
        owner = _result_registry_owner(conn, row)
        if not owner:
            results.append({"registry_id": row["id"], "status": "LOAD_CASE_REQUIRED"}); continue
        if binding and not _binding_matches_owner(binding, owner):
            results.append({"registry_id": row["id"], "status": "BINDING_CONFLICT"}); continue
        try:
            config = _result_config(entry.get("result_config"))
            _verify_result_config(conn, config)
        except ValueError as error:
            results.append({"registry_id": row["id"], "status": "CONFIG_INVALID", "message": str(error)}); continue
        except HTTPException as error:
            detail = error.detail if isinstance(error.detail, dict) else {}
            results.append({"registry_id": row["id"], "status": "CONFIG_CONFLICT", "code": detail.get("code", "SEMANTIC_CONFIG_INVALID")}); continue
        if binding and entry.get("expected_binding_revision") is None:
            results.append({"registry_id": row["id"], "status": "REVISION_REQUIRED", "current_revision": binding["revision"]}); continue
        if binding and int(entry["expected_binding_revision"]) != int(binding["revision"]):
            results.append({"registry_id": row["id"], "status": "REVISION_CONFLICT", "current_revision": binding["revision"]}); continue
        results.append({"registry_id": row["id"], "status": "UPDATE" if binding else "CREATE", "binding_id": binding["id"] if binding else None,
                        "expected_binding_revision": binding["revision"] if binding else None, "result_config": config})
    return {"items": results, "can_apply": bool(results) and all(item["status"] in {"CREATE", "UPDATE"} for item in results)}


def apply_result_config(conn, root_key: str, items: list[dict], actor: str) -> dict:
    preview = result_config_preview(conn, root_key, items)
    if not preview["can_apply"]:
        fail("RESULT_CONFIG_PREVIEW_CONFLICT", "결과 읽기 설정 미리보기를 다시 확인하세요.")
    registry_rows = {str(row["id"]): dict(row) for row in rows(conn.execute(
        "SELECT * FROM folder_discovery_registry WHERE root_key=? AND role_kind='RESULTS'", [root_key]))}
    applied = []
    for proposed in preview["items"]:
        row, config = registry_rows[proposed["registry_id"]], proposed["result_config"]
        binding = mapping_repository.binding(conn, proposed["binding_id"]) if proposed["binding_id"] else None
        if binding:
            payload = SimpleNamespace(id=binding["id"], expected_revision=int(binding["revision"]), project_id=binding["project_id"],
                                      request_id=binding["request_id"], load_case_id=binding["load_case_id"], role=binding["role"],
                                      recipe_ids=config["recipe_ids"], template_id=config.get("template_id"))
            revision = int(binding["revision"]) + 1
            mapping_repository.update_binding(conn, payload=payload, relative_path=str(binding["relative_path"]), revision=revision, actor=actor, now=now())
            applied.append({"registry_id": row["id"], "binding_id": binding["id"], "revision": revision, "status": "UPDATED"})
        else:
            # Folder registry's parent is a load case for this bulk action.
            lineage = mapping_repository.binding_lineage(conn, None, row["parent_target_id"])
            if not lineage:
                fail("SEMANTIC_BINDING_TARGET_CONFLICT", "결과 폴더의 하중 경우를 찾을 수 없습니다.")
            payload = SimpleNamespace(project_id=str(lineage[0]), request_id=str(lineage[1]), load_case_id=row["parent_target_id"], role="RESULTS", recipe_ids=config["recipe_ids"], template_id=config.get("template_id"))
            binding_id = ident("semantic-binding")
            mapping_repository.create_binding(conn, binding_id=binding_id, payload=payload, relative_path=row["relative_path"], actor=actor, now=now())
            applied.append({"registry_id": row["id"], "binding_id": binding_id, "revision": 1, "status": "CREATED"})
    return {"items": applied}


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
        if not is_role and entry.get("default_result_config") is not None:
            item["default_result_config"] = _result_config(entry["default_result_config"])
        if is_role:
            if entry["kind"] not in ROLE_KINDS:
                raise ValueError("지원하지 않는 폴더 역할 종류입니다.")
            item["kind"] = entry["kind"]
        found[key] = item
        normalized.add(key.casefold())
    return found


def _result_config(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("결과 읽기 설정 형식이 올바르지 않습니다.")
    recipe_ids, template_id = value.get("recipe_ids"), value.get("template_id")
    if not isinstance(recipe_ids, list) or not recipe_ids or len(recipe_ids) > 32:
        raise ValueError("결과 읽기 레시피를 하나 이상 선택하세요.")
    if any(not isinstance(item, str) or not item.strip() for item in recipe_ids) or len(set(recipe_ids)) != len(recipe_ids):
        raise ValueError("결과 읽기 레시피가 중복되었거나 올바르지 않습니다.")
    if template_id is not None and (not isinstance(template_id, str) or not template_id.strip()):
        raise ValueError("표시 템플릿 식별자가 올바르지 않습니다.")
    return {"recipe_ids": recipe_ids, **({"template_id": template_id} if template_id else {})}


def _resolved_result_config(rule: dict, analysis_defaults: dict[str, dict]) -> tuple[dict | None, str]:
    if rule.get("result_config") is not None:
        return _result_config(rule["result_config"]), "RULE"
    default = analysis_defaults.get(str(rule.get("analysis_type") or ""))
    return (_result_config(default), "ANALYSIS_TYPE_DEFAULT") if default else (None, "NONE")


def _attach_result_defaults(plan: dict, defaults: dict[str, dict]) -> dict:
    """Use the owning load-case type when a result rule deliberately omits policy."""
    load_types = {str(row["target_id"]): str(row.get("analysis_type") or "")
                  for row in plan["rows"] if row.get("role_kind") == "LOAD_CASE"}
    for row in plan["rows"]:
        if row.get("role_kind") != "RESULTS" or row.get("result_config") is not None:
            continue
        config = defaults.get(load_types.get(str(row.get("load_case_id") or ""), ""))
        if config:
            row["result_config"] = _result_config(config)
            row["result_config_source"] = "ANALYSIS_TYPE_DEFAULT"
            row["binding_status"] = "WILL_CREATE"
    return plan


def _attach_existing_result_bindings(conn, plan: dict) -> dict:
    """Manual bindings have precedence over any proposed folder-rule policy."""
    candidates = {str(entry["relative_path"]): entry for entry in mapping_repository.semantic_bindings(conn)}
    for row in plan["rows"]:
        if row.get("role_kind") != "RESULTS" or not row.get("load_case_id"):
            continue
        entry = candidates.get(str(row["relative_path"]))
        if not entry:
            continue
        binding = mapping_repository.binding(conn, str(entry["id"]))
        owner = (str(row.get("project_id") or ""), str(row.get("request_id") or ""), str(row.get("load_case_id") or ""))
        if not _binding_matches_owner(binding or {}, owner):
            row.update(status="CONFLICT", binding_status="BINDING_CONFLICT", message="다른 업무의 기존 결과 연결과 경로가 겹칩니다.")
            continue
        row["result_config"] = {"recipe_ids": decoded(binding["recipe_ids_json"]),
                                **({"template_id": binding["template_id"]} if binding.get("template_id") else {})}
        row["result_config_source"] = "EXISTING_BINDING"
        row["binding_status"] = "REUSE"
        row["binding_id"] = binding["id"]
        row["binding_revision"] = binding["revision"]
    return plan


def _validate_result_policies(conn, plan: dict) -> dict:
    """Make inactive/missing automatic policies visible during proposal."""
    for row in plan["rows"]:
        if row.get("role_kind") != "RESULTS" or not row.get("result_config") or row.get("result_config_source") == "EXISTING_BINDING":
            continue
        try:
            _verify_result_config(conn, row["result_config"])
        except HTTPException as error:
            detail = error.detail if isinstance(error.detail, dict) else {}
            row.update(status="CONFLICT", binding_status="CONFIG_CONFLICT", message=detail.get("code", "SEMANTIC_CONFIG_INVALID"))
    conflicts = sum(row.get("status") == "CONFLICT" for row in plan["rows"])
    plan["summary"]["conflicts"] = conflicts
    plan["can_apply"] = bool(plan["rows"]) and not conflicts
    return plan


def _verify_result_config(conn, config: dict) -> None:
    for recipe_id in config["recipe_ids"]:
        record = mapping_repository.version(conn, "recipe", recipe_id, active=True)
        if not record or record[0] is None:
            fail("SEMANTIC_RECIPE_NOT_ACTIVE", "활성 결과 읽기 레시피가 없습니다.", 422)
    if config.get("template_id"):
        record = mapping_repository.version(conn, "template", config["template_id"], active=True)
        if not record or record[0] is None:
            fail("SEMANTIC_TEMPLATE_NOT_ACTIVE", "활성 표시 템플릿이 없습니다.", 422)


def _result_binding(conn, item: dict, config: dict | None, actor: str) -> tuple[str | None, str]:
    if item["role_kind"] != "RESULTS" or not item.get("load_case_id"):
        return None, "NOT_LOAD_CASE_RESULT"
    existing = mapping_repository.semantic_bindings(conn)
    found = next((entry for entry in existing if entry["relative_path"] == item["relative_path"]), None)
    if found:
        current = mapping_repository.binding(conn, str(found["id"]))
        if not current or current.get("role") != "RESULTS" or (str(current.get("load_case_id") or "") != str(item["load_case_id"]) or
                           str(current.get("project_id") or "") != str(item["project_id"] or "") or
                           str(current.get("request_id") or "") != str(item["request_id"] or "")):
            fail("SEMANTIC_BINDING_TARGET_CONFLICT", "다른 하중 경우가 결과 폴더를 소유합니다.")
        return str(found["id"]), "REUSED"
    if config is None:
        return None, "CONFIG_REQUIRED"
    _verify_result_config(conn, config)
    binding_id = ident("semantic-binding")
    payload = SimpleNamespace(project_id=item["project_id"], request_id=item["request_id"], load_case_id=item["load_case_id"],
                              role="RESULTS", recipe_ids=config["recipe_ids"], template_id=config.get("template_id"))
    mapping_repository.create_binding(conn, binding_id=binding_id, payload=payload, relative_path=item["relative_path"], actor=actor, now=now())
    return binding_id, "CREATED"


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
            # INPUT/RESULTS registry rows are attached to the nearest real
            # workload.  They may legitimately belong directly to a project
            # or request (for example a project-level CAD directory), not
            # only to a load case.
            parent_id = row.get("parent_target_id")
            row["target_valid"] = bool(parent_id and conn.execute(
                "SELECT 1 FROM projects WHERE id=? UNION ALL "
                "SELECT 1 FROM analysis_requests WHERE id=? UNION ALL "
                "SELECT 1 FROM load_cases WHERE id=? LIMIT 1",
                [parent_id, parent_id, parent_id],
            ).fetchone())
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
    defaults = {entry["key"]: entry.get("default_result_config") for entry in options["analysis_types"]}
    enriched_rules = []
    for rule in rule_list:
        copied = dict(rule)
        option = next((entry for entry in options["roles"] if entry["key"] == copied.get("role")), None)
        if option and option["kind"] == "RESULTS":
            copied["result_config"], copied["result_config_source"] = _resolved_result_config(copied, defaults)
        enriched_rules.append(copied)
    baseline = _validate_result_policies(conn, _attach_existing_result_bindings(conn, _attach_result_defaults(
        build_plan(data["nodes"], enriched_rules, data["root_key"], current_registry, old_paths, bindings, options), defaults)))
    roots = _exclusion_roots(data, baseline, excluded_paths)
    if not roots:
        return {**baseline, "excluded_paths": [], "summary": {**baseline["summary"], "excluded": 0}}
    active_nodes = [node for node in data["nodes"] if _is_excluded(node["relative_path"], roots) is None]
    active = _validate_result_policies(conn, _attach_existing_result_bindings(conn, _attach_result_defaults(
        build_plan(active_nodes, enriched_rules, data["root_key"], current_registry, old_paths, bindings, options), defaults)))
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
        binding_outcome = {"created_ids": [], "reused_ids": [], "configuration_required": []}
        kept = excluded = 0
        for item in current["rows"]:
            if item["status"] == "KEEP":
                kept += 1
                binding_id, state = _result_binding(conn, item, item.get("result_config"), principal.user_id)
                if state == "CREATED": binding_outcome["created_ids"].append(binding_id)
                elif state == "REUSED": binding_outcome["reused_ids"].append(binding_id)
                elif state == "CONFIG_REQUIRED": binding_outcome["configuration_required"].append(item["relative_path"])
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
            binding_id, state = _result_binding(conn, item, item.get("result_config"), principal.user_id)
            if state == "CREATED": binding_outcome["created_ids"].append(binding_id)
            elif state == "REUSED": binding_outcome["reused_ids"].append(binding_id)
            elif state == "CONFIG_REQUIRED": binding_outcome["configuration_required"].append(item["relative_path"])
        # Concurrent folder changes invalidate the entire transaction, including the audit.
        after = scan(root, data["relative_path"])
        if root_identity(configured_root(conn)) != data["root_key"] or after["status"] != "COMPLETE" or after["nodes"] != data["nodes"]:
            fail("SCAN_STALE", "적용 중 폴더 구조가 변경되었습니다. 다시 조사하세요.")
        outcome = {"status": "APPLIED", "created": created, "kept_count": kept, "excluded_count": excluded, "bindings": binding_outcome}
        audit({"preview_id": preview_id, "scan_id": data["id"], **outcome})
        conn.execute("UPDATE folder_discovery_previews SET applied_json=? WHERE id=?", [json.dumps(outcome), preview_id])
        return outcome
