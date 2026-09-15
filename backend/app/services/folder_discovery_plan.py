"""Pure proposal construction; identifiers never bind to similarly named demo data."""
from __future__ import annotations

import unicodedata
from uuid import NAMESPACE_URL, uuid5

ROLES = ("PROJECT", "REQUEST", "LOAD_CASE")
ANALYSIS_TYPES = ("DROP", "SIDE_CLAMP", "SPDM_CMS", "SPDM_MODAL", "SPDM_DEFLECTION", "SPDM_STIFFNESS", "SPDM_VIBRATION")


def folded(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def overlaps(left: str, right: str) -> bool:
    left, right = folded(left), folded(right)
    return not left or not right or left == right or left.startswith(right + "/") or right.startswith(left + "/")


def extract(name: str, rule: dict) -> tuple[str, str]:
    parts = name.split(rule["delimiter"])
    code_index, name_index = rule["code_token"], rule["name_from_token"]
    code = name if not code_index else parts[code_index - 1] if code_index <= len(parts) else ""
    label = name if not name_index else rule["delimiter"].join(parts[name_index - 1:])
    code, label = code.strip(), label.strip()
    if not code or not label or len(code) > 256 or len(label) > 512:
        raise ValueError("번호·이름 토큰이 없거나 길이 한도를 초과했습니다.")
    if any(ord(char) < 32 or ord(char) == 127 for char in code + label):
        raise ValueError("번호·이름에 제어문자를 사용할 수 없습니다.")
    return code, label


def build_plan(nodes: list[dict], rules: list[dict], root_key: str, registry: list[dict],
               legacy_paths: list[str], semantic_bindings: list[dict]) -> dict:
    by_path = {(folded(row["relative_path"]), row["role"]): row for row in registry}
    by_code = {(row["role"], row["scope_key"], folded(row["code"])): row for row in registry}
    contexts, proposed, proposed_paths = {}, {}, {}
    result = []
    for node in nodes:
        context = dict(contexts.get(node["parent_path"], {}))
        matches = [rule for rule in rules if rule["depth"] == node["depth"] and node["name"].startswith(rule["prefix"])]
        for role in ROLES:
            selected = [rule for rule in matches if rule["role"] == role]
            if not selected:
                continue
            rule = selected[0]
            parent_role = "PROJECT" if role == "REQUEST" else "REQUEST" if role == "LOAD_CASE" else None
            parent = context.get(parent_role) if parent_role else None
            scope = parent["target_id"] if parent else ""
            target_id = f"{role.lower()}-{uuid5(NAMESPACE_URL, root_key + ':' + folded(node['relative_path']) + ':' + role).hex}"
            row = {"relative_path": node["relative_path"], "role": role, "code": "", "name": "",
                   "analysis_type": rule["analysis_type"] if role == "LOAD_CASE" else "",
                   "scope_key": scope, "parent_target_id": scope or None, "target_id": target_id,
                   "status": "CREATE", "message": "새 업무 항목 생성"}
            try:
                path_key = (folded(node["relative_path"]), role)
                if path_key in proposed_paths:
                    proposed_paths[path_key].update(status="CONFLICT", message="정규화한 폴더 경로가 다른 폴더와 중복됩니다.")
                    raise ValueError("정규화한 폴더 경로가 다른 폴더와 중복됩니다.")
                proposed_paths[path_key] = row
                row["code"], row["name"] = extract(node["name"], rule)
                if len(selected) != 1:
                    raise ValueError("같은 폴더 역할에 여러 규칙이 일치합니다.")
                if parent_role and (not parent or parent["status"] == "CONFLICT"):
                    raise ValueError("상위 프로젝트 또는 의뢰가 없거나 충돌합니다.")
                if role == "PROJECT" and context.get("PROJECT"):
                    raise ValueError("프로젝트 안의 중첩 프로젝트는 이번 생성 규칙에서 지원하지 않습니다.")
                if role == "LOAD_CASE" and row["analysis_type"] not in ANALYSIS_TYPES:
                    raise ValueError("하중 경우의 해석 종류를 지정하세요.")
                key = (role, scope, folded(row["code"]))
                old = by_path.get((folded(node["relative_path"]), role))
                code_owner = by_code.get(key)
                if key in proposed:
                    previous = proposed[key]
                    previous.update(status="CONFLICT", message="같은 상위 업무의 번호가 다른 폴더와 중복됩니다.")
                    raise ValueError("같은 상위 업무의 번호가 다른 폴더와 중복됩니다.")
                proposed[key] = row
                if code_owner and code_owner is not old:
                    raise ValueError("같은 번호가 다른 폴더에 연결되어 있습니다. 이동·이름 변경을 확인하세요.")
                if old:
                    if any(old.get(field) != row.get(field) for field in ("relative_path", "scope_key", "code", "name", "analysis_type")):
                        raise ValueError("기존 폴더 연결과 규칙 결과가 다릅니다. 기존 업무는 변경하지 않습니다.")
                    if not old.get("target_valid", False):
                        raise ValueError("기존 업무 항목이 변경되었거나 사라졌습니다.")
                    row.update(target_id=old["target_id"], status="KEEP", message="확정된 기존 연결 유지")
                else:
                    if any(overlaps(node["relative_path"], path) for path in legacy_paths):
                        raise ValueError("기존 SPDM 업무 생성 영역과 겹칩니다.")
                    project_id = row["target_id"] if role == "PROJECT" else context.get("PROJECT", {}).get("target_id")
                    request_id = row["target_id"] if role == "REQUEST" else context.get("REQUEST", {}).get("target_id")
                    load_case_id = row["target_id"] if role == "LOAD_CASE" else None
                    for binding in semantic_bindings:
                        if overlaps(node["relative_path"], binding["relative_path"]):
                            if (binding["project_id"] != project_id or
                                (binding["request_id"] and binding["request_id"] != request_id) or
                                (binding["load_case_id"] and binding["load_case_id"] != load_case_id)):
                                raise ValueError("다른 업무에 연결된 결과 폴더와 겹칩니다.")
            except ValueError as error:
                row.update(status="CONFLICT", message=str(error))
            # Parent contexts are copied so intervening container directories inherit them.
            context[role] = row
            if role == "PROJECT":
                context.pop("REQUEST", None)
                context.pop("LOAD_CASE", None)
            elif role == "REQUEST":
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
            "summary": {"projects": sum(row["role"] == "PROJECT" and row["status"] == "CREATE" for row in result),
                        "requests": sum(row["role"] == "REQUEST" and row["status"] == "CREATE" for row in result),
                        "load_cases": sum(row["role"] == "LOAD_CASE" and row["status"] == "CREATE" for row in result),
                        "conflicts": conflicts}}
