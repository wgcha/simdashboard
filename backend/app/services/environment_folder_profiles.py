"""Versioned, reusable environment rules; legacy definitions remain untouched."""
from __future__ import annotations

from fnmatch import fnmatchcase
import json
from uuid import uuid4

from ..database_connection import rows
from . import folder_discovery as legacy

ROLE_SETS = {
    "USAGE": {"PROJECT", "REQUEST", "WORKING", "FINAL", "SIMULATION_CASE", "EVALUATION", "RESULTS", "INPUT", "CONTAINER"},
    "DISTRIBUTION": {"PROJECT", "REQUEST", "WORKING", "FINAL", "SIMULATION_CASE", "LOAD_CASE", "EXECUTION_RUN", "RUN_OPTION", "SCENE", "RESULTS", "INPUT", "CONTAINER"},
}


def validate_rules(environment, definition):
    if environment not in ROLE_SETS:
        raise ValueError("사용환경 또는 유통환경을 선택하세요.")
    rules = definition.get("rules", [])
    if not isinstance(rules, list) or len(rules) > 100:
        raise ValueError("규칙은 최대 100개까지 저장할 수 있습니다.")
    allowed = ROLE_SETS[environment]
    normalized = []
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("role_kind") not in allowed:
            raise ValueError("선택 환경에서 지원하지 않는 역할입니다.")
        parent = rule.get("parent_role")
        if parent and parent not in allowed | {"ROOT"}:
            raise ValueError("상위 역할이 올바르지 않습니다.")
        pattern = rule.get("pattern", "")
        mode = rule.get("match_mode", "glob")
        if not isinstance(pattern, str) or len(pattern) > 256 or any(ord(c) < 32 for c in pattern):
            raise ValueError("폴더 이름 규칙은 제어문자 없는 256자 이내여야 합니다.")
        if mode not in {"glob", "contains", "level"}:
            raise ValueError("규칙 일치 방식을 확인하세요.")
        depth = rule.get("depth")
        if depth is not None and (type(depth) is not int or not 0 <= depth <= 64):
            raise ValueError("깊이는 0부터 64까지 지정하세요.")
        if mode == "level" and depth is None:
            raise ValueError("Working 기준 깊이를 지정하세요.")
        item = {"role_kind": rule["role_kind"], "pattern": pattern, "match_mode": mode}
        if parent:
            item["parent_role"] = parent
        if depth is not None:
            item["depth"] = depth
        normalized.append(item)
    result = {"roles": sorted(allowed), "rules": normalized, "level_base": "WORKING"}
    description = definition.get("description", "")
    if not isinstance(description, str) or len(description) > 512 or any(ord(c) < 32 and c not in "\t\n\r" for c in description):
        raise ValueError("규칙 설명은 512자 이내여야 합니다.")
    if description.strip():
        result["description"] = description.strip()
    sources = definition.get("usage_sources")
    if sources is not None:
        if environment != "USAGE" or not isinstance(sources, dict):
            raise ValueError("사용환경 파일 선택 규칙 형식이 올바르지 않습니다.")
        if sources.get("version", 1) != 1:
            raise ValueError("지원하지 않는 사용환경 파일 선택 규칙 버전입니다.")
        selection = sources.get("selection", {})
        paths = sources.get("metric_paths", {})
        if not isinstance(selection, dict) or any(key not in {"json", "video", "image", "csv", "media"} or type(value) is not bool for key, value in selection.items()):
            raise ValueError("파일 형식 선택은 지원 형식의 true/false 값이어야 합니다.")
        if not isinstance(paths, dict) or len(paths) > 100:
            raise ValueError("JSON 키 경로는 최대 100개까지 저장할 수 있습니다.")
        clean_paths = {}
        for key, segments in paths.items():
            if not isinstance(key, str) or len(key) > 512 or not isinstance(segments, list) or not 1 <= len(segments) <= 16 or any(not isinstance(part, str) or not part or len(part) > 256 for part in segments):
                raise ValueError("JSON 키 경로는 비어 있지 않은 segment 배열이어야 합니다.")
            clean_paths[key] = list(segments)
        result["usage_sources"] = {"version": 1, "selection": dict(selection), "metric_paths": clean_paths}
    return result


def resolve_role(name, depth, parent_role, rules, environment, *, working_level=None):
    """Return (role, matched, conflict); never silently choose overlapping roles."""
    definition = validate_rules(environment, rules)
    level_matches, named_matches = [], []
    for rule in definition["rules"]:
        if rule["match_mode"] == "level":
            match = working_level is not None and rule.get("depth") == working_level
            destination = level_matches
        else:
            if rule.get("parent_role") and rule["parent_role"] != (parent_role or "ROOT"):
                continue
            if rule.get("depth") is not None and rule["depth"] != depth:
                continue
            value, pattern = name.casefold(), rule["pattern"].casefold()
            match = pattern in value if rule["match_mode"] == "contains" else fnmatchcase(value, pattern)
            destination = named_matches
        if match:
            destination.append(rule["role_kind"])
    # A specific name rule is the explicit per-folder exception to the
    # Working-relative structural default. Multiple name matches stay a conflict.
    matches = named_matches or level_matches
    kinds = set(matches)
    return (next(iter(kinds)) if len(kinds) == 1 else None, bool(matches), len(kinds) > 1)


def save_profile(conn, *, environment, name, rules, profile_id=None, expected_revision=None):
    name = name.strip()
    if not name or len(name) > 128 or any(ord(c) < 32 for c in name):
        raise ValueError("규칙 이름은 1~128자로 입력하세요.")
    definition = validate_rules(environment, rules)
    collision = conn.execute("SELECT id FROM folder_environment_profiles WHERE environment=? AND name=?", [environment, name]).fetchone()
    if collision and str(collision[0]) != profile_id:
        legacy.fail("ENVIRONMENT_PROFILE_NAME_CONFLICT", "같은 환경에 동일한 규칙 이름이 있습니다.")
    encoded = json.dumps(definition, ensure_ascii=False)
    if profile_id:
        lock_clause = " FOR UPDATE" if getattr(conn, "backend", "") == "postgresql" else ""
        current = conn.execute("SELECT environment,revision,rules_json FROM folder_environment_profiles WHERE id=?" + lock_clause, [profile_id]).fetchone()
        if not current:
            legacy.fail("ENVIRONMENT_PROFILE_NOT_FOUND", "저장 규칙을 찾을 수 없습니다.", 404)
        if current[0] != environment:
            raise ValueError("저장된 규칙의 환경은 바꿀 수 없습니다. 다른 환경 규칙으로 새로 저장하세요.")
        stored_rules = json.loads(current[2]) if isinstance(current[2], str) else current[2]
        metadata = stored_rules.get("profile_metadata", {}) if isinstance(stored_rules, dict) else {}
        if isinstance(metadata, dict) and metadata.get("archived"):
            legacy.fail("ENVIRONMENT_PROFILE_ARCHIVED", "보관된 규칙은 편집할 수 없습니다. 복사해 새 규칙으로 저장하세요.")
        if current[1] != expected_revision:
            legacy.fail("ENVIRONMENT_PROFILE_REVISION_CONFLICT", "규칙이 변경되었습니다. 다시 불러오세요.")
        updated = conn.execute("""UPDATE folder_environment_profiles SET name=?,rules_json=?,revision=revision+1,
            updated_at=CURRENT_TIMESTAMP WHERE id=? AND revision=? RETURNING id""",
            [name, encoded, profile_id, expected_revision]).fetchone()
        if not updated:
            legacy.fail("ENVIRONMENT_PROFILE_REVISION_CONFLICT", "규칙이 변경되었습니다. 다시 불러오세요.")
    else:
        profile_id = f"environment-profile-{uuid4().hex}"
        conn.execute("""INSERT INTO folder_environment_profiles(id,environment,name,revision,rules_json,created_at,updated_at)
            VALUES (?,?,?,1,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""", [profile_id, environment, name, encoded])
    record = rows(conn.execute("SELECT * FROM folder_environment_profiles WHERE id=?", [profile_id]))[0]
    raw = record.pop("rules_json")
    return {**record, "rules": json.loads(raw) if isinstance(raw, str) else raw}


def copy_legacy(conn, environment, name, relative_path):
    root = legacy.configured_root(conn)
    record = conn.execute("SELECT rules_json FROM folder_discovery_rules WHERE root_key=? AND relative_path=?",
        [legacy.root_identity(root), legacy.normal(relative_path)]).fetchone()
    if not record:
        legacy.fail("FOLDER_RULES_NOT_FOUND", "해당 위치의 기존 규칙이 없습니다.", 404)
    original = json.loads(record[0]) if isinstance(record[0], str) else record[0]
    catalog = legacy.catalog(conn)
    role_kinds = {r["key"]: r["kind"] for r in catalog["roles"]}
    # Copy only roles with the same semantics. Old LOAD_CASE is deliberately
    # not silently converted into a new Case or a distribution load branch.
    copied, omitted = [], []
    for item in original:
        kind = role_kinds.get(item["role"], item["role"])
        if kind not in {"PROJECT", "REQUEST", "INPUT", "RESULTS"}:
            omitted.append(item)
            continue
        copied.append({"role_kind": kind, "depth": item["depth"], "pattern": item.get("keyword") or item.get("prefix", ""), "match_mode": "contains"})
    profile = save_profile(conn, environment=environment, name=name, rules={"rules": copied})
    return {**profile, "requires_review": True, "omitted_rules": omitted,
        "message": "기존 규칙은 보존했습니다. 하중경우와 이름·코드 추출은 자동 전환하지 않습니다. 구조 확인 후 사용하세요."}


def history(conn, limit=50, offset=0):
    from .folder_discovery_environment import registration
    root_key = legacy.root_identity(legacy.configured_root(conn))
    join = """FROM folder_environment_registrations r
        JOIN folder_environment_previews p ON p.id=r.preview_id
        JOIN folder_environment_scans s ON s.id=p.scan_id WHERE s.root_key=?"""
    count = conn.execute("SELECT count(*) " + join, [root_key]).fetchone()[0]
    found = conn.execute("SELECT r.id " + join + " ORDER BY r.created_at DESC,r.id LIMIT ? OFFSET ?", [root_key, limit, offset]).fetchall()
    return {"total": count, "items": [registration(conn, str(row[0])) for row in found]}


def archive_profile(conn, profile_id, expected_revision):
    lock_clause = " FOR UPDATE" if getattr(conn, "backend", "") == "postgresql" else ""
    row = conn.execute("SELECT environment,name,revision,rules_json,updated_at FROM folder_environment_profiles WHERE id=?" + lock_clause, [profile_id]).fetchone()
    if not row:
        legacy.fail("ENVIRONMENT_PROFILE_NOT_FOUND", "저장 규칙을 찾을 수 없습니다.", 404)
    if int(row[2]) != expected_revision:
        legacy.fail("ENVIRONMENT_PROFILE_REVISION_CONFLICT", "규칙이 변경되었습니다. 다시 불러오세요.")
    definition = json.loads(row[3]) if isinstance(row[3], str) else row[3]
    metadata = definition.get("profile_metadata") if isinstance(definition, dict) else None
    if isinstance(metadata, dict) and metadata.get("archived"):
        legacy.fail("ENVIRONMENT_PROFILE_ALREADY_ARCHIVED", "이미 보관된 규칙입니다.")
    definition = dict(definition)
    definition["profile_metadata"] = {"archived": True, "archived_revision": expected_revision}
    changed = conn.execute("UPDATE folder_environment_profiles SET rules_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND revision=? AND updated_at=? RETURNING id",
                           [json.dumps(definition, ensure_ascii=False), profile_id, expected_revision, row[4]]).fetchone()
    if not changed:
        legacy.fail("ENVIRONMENT_PROFILE_REVISION_CONFLICT", "규칙이 변경되었습니다. 다시 불러오세요.")
    return {"id": profile_id, "environment": str(row[0]), "name": str(row[1]), "revision": expected_revision, "archived": True}
