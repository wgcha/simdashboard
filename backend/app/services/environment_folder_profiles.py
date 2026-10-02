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
    if is_depth_rules(rules):
        raise ValueError("깊이 스키마는 깊이 스키마 저장으로만 바꿀 수 있습니다.")
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


def history(conn, limit=50, offset=0, include_deleted=False):
    """Registration history; DELETED tombstones are hidden unless ``include_deleted`` (§13.6)."""
    from .folder_discovery_environment import registration
    root_key = legacy.root_identity(legacy.configured_root(conn))
    join = """FROM folder_environment_registrations r
        JOIN folder_environment_previews p ON p.id=r.preview_id
        JOIN folder_environment_scans s ON s.id=p.scan_id WHERE s.root_key=?"""
    if not include_deleted:
        join += " AND r.status<>'DELETED'"
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


# ---------------------------------------------------------------------------
# DEPTH_V1 depth-based folder role schema (docs/contracts/depth-schema.md)
# ---------------------------------------------------------------------------
DEPTH_FORMAT = "DEPTH_V1"
ENVIRONMENT_KEYWORDS = {"USAGE": "사용", "DISTRIBUTION": "유통"}
DEPTH_ROLE_SETS = {
    "USAGE": frozenset({"PROJECT", "REQUEST", "WORKING", "FINAL", "FINAL_CAE", "FINAL_REPORTS", "FINAL_CAD",
                        "FINAL_VERSION", "SIMULATION_CASE", "SCENE", "CONTAINER"}),
    "DISTRIBUTION": frozenset({"PROJECT", "REQUEST", "WORKING", "FINAL", "FINAL_CAE", "FINAL_REPORTS", "FINAL_CAD",
                               "FINAL_VERSION", "SIMULATION_CASE", "SCENE", "CONTAINER",
                               "LOAD_CASE", "EXECUTION_RUN", "RUN_OPTION"}),
}
UPPER_MAX_LEVELS = 8
# Sanity bound for the editable lower section (not a contract rule; a bound on input size).
LOWER_MAX_LEVELS = 16
FINAL_BLOCK = {
    "fixed": True,
    "children": [
        {"name": "CAE", "role": "FINAL_CAE"},
        {"name": "Reports", "role": "FINAL_REPORTS"},
        {"name": "CAD", "role": "FINAL_CAD", "below": "CONTENT"},
    ],
    "ignored": [".finalizations"],
    "below_child": ["SIMULATION_CASE", "FINAL_VERSION", "MIRROR_WORKING_FROM_LEVEL_3"],
}
DEFAULT_UPPER = {"levels": [{"level": 1, "role": "PROJECT"}, {"level": 2, "role": "REQUEST"}]}
DEFAULT_LOWER = {
    "USAGE": {"levels": [{"level": 1, "role": "WORKING", "fixed_name": "Working"},
                         {"level": 2, "role": "SIMULATION_CASE"}, {"level": 3, "role": "SCENE"}],
              "below_last": "CONTENT"},
    "DISTRIBUTION": {"levels": [{"level": 1, "role": "WORKING", "fixed_name": "Working"},
                                {"level": 2, "role": "SIMULATION_CASE"}, {"level": 3, "role": "LOAD_CASE"},
                                {"level": 4, "role": "EXECUTION_RUN"}, {"level": 5, "role": "RUN_OPTION"},
                                {"level": 6, "role": "SCENE"}],
                     "below_last": "CONTENT"},
}
DEVIATION_MESSAGES = {
    "ENV_KEYWORD_BOTH": "의뢰 폴더 이름에 '사용'과 '유통'이 모두 있어 환경을 정할 수 없습니다.",
    "ENV_KEYWORD_NONE": "의뢰 폴더 이름에 '사용' 또는 '유통'이 없어 환경을 정할 수 없습니다.",
    "WORKING_MISSING": "의뢰 폴더 아래에 Working 폴더가 없습니다.",
    "UNEXPECTED_REQUEST_CHILD": "의뢰 폴더 바로 아래에는 Working과 Final만 둘 수 있습니다.",
    "UNEXPECTED_FINAL_CHILD": "Final 바로 아래에는 CAE, Reports, CAD만 둘 수 있습니다.",
    "FINAL_VERSION_INVALID": "Final Case 아래 폴더 이름이 Final 지정 ID(32자리 hex) 형식이 아닙니다.",
}
BLOCKING_DEVIATIONS = frozenset({"ENV_KEYWORD_BOTH", "ENV_KEYWORD_NONE", "WORKING_MISSING", "UNEXPECTED_REQUEST_CHILD"})
WARNING_DEVIATIONS = frozenset({"UNEXPECTED_FINAL_CHILD", "FINAL_VERSION_INVALID"})
_FINAL_CHILDREN = {item["name"].casefold(): item["role"] for item in FINAL_BLOCK["children"]}
_FINAL_VERSION = __import__("re").compile(r"[0-9a-fA-F]{32}")
_LEVEL_KEYS = {"level", "role"}
_LOWER_LEVEL_KEYS = {"level", "role", "fixed_name"}


def is_depth_rules(definition) -> bool:
    return isinstance(definition, dict) and definition.get("format") == DEPTH_FORMAT


def is_ignored_name(name: str) -> bool:
    """§5.1: hidden/system folders are never part of the schema, counts or samples."""
    return str(name).startswith((".", "$", "~"))


def keyword_environment(name: str):
    """(environment, deviation_code) from a request folder name (D4)."""
    found = [env for env, keyword in ENVIRONMENT_KEYWORDS.items() if keyword in str(name)]
    if len(found) == 1:
        return found[0], None
    return None, "ENV_KEYWORD_BOTH" if found else "ENV_KEYWORD_NONE"


def _check_level_entries(levels, allowed_keys, label):
    if not isinstance(levels, list) or not levels:
        raise ValueError(f"{label} 깊이 목록이 비어 있습니다.")
    for index, item in enumerate(levels):
        if not isinstance(item, dict):
            raise ValueError(f"{label} 깊이 항목 형식이 올바르지 않습니다.")
        extra = set(item) - allowed_keys
        if extra:
            raise ValueError(f"{label} 깊이에는 이름 조건({', '.join(sorted(extra))})을 둘 수 없습니다. 역할은 깊이로만 정합니다.")
        if type(item.get("level")) is not int or item["level"] != index + 1:
            raise ValueError(f"{label} 깊이는 1부터 연속이어야 합니다.")
        if not isinstance(item.get("role"), str):
            raise ValueError(f"{label} 깊이 역할이 올바르지 않습니다.")


def validate_depth_upper(upper):
    if not isinstance(upper, dict) or set(upper) - {"levels"}:
        raise ValueError("상위 구조 형식이 올바르지 않습니다.")
    levels = upper.get("levels")
    _check_level_entries(levels, _LEVEL_KEYS, "상위 구조")
    if len(levels) > UPPER_MAX_LEVELS:
        raise ValueError(f"상위 구조는 최대 {UPPER_MAX_LEVELS}단계입니다.")
    roles = [item["role"] for item in levels]
    if any(role not in {"PROJECT", "REQUEST", "CONTAINER"} for role in roles):
        raise ValueError("상위 구조 역할은 PROJECT, REQUEST, CONTAINER만 쓸 수 있습니다.")
    if roles.count("PROJECT") != 1 or roles.count("REQUEST") != 1:
        raise ValueError("상위 구조에는 PROJECT와 REQUEST가 정확히 1개씩 있어야 합니다.")
    if roles[-1] != "REQUEST":
        raise ValueError("REQUEST는 상위 구조의 마지막 깊이여야 합니다.")
    if roles.index("PROJECT") > roles.index("REQUEST"):
        raise ValueError("PROJECT는 REQUEST보다 얕아야 합니다.")
    return {"levels": [{"level": item["level"], "role": item["role"]} for item in levels]}


def validate_depth_lower(environment, lower):
    if environment not in DEPTH_ROLE_SETS:
        raise ValueError("사용환경 또는 유통환경을 선택하세요.")
    if not isinstance(lower, dict) or set(lower) - {"levels", "below_last"}:
        raise ValueError("하위 구조 형식이 올바르지 않습니다.")
    if lower.get("below_last", "CONTENT") != "CONTENT":
        raise ValueError("마지막 깊이 아래는 내용물(CONTENT)로만 둘 수 있습니다.")
    levels = lower.get("levels")
    _check_level_entries(levels, _LOWER_LEVEL_KEYS, "하위 구조")
    if len(levels) > LOWER_MAX_LEVELS:
        raise ValueError(f"하위 구조는 최대 {LOWER_MAX_LEVELS}단계입니다.")
    for item in levels[1:]:
        if "fixed_name" in item:
            raise ValueError("이름 고정은 1단계 Working에만 쓸 수 있습니다. 역할은 깊이로만 정합니다.")
    first = levels[0]
    if first["role"] != "WORKING" or first.get("fixed_name", "Working") != "Working":
        raise ValueError("하위 1단계는 Working이어야 합니다.")
    if len(levels) < 3:
        raise ValueError("하위 구조는 Working, Case, Scene을 포함해야 합니다.")
    if levels[1]["role"] != "SIMULATION_CASE":
        raise ValueError("하위 2단계는 SIMULATION_CASE여야 합니다.")
    if levels[-1]["role"] != "SCENE":
        raise ValueError("하위 구조의 마지막 깊이는 SCENE이어야 합니다.")
    allowed_middle = DEPTH_ROLE_SETS[environment] & {"LOAD_CASE", "EXECUTION_RUN", "RUN_OPTION", "SCENE", "CONTAINER"}
    for item in levels[2:]:
        if item["role"] not in allowed_middle:
            raise ValueError("선택 환경의 하위 구조에서 쓸 수 없는 역할입니다.")
    if environment == "DISTRIBUTION" and not any(item["role"] == "RUN_OPTION" for item in levels):
        raise ValueError("유통환경 하위 구조에는 RUN_OPTION 깊이가 필요합니다.")
    normalized = [{"level": 1, "role": "WORKING", "fixed_name": "Working"}]
    normalized.extend({"level": item["level"], "role": item["role"]} for item in levels[1:])
    return {"levels": normalized, "below_last": "CONTENT"}


def _validate_usage_sources(environment, sources):
    if sources is None:
        return None
    if environment != "USAGE":
        raise ValueError("사용환경 파일 선택 규칙은 사용환경에만 저장할 수 있습니다.")
    return validate_rules("USAGE", {"rules": [], "usage_sources": sources}).get("usage_sources")


def validate_depth_rules(environment, definition):
    """Validate one stored/submitted DEPTH_V1 profile row (§4.2, §4.3)."""
    if not is_depth_rules(definition):
        raise ValueError("깊이 스키마 형식이 아닙니다.")
    if environment not in DEPTH_ROLE_SETS:
        raise ValueError("사용환경 또는 유통환경을 선택하세요.")
    keyword = definition.get("environment_keyword", ENVIRONMENT_KEYWORDS[environment])
    if keyword != ENVIRONMENT_KEYWORDS[environment]:
        raise ValueError("환경 키워드는 변경할 수 없습니다.")
    result = {
        "format": DEPTH_FORMAT,
        "schema_set_id": definition.get("schema_set_id"),
        "upper": validate_depth_upper(definition.get("upper")),
        "environment_keyword": ENVIRONMENT_KEYWORDS[environment],
        "lower": validate_depth_lower(environment, definition.get("lower")),
        # §4.3: the Final block is a server constant; any submitted value is ignored.
        "final": json.loads(json.dumps(FINAL_BLOCK)),
        "usage_sources": _validate_usage_sources(environment, definition.get("usage_sources")),
    }
    if not isinstance(result["schema_set_id"], str) or not result["schema_set_id"]:
        raise ValueError("깊이 스키마 세트 ID가 없습니다.")
    metadata = definition.get("profile_metadata")
    if isinstance(metadata, dict):
        result["profile_metadata"] = dict(metadata)
    return result


_legacy_validate_rules = validate_rules


def validate_rules(environment, definition):  # noqa: F811 - format-aware wrapper
    if is_depth_rules(definition):
        return validate_depth_rules(environment, definition)
    return _legacy_validate_rules(environment, definition)


def default_depth_rules(environment, schema_set_id, *, upper=None, lower=None, usage_sources=None):
    return validate_depth_rules(environment, {
        "format": DEPTH_FORMAT, "schema_set_id": schema_set_id,
        "upper": upper or DEFAULT_UPPER, "environment_keyword": ENVIRONMENT_KEYWORDS[environment],
        "lower": lower or DEFAULT_LOWER[environment],
        "usage_sources": usage_sources if environment == "USAGE" else None,
    })


def usage_sources_evaluation_to_scene(sources):
    """D8: rewrite EVALUATION role references in copied Usage source rules to SCENE."""
    import re as _re
    if sources is None:
        return None

    def convert(value):
        if isinstance(value, str):
            return _re.sub(r"\bEVALUATION\b", "SCENE", value)
        if isinstance(value, list):
            return [convert(item) for item in value]
        if isinstance(value, dict):
            return {convert(key): convert(item) for key, item in value.items()}
        return value
    return convert(sources)


# ---- pure interpretation (§5) ------------------------------------------------

def _depth_parts(schema):
    upper = schema["upper"]["levels"]
    roles = [item["role"] for item in upper]
    return upper, roles.index("PROJECT") + 1, len(upper)


def resolve_path(parts, schema):
    """Resolve a storage-root-relative folder path against one DEPTH_V1 profile.

    ``schema`` is a validated DEPTH_V1 rules dict for one environment. Returns
    ``None`` for ignored folders, otherwise a dict with ``level``, ``segment``,
    ``role_kind``, ``status``, ``deviation``, ``environment`` and, when the
    request keyword names the other environment, ``out_of_scope: True``.
    Tree-level conditions (``WORKING_MISSING``, ``BRANCH_INCOMPLETE``) are
    added by :func:`annotate_tree` because they depend on sibling/child folders.
    """
    parts = [str(part) for part in parts if str(part)]
    if any(is_ignored_name(part) for part in parts):
        return None
    upper, _project_level, request_level = _depth_parts(schema)
    depth = len(parts)

    def node(level, segment, role, status, code=None, environment=None, **extra):
        deviation = {"code": code, "message": DEVIATION_MESSAGES[code]} if code else None
        return {"level": level, "segment": segment, "role_kind": role, "status": status,
                "deviation": deviation, "environment": environment, "info": None, **extra}

    if depth == 0:
        return node(0, "UPPER", None, "CONTENT")
    if depth < request_level:
        return node(depth, "UPPER", upper[depth - 1]["role"], "CONFIRMED")
    environment, keyword_code = keyword_environment(parts[request_level - 1])
    own_environment = next((env for env, word in ENVIRONMENT_KEYWORDS.items()
                            if word == schema.get("environment_keyword")), None)
    out_of_scope = bool(environment and own_environment and environment != own_environment)
    if depth == request_level:
        if keyword_code:
            return node(depth, "UPPER", "REQUEST", "UNRESOLVED", keyword_code)
        return node(depth, "UPPER", "REQUEST", "CONFIRMED", environment=environment, out_of_scope=out_of_scope)
    k = depth - request_level
    branch = parts[request_level].casefold()
    segment = "FINAL" if branch == "final" else "LOWER"
    if keyword_code:
        # §5.3: the whole lower section stays unresolved and is not explored.
        return node(k, segment, None, "UNRESOLVED")
    extra = {"out_of_scope": out_of_scope}
    if k == 1:
        if branch == "working":
            return node(1, "LOWER", "WORKING", "CONFIRMED", environment=environment, **extra)
        if branch == "final":
            return node(1, "FINAL", "FINAL", "CONFIRMED", environment=environment, **extra)
        return node(1, "LOWER", None, "UNRESOLVED", "UNEXPECTED_REQUEST_CHILD", environment, **extra)
    lower = schema["lower"]["levels"]
    if branch == "working":
        if k <= len(lower):
            return node(k, "LOWER", lower[k - 1]["role"], "CONFIRMED", environment=environment, **extra)
        return node(k, "LOWER", None, "CONTENT", environment=environment, **extra)
    if branch != "final":
        return node(k, "LOWER", None, "UNRESOLVED", environment=environment, **extra)
    final_child = parts[request_level + 1].casefold()
    final_role = _FINAL_CHILDREN.get(final_child)
    if k == 2:
        if final_role:
            return node(2, "FINAL", final_role, "CONFIRMED", environment=environment, **extra)
        return node(2, "FINAL", None, "CONTENT", "UNEXPECTED_FINAL_CHILD", environment, **extra)
    if final_role is None or final_role == "FINAL_CAD":
        return node(k, "FINAL", None, "CONTENT", environment=environment, **extra)
    if k == 3:
        return node(3, "FINAL", "SIMULATION_CASE", "CONFIRMED", environment=environment, **extra)
    if k == 4:
        valid = bool(_FINAL_VERSION.fullmatch(parts[depth - 1]))
        return node(4, "FINAL", "FINAL_VERSION", "CONFIRMED",
                    None if valid else "FINAL_VERSION_INVALID", environment, **extra)
    mirrored = k - 2  # Final k=5 mirrors Working L3
    if mirrored <= len(lower):
        return node(k, "FINAL", lower[mirrored - 1]["role"], "CONFIRMED", environment=environment, **extra)
    return node(k, "FINAL", None, "CONTENT", environment=environment, **extra)


def annotate_tree(entries, schema):
    """Add WORKING_MISSING and BRANCH_INCOMPLETE to resolved nodes in place.

    ``entries`` are dicts with ``relative_path``, ``parent_path``, an optional
    ``children_skipped`` flag and the :func:`resolve_path` fields. Only folders
    whose children were actually listed are judged.
    """
    _upper, project_level, request_level = _depth_parts(schema)
    lower_count = len(schema["lower"]["levels"])
    children: dict[str, list[dict]] = {}
    for entry in entries:
        parent = entry.get("parent_path")
        if parent is not None:
            children.setdefault(str(parent).casefold(), []).append(entry)
    for entry in entries:
        if entry.get("children_skipped") or entry.get("status") == "EXCLUDED" or entry.get("out_of_scope"):
            continue
        kids = children.get(str(entry.get("relative_path") or "").casefold(), [])
        segment, level = entry.get("segment"), entry.get("level")
        if segment == "UPPER" and level == request_level and not entry.get("deviation"):
            if not any(str(kid.get("name") or "").casefold() == "working" for kid in kids):
                entry["status"] = "UNRESOLVED"
                entry["deviation"] = {"code": "WORKING_MISSING", "message": DEVIATION_MESSAGES["WORKING_MISSING"]}
            continue
        if kids or entry.get("deviation"):
            continue
        if segment == "UPPER" and project_level <= level < request_level:
            entry["info"] = "BRANCH_INCOMPLETE"
        elif segment == "LOWER" and entry.get("role_kind") and level < lower_count:
            entry["info"] = "BRANCH_INCOMPLETE"
    return entries


def resolve_tree(paths, schema):
    """Resolve a list of root-relative folder paths (convenience for tests/check)."""
    entries = []
    for path in paths:
        parts = [part for part in str(path).split("/") if part]
        resolved = resolve_path(parts, schema)
        if resolved is None:
            continue
        parent = "/".join(parts[:-1]) if parts else None
        entries.append({"relative_path": "/".join(parts), "parent_path": parent,
                        "name": parts[-1] if parts else "", **resolved})
    return annotate_tree(entries, schema)


# ---- schema sets: storage (§4.1) --------------------------------------------

def _decoded(value):
    return json.loads(value) if isinstance(value, str) else value


def _depth_rows(conn, *, include_archived=False):
    found = []
    for row in conn.execute("SELECT id,environment,name,revision,rules_json,created_at,updated_at "
                            "FROM folder_environment_profiles ORDER BY created_at DESC,id DESC").fetchall():
        rules = _decoded(row[4])
        if not is_depth_rules(rules):
            continue
        metadata = rules.get("profile_metadata") if isinstance(rules.get("profile_metadata"), dict) else {}
        if metadata.get("archived") and not include_archived:
            continue
        found.append({"id": str(row[0]), "environment": str(row[1]), "name": str(row[2]),
                      "revision": int(row[3]), "rules": rules, "created_at": row[5], "updated_at": row[6]})
    return found


def _current_set(conn):
    rows_by_env = {}
    for item in _depth_rows(conn):
        rows_by_env.setdefault(item["environment"], item)
    if set(rows_by_env) != {"USAGE", "DISTRIBUTION"}:
        legacy.fail("DEPTH_SCHEMA_MISSING", "현재 깊이 스키마가 없습니다. 마이그레이션을 확인하세요.", 409)
    set_ids = {item["rules"].get("schema_set_id") for item in rows_by_env.values()}
    if len(set_ids) != 1:
        legacy.fail("DEPTH_SCHEMA_INCONSISTENT", "사용·유통 깊이 스키마가 서로 다른 세트입니다.", 409)
    return rows_by_env


def _schema_response(rows_by_env):
    usage = rows_by_env["USAGE"]
    created_by = (usage["rules"].get("profile_metadata") or {}).get("created_by")
    return {
        "schema_set_id": usage["rules"]["schema_set_id"],
        "upper": usage["rules"]["upper"],
        "environments": {env: {"profile_id": item["id"], "environment_keyword": item["rules"]["environment_keyword"],
                               "lower": item["rules"]["lower"], "usage_sources": item["rules"].get("usage_sources")}
                         for env, item in rows_by_env.items()},
        "final": json.loads(json.dumps(FINAL_BLOCK)),
        "created_at": usage["created_at"],
        "created_by": created_by,
    }


def get_depth_schema(conn):
    """Current DEPTH_V1 set: {schema_set_id, upper, environments:{USAGE, DISTRIBUTION}, created_at, created_by}."""
    return _schema_response(_current_set(conn))


def _validated_draft(upper, environments):
    """Validate an editor draft; returns (upper, {env: lower}, {env: usage_sources}). Raises ValueError (422)."""
    clean_upper = validate_depth_upper(upper)
    if not isinstance(environments, dict) or set(environments) != {"USAGE", "DISTRIBUTION"}:
        raise ValueError("사용환경과 유통환경 하위 구조를 모두 보내세요.")
    lowers, sources = {}, {}
    for environment, item in environments.items():
        if not isinstance(item, dict):
            raise ValueError("환경별 하위 구조 형식이 올바르지 않습니다.")
        keyword = item.get("environment_keyword", ENVIRONMENT_KEYWORDS[environment])
        if keyword != ENVIRONMENT_KEYWORDS[environment]:
            raise ValueError("환경 키워드는 변경할 수 없습니다.")
        lowers[environment] = validate_depth_lower(environment, item.get("lower"))
        sources[environment] = _validate_usage_sources(environment, item.get("usage_sources"))
    return clean_upper, lowers, sources


def save_depth_schema(conn, actor, expected_schema_set_id, upper, environments):
    """Insert a new immutable USAGE+DISTRIBUTION pair and archive the previous set.

    Raises ValueError for invalid input (router maps to 422) and HTTPException
    409 ``DEPTH_SCHEMA_CONFLICT`` when ``expected_schema_set_id`` is not current.
    The caller owns the transaction and the write lock.
    """
    clean_upper, lowers, sources = _validated_draft(upper, environments)
    lock_clause = " FOR UPDATE" if getattr(conn, "backend", "") == "postgresql" else ""
    if lock_clause:
        conn.execute("SELECT id FROM folder_environment_profiles" + lock_clause).fetchall()
    current = _current_set(conn)
    current_id = current["USAGE"]["rules"]["schema_set_id"]
    if expected_schema_set_id != current_id:
        legacy.fail("DEPTH_SCHEMA_CONFLICT", "다른 관리자가 깊이 스키마를 먼저 저장했습니다. 다시 불러오세요.", 409)
    schema_set_id = f"dss-{uuid4()}"
    actor_id = getattr(actor, "user_id", actor)
    for environment in ("USAGE", "DISTRIBUTION"):
        previous = current[environment]
        archived = dict(previous["rules"])
        archived["profile_metadata"] = {**(archived.get("profile_metadata") or {}), "archived": True,
                                        "archived_revision": previous["revision"], "superseded_by": schema_set_id}
        changed = conn.execute(
            "UPDATE folder_environment_profiles SET rules_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND revision=? RETURNING id",
            [json.dumps(archived, ensure_ascii=False), previous["id"], previous["revision"]]).fetchone()
        if not changed:
            legacy.fail("DEPTH_SCHEMA_CONFLICT", "다른 관리자가 깊이 스키마를 먼저 저장했습니다. 다시 불러오세요.", 409)
        rules = default_depth_rules(environment, schema_set_id, upper=clean_upper, lower=lowers[environment],
                                    usage_sources=sources[environment])
        rules["profile_metadata"] = {"created_by": actor_id, "supersedes": current_id}
        conn.execute("INSERT INTO folder_environment_profiles(id,environment,name,revision,rules_json,created_at,updated_at) "
                     "VALUES (?,?,?,1,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                     [f"environment-profile-{uuid4().hex}", environment, f"깊이 스키마 {schema_set_id}",
                      json.dumps(rules, ensure_ascii=False)])
    return get_depth_schema(conn)


# ---- samples & dry-run check (§6) --------------------------------------------

SAMPLE_REQUESTS = 20
SAMPLE_NAMES = 8
RUN_OPTION_NAME_LIMIT = 500


def _lister(root):
    from .folder_auto_discovery import _Lister
    return _Lister(root)


def _visible_children(lister, relative):
    return [(name, path) for name, path in lister.children(relative) if not is_ignored_name(name)]


def _upper_requests(lister, upper):
    """BFS the upper section. Returns (levels{d: [(name,path)]}, requests[(name,path)])."""
    request_level = len(upper["levels"])
    frontier = [("", "")]
    levels = {}
    for depth in range(1, request_level + 1):
        next_frontier = []
        for _name, path in frontier:
            next_frontier.extend(_visible_children(lister, path))
        levels[depth] = next_frontier
        frontier = next_frontier
    return levels, frontier


def _level_summary(level, names, truncated=False):
    counts = {}
    for name in names:
        key = name.casefold()
        entry = counts.setdefault(key, {"name": name, "count": 0})
        entry["count"] += 1
    ordered = sorted(counts.values(), key=lambda item: (-item["count"], item["name"].casefold()))
    return {"level": level, "folder_count": len(names), "samples": ordered[:SAMPLE_NAMES],
            "truncated": truncated or len(ordered) > SAMPLE_NAMES}


def depth_schema_samples(conn, root, segment, upper=None):
    """Read-only depth samples for the editor (§6 POST …/depth-schema/samples)."""
    if segment not in {"UPPER", "USAGE", "DISTRIBUTION"}:
        raise ValueError("segment는 UPPER, USAGE, DISTRIBUTION 중 하나여야 합니다.")
    upper = validate_depth_upper(upper) if upper is not None else get_depth_schema(conn)["upper"]
    lister = _lister(root)
    if segment == "UPPER":
        request_level = len(upper["levels"])
        frontier, levels = [("", "")], []
        for depth in range(1, max(request_level, 1) + 2):
            children = []
            for _name, path in frontier:
                children.extend(_visible_children(lister, path))
            if not children:
                break
            levels.append(_level_summary(depth, [name for name, _ in children]))
            frontier = children
        return {"levels": levels, "requests_sampled": 0}
    _levels, requests = _upper_requests(lister, upper)
    matching = [(name, path) for name, path in requests if keyword_environment(name)[0] == segment]
    by_level: dict[int, list[str]] = {}
    for _name, request_path in matching[:SAMPLE_REQUESTS]:
        # Only the Working branch is aggregated (Final is fixed, not editable).
        frontier = [(name, path) for name, path in _visible_children(lister, request_path) if name.casefold() == "working"]
        depth = 1
        while frontier and depth <= LOWER_MAX_LEVELS + 4:
            by_level.setdefault(depth, []).extend(name for name, _ in frontier)
            next_frontier = []
            for _name, path in frontier:
                next_frontier.extend(_visible_children(lister, path))
            frontier, depth = next_frontier, depth + 1
    result = {"levels": [_level_summary(level, names) for level, names in sorted(by_level.items())],
              "requests_sampled": min(len(matching), SAMPLE_REQUESTS)}
    if segment == "DISTRIBUTION":
        names, truncated = run_option_names(conn, root, upper=upper, lister=lister, requests=matching)
        result["run_option_names"] = names
        result["run_option_names_truncated"] = truncated
    if lister.issues:
        result["issues"] = lister.issues[:20]
    return result


def run_option_names(conn, root, *, upper=None, lister=None, requests=None, lower=None):
    """All folder names at the DISTRIBUTION RUN_OPTION level across every matching request.

    Names merge by casefold (first spelling kept) with folder and request counts.
    """
    lister = lister or _lister(root)
    if upper is None:
        upper = get_depth_schema(conn)["upper"]
    if requests is None:
        requests = [(name, path) for name, path in _upper_requests(lister, upper)[1]
                    if keyword_environment(name)[0] == "DISTRIBUTION"]
    if lower is None:
        try:
            lower = get_depth_schema(conn)["environments"]["DISTRIBUTION"]["lower"]
        except Exception:  # noqa: BLE001 - draft without saved schema uses the default
            lower = DEFAULT_LOWER["DISTRIBUTION"]
    option_level = next((item["level"] for item in lower["levels"] if item["role"] == "RUN_OPTION"), 5)
    merged: dict[str, dict] = {}
    for _name, request_path in requests:
        frontier = [(name, path) for name, path in _visible_children(lister, request_path) if name.casefold() == "working"]
        for _depth in range(1, option_level):
            next_frontier = []
            for _child, path in frontier:
                next_frontier.extend(_visible_children(lister, path))
            frontier = next_frontier
        seen_here = set()
        for name, _path in frontier:
            key = name.casefold()
            entry = merged.setdefault(key, {"name": name, "count": 0, "request_count": 0})
            entry["count"] += 1
            if key not in seen_here:
                entry["request_count"] += 1
                seen_here.add(key)
    ordered = sorted(merged.values(), key=lambda item: (-item["count"], item["name"].casefold()))
    return ordered[:RUN_OPTION_NAME_LIMIT], len(ordered) > RUN_OPTION_NAME_LIMIT


CHECK_EXAMPLES = 20


def depth_schema_check(conn, root, upper, environments):
    """Dry-run a draft schema over the storage tree: {by_code:{CODE:n}, examples:[{relative_path, code}]}.

    Read-only; walks the upper section and every request's lower section with
    the shared bounded lister.
    """
    clean_upper, lowers, _sources = _validated_draft(upper, environments)
    schemas = {env: {"upper": clean_upper, "lower": lowers[env], "environment_keyword": ENVIRONMENT_KEYWORDS[env]}
               for env in lowers}
    lister = _lister(root)
    entries = []

    def add(path, parent, name, children_listed=True):
        entries.append({"relative_path": path, "parent_path": parent, "name": name,
                        "children_skipped": not children_listed})

    _levels, requests = _upper_requests(lister, clean_upper)
    request_level = len(clean_upper["levels"])
    # Upper nodes for BRANCH_INCOMPLETE are informational only; only requests are walked.
    for request_name, request_path in requests:
        add(request_path, "/".join(request_path.split("/")[:-1]), request_name)
        environment = keyword_environment(request_name)[0]
        if environment is None:
            continue
        stack = [(request_path, 0)]
        while stack:
            path, k = stack.pop()
            for name, child in _visible_children(lister, path):
                add(child, path, name)
                if k + 1 < 6 + len(lowers[environment]["levels"]):
                    stack.append((child, k + 1))
    by_code: dict[str, int] = {}
    examples = []
    for environment, schema in schemas.items():
        scoped = []
        for entry in entries:
            parts = entry["relative_path"].split("/")
            env, _code = keyword_environment(parts[request_level - 1]) if len(parts) >= request_level else (None, None)
            if env not in (environment, None) or (env is None and environment != "USAGE"):
                continue  # keyword-less requests are counted once
            resolved = resolve_path(parts, schema)
            if resolved is None:
                continue
            scoped.append({**entry, **resolved})
        annotate_tree(scoped, schema)
        for entry in scoped:
            deviation = entry.get("deviation")
            if not deviation:
                continue
            by_code[deviation["code"]] = by_code.get(deviation["code"], 0) + 1
            if len(examples) < CHECK_EXAMPLES:
                examples.append({"relative_path": entry["relative_path"], "code": deviation["code"]})
    result = {"by_code": by_code, "examples": examples, "requests_checked": len(requests)}
    if lister.issues:
        result["issues"] = lister.issues[:20]
    return result
