"""Pure proposal construction; identifiers never bind to similarly named demo data."""
from __future__ import annotations

import unicodedata
from uuid import NAMESPACE_URL, uuid5

ROLES = ("PROJECT", "REQUEST", "LOAD_CASE")
ANALYSIS_TYPES = ("DROP", "SIDE_CLAMP", "SPDM_CMS", "SPDM_MODAL", "SPDM_DEFLECTION", "SPDM_STIFFNESS", "SPDM_VIBRATION")
ENTITY_KINDS = ("PROJECT", "REQUEST", "LOAD_CASE")
METADATA_KINDS = ("RESULTS", "INPUT")


def folded(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def overlaps(left: str, right: str) -> bool:
    left, right = folded(left), folded(right)
    return not left or not right or left == right or left.startswith(right + "/") or right.startswith(left + "/")


def extract(name: str, rule: dict) -> tuple[str, str]:
    delimiter = rule.get("delimiter", "")
    if not delimiter or delimiter not in name:
        code, label = "", name.strip()
        if not label or len(label) > 512:
            raise ValueError("이름이 없거나 길이 한도를 초과했습니다.")
        if any(ord(char) < 32 or ord(char) == 127 for char in label):
            raise ValueError("이름에 제어문자를 사용할 수 없습니다.")
        return code, label
    parts = name.split(delimiter)
    code_index, name_index = rule["code_token"], rule["name_from_token"]
    code = name if not code_index else parts[code_index - 1] if code_index <= len(parts) else ""
    label = name if not name_index else delimiter.join(parts[name_index - 1:])
    code, label = code.strip(), label.strip()
    if not code or not label or len(code) > 256 or len(label) > 512:
        raise ValueError("번호·이름 토큰이 없거나 길이 한도를 초과했습니다.")
    if any(ord(char) < 32 or ord(char) == 127 for char in code + label):
        raise ValueError("번호·이름에 제어문자를 사용할 수 없습니다.")
    return code, label


def _default_options() -> dict:
    labels = {"PROJECT": "프로젝트", "REQUEST": "의뢰", "LOAD_CASE": "하중 경우", "RESULTS": "결과 폴더", "INPUT": "입력 폴더"}
    return {"roles": [{"key": key, "label": labels[key], "kind": key, "active": True} for key in (*ENTITY_KINDS, *METADATA_KINDS)],
            "analysis_types": [{"key": key, "label": key, "active": True} for key in ANALYSIS_TYPES]}


def build_plan(nodes: list[dict], rules: list[dict], root_key: str, registry: list[dict],
               legacy_paths: list[str], semantic_bindings: list[dict], options: dict | None = None) -> dict:
    options = options or _default_options()
    role_catalog = {item["key"]: item for item in options["roles"]}
    active_types = {item["key"] for item in options["analysis_types"] if item.get("active")}
    by_path = {(folded(row["relative_path"]), row["role"]): row for row in registry}
    by_code = {(row.get("role_kind", row["role"]), row["scope_key"], folded(row["code"])): row for row in registry if row.get("code")}
    contexts, proposed, proposed_paths = {}, {}, {}
    result = []
    for node in nodes:
        context = dict(contexts.get(node["parent_path"], {}))
        matches = [rule for rule in rules if rule["depth"] == node["depth"] and
                   folded((rule.get("keyword") if rule.get("keyword") is not None else rule.get("prefix", ""))) in folded(node["name"])]
        selected_by_kind: dict[str, list[tuple[dict, dict | None, str | None]]] = {}
        selected_rules: list[tuple[dict, dict | None, str | None]] = []
        for rule in matches:
            role = role_catalog.get(rule["role"])
            if not role:
                selected_rules.append((rule, None, "카탈로그에 없는 폴더 역할입니다."))
                continue
            selected_by_kind.setdefault(role["kind"], []).append((rule, role, None))
        for kind, entries in selected_by_kind.items():
            if len(entries) > 1:
                selected_rules.extend((rule, role, "같은 폴더에 같은 종류의 역할 규칙이 여러 개 일치합니다.") for rule, role, _ in entries)
            else:
                selected_rules.extend(entries)
        order = {"PROJECT": 0, "REQUEST": 1, "LOAD_CASE": 2, "RESULTS": 3, "INPUT": 4}
        selected_rules.sort(key=lambda item: (order.get(item[1]["kind"] if item[1] else "", 99), item[0]["role"]))
        for rule, role_option, selection_error in selected_rules:
            role = rule["role"]
            role_kind = role_option["kind"] if role_option else ""
            parent_kind = "PROJECT" if role_kind == "REQUEST" else "REQUEST" if role_kind == "LOAD_CASE" else "LOAD_CASE" if role_kind in METADATA_KINDS else None
            parent = context.get(parent_kind) if parent_kind else None
            scope = parent["target_id"] if parent else ""
            target_id = f"{role.lower()}-{uuid5(NAMESPACE_URL, root_key + ':' + folded(node['relative_path']) + ':' + role).hex}"
            row = {"relative_path": node["relative_path"], "role": role, "role_kind": role_kind, "role_label": role_option["label"] if role_option else role,
                   "code": "", "name": "", "analysis_type": rule["analysis_type"] if role_kind == "LOAD_CASE" else "",
                   "scope_key": scope, "parent_target_id": scope or None, "target_id": target_id,
                   "project_id": context.get("PROJECT", {}).get("target_id"), "request_id": context.get("REQUEST", {}).get("target_id"),
                   "load_case_id": context.get("LOAD_CASE", {}).get("target_id"),
                   "status": "CREATE", "message": "새 업무 항목 생성"}
            if role_kind == "PROJECT": row["project_id"] = target_id
            elif role_kind == "REQUEST": row["request_id"] = target_id
            elif role_kind == "LOAD_CASE": row["load_case_id"] = target_id
            try:
                if selection_error:
                    raise ValueError(selection_error)
                path_key = (folded(node["relative_path"]), role)
                if path_key in proposed_paths:
                    proposed_paths[path_key].update(status="CONFLICT", message="정규화한 폴더 경로가 다른 폴더와 중복됩니다.")
                    raise ValueError("정규화한 폴더 경로가 다른 폴더와 중복됩니다.")
                proposed_paths[path_key] = row
                row["code"], row["name"] = extract(node["name"], rule)
                if parent_kind and (not parent or parent["status"] == "CONFLICT"):
                    raise ValueError("상위 프로젝트 또는 의뢰가 없거나 충돌합니다.")
                if role_kind == "PROJECT" and context.get("PROJECT"):
                    raise ValueError("프로젝트 안의 중첩 프로젝트는 이번 생성 규칙에서 지원하지 않습니다.")
                key = (role_kind, scope, folded(row["code"])) if row["code"] and role_kind in ("PROJECT", "REQUEST") else None
                old = by_path.get((folded(node["relative_path"]), role))
                code_owner = by_code.get(key) if key else None
                if key and key in proposed:
                    previous = proposed[key]
                    previous.update(status="CONFLICT", message="같은 상위 업무의 번호가 다른 폴더와 중복됩니다.")
                    raise ValueError("같은 상위 업무의 번호가 다른 폴더와 중복됩니다.")
                if key:
                    proposed[key] = row
                if code_owner and code_owner is not old:
                    raise ValueError("같은 번호가 다른 폴더에 연결되어 있습니다. 이동·이름 변경을 확인하세요.")
                if old:
                    if any((old.get(field) or "") != (row.get(field) or "") for field in ("relative_path", "scope_key", "code", "name", "analysis_type")) or old.get("role_kind", old.get("role")) != role_kind:
                        raise ValueError("기존 폴더 연결과 규칙 결과가 다릅니다. 기존 업무는 변경하지 않습니다.")
                    if not old.get("target_valid", False):
                        raise ValueError("기존 업무 항목이 변경되었거나 사라졌습니다.")
                    row.update(target_id=old["target_id"], status="KEEP", message="확정된 기존 연결 유지")
                else:
                    if not role_option.get("active", False):
                        raise ValueError("비활성화된 폴더 역할은 새 업무에 사용할 수 없습니다.")
                    if role_kind == "LOAD_CASE" and row["analysis_type"] not in active_types:
                        raise ValueError("비활성화된 해석 종류는 새 업무에 사용할 수 없습니다.")
                    if any(overlaps(node["relative_path"], path) for path in legacy_paths):
                        raise ValueError("기존 SPDM 업무 생성 영역과 겹칩니다.")
                    project_id, request_id, load_case_id = row["project_id"], row["request_id"], row["load_case_id"]
                    for binding in semantic_bindings:
                        if overlaps(node["relative_path"], binding["relative_path"]):
                            if (binding["project_id"] != project_id or
                                (binding["request_id"] and binding["request_id"] != request_id) or
                                (binding["load_case_id"] and binding["load_case_id"] != load_case_id)):
                                raise ValueError("다른 업무에 연결된 결과 폴더와 겹칩니다.")
            except ValueError as error:
                row.update(status="CONFLICT", message=str(error))
            # Parent contexts are copied so intervening container directories inherit them.
            if role_kind in ENTITY_KINDS:
                context[role_kind] = row
            if role_kind == "PROJECT":
                context.pop("REQUEST", None)
                context.pop("LOAD_CASE", None)
            elif role_kind == "REQUEST":
                context.pop("LOAD_CASE", None)
            result.append(row)
        contexts[node["relative_path"]] = context
    # A duplicate can invalidate a parent that earlier descendants already used.
    invalid = {row["target_id"] for row in result if row["status"] == "CONFLICT"}
    for row in result:
        if row["parent_target_id"] in invalid:
            row.update(status="CONFLICT", message="상위 업무 충돌을 먼저 해결하세요.")
            invalid.add(row["target_id"])
    conflicts = sum(row["status"] == "CONFLICT" for row in result)
    return {"rows": result, "can_apply": bool(result) and not conflicts,
            "unmatched_count": len(nodes) - len({row["relative_path"] for row in result}),
            "summary": {"projects": sum(row["role_kind"] == "PROJECT" and row["status"] == "CREATE" for row in result),
                        "requests": sum(row["role_kind"] == "REQUEST" and row["status"] == "CREATE" for row in result),
                        "load_cases": sum(row["role_kind"] == "LOAD_CASE" and row["status"] == "CREATE" for row in result),
                        "folders": sum(row["role_kind"] in METADATA_KINDS and row["status"] == "CREATE" for row in result),
                        "conflicts": conflicts}}
