"""Folder name warnings for the Case results screen (W6, case-results-workflow-redesign §8.4).

Users copy Scene folders into the SPDM share by hand, so a naming slip
(`4_Edge` next to `4_edge2`, `6_Corner` in one Case and `6_corner` in another)
silently splits results or breaks Case comparison. This module only reads the
Folder Schema snapshot the catalog already resolved; it never scans the share
and never changes folders.

Each warning is ``{kind, severity, message, paths, case_id?, run_option_id?}``.
Messages are Korean and never contain internal codes.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from . import folder_schema_hierarchy

_SEPARATORS = re.compile(r"[\s_\-]+")
_TRAILING_DIGITS = re.compile(r"\d+$")
_LEADING_NUMBER = re.compile(r"^\d+")
MAX_WARNINGS = 50
MAX_PATHS = 20
MAX_GROUP = 300

# Depth-schema deviation codes (docs/contracts/depth-schema.md §5) in plain Korean.
DEVIATION_TEXT = {
    "ENV_KEYWORD_BOTH": "의뢰 폴더 이름에 '사용'과 '유통'이 모두 있어 환경을 정할 수 없습니다.",
    "ENV_KEYWORD_NONE": "의뢰 폴더 이름에 '사용' 또는 '유통'이 없어 환경을 정할 수 없습니다.",
    "WORKING_MISSING": "의뢰 폴더 아래에 Working 폴더가 없습니다.",
    "UNEXPECTED_REQUEST_CHILD": "의뢰 폴더 바로 아래에 Working·Final 이외의 폴더가 있습니다.",
    "UNEXPECTED_FINAL_CHILD": "Final 폴더 바로 아래에 CAE·Report·CAD 이외의 폴더가 있습니다.",
    "FINAL_VERSION_INVALID": "Final Case 아래 폴더 이름이 Final 지정 형식이 아닙니다.",
}


def _fold(name: str) -> str:
    return _SEPARATORS.sub("", name).casefold()


def _plain(name: str) -> str:
    return _SEPARATORS.sub("", name)


def _letter_edit(a: str, b: str) -> bool:
    """One letter inserted/removed/replaced, or two adjacent letters swapped.

    Digit edits are ignored on purpose: ``1_Face`` and ``2_Face`` are different
    Scenes, not a spelling slip.
    """
    if a == b or abs(len(a) - len(b)) > 1 or min(len(a), len(b)) < 5:
        return False
    if len(a) == len(b):
        diff = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        if len(diff) == 1:
            i = diff[0]
            return a[i].isalpha() and b[i].isalpha()
        if len(diff) == 2 and diff[1] == diff[0] + 1:
            i = diff[0]
            return a[i] == b[i + 1] and a[i + 1] == b[i] and a[i].isalpha() and b[i].isalpha()
        return False
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    for i in range(len(long_)):
        if long_[:i] + long_[i + 1:] == short:
            return long_[i].isalpha()
    return False


def _similar(a: str, b: str) -> tuple[str, str] | None:
    """(kind, severity) when two sibling names look like the same Scene."""
    if a == b:
        return None
    fa, fb = _fold(a), _fold(b)
    if fa == fb:
        return "SCENE_NAME_CASE", "warning"
    for short, long_ in ((a, b), (b, a)):
        fs, fl = _fold(short), _fold(long_)
        if fs and _TRAILING_DIGITS.search(fl) and not _TRAILING_DIGITS.search(fs) \
                and _TRAILING_DIGITS.sub("", fl) == fs:
            # `4_Edge` / `4_Edge2` may be intentional; `4_Edge` / `4_edge2` is a slip.
            exact = _TRAILING_DIGITS.sub("", _plain(long_)) == _plain(short)
            return "SCENE_NAME_SUFFIX", "info" if exact else "warning"
    if _letter_edit(fa, fb):
        return "SCENE_NAME_SPELLING", "warning"
    return None


def _clusters(names: Iterable[str]) -> list[tuple[str, str, list[str]]]:
    """Group near-identical names; returns (kind, severity, names) per group."""
    names = sorted(set(names), key=lambda item: (item.casefold(), item))[:MAX_GROUP]
    parent = {name: name for name in names}
    links: dict[tuple[str, str], tuple[str, str]] = {}

    def root(name: str) -> str:
        while parent[name] != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name

    for index, first in enumerate(names):
        for second in names[index + 1:]:
            found = _similar(first, second)
            if found:
                links[(first, second)] = found
                parent[root(second)] = root(first)
    groups: dict[str, list[str]] = {}
    for name in names:
        groups.setdefault(root(name), []).append(name)
    rank = {"SCENE_NAME_CASE": 0, "SCENE_NAME_SPELLING": 1, "SCENE_NAME_SUFFIX": 2}
    result = []
    for members in groups.values():
        if len(members) < 2:
            continue
        found = [value for pair, value in links.items() if pair[0] in members]
        kind = min((item[0] for item in found), key=rank.__getitem__)
        severity = "warning" if any(item[1] == "warning" for item in found) else "info"
        result.append((kind, severity, members))
    return result


_KIND_TEXT = {
    "SCENE_NAME_CASE": "대소문자나 구분 기호(_ - 공백)만 다릅니다",
    "SCENE_NAME_SPELLING": "철자가 한두 글자만 다릅니다",
    "SCENE_NAME_SUFFIX": "뒤에 번호만 덧붙은 이름입니다",
}


def _quoted(names: Iterable[str]) -> str:
    return ", ".join(f"'{name}'" for name in names)


def folder_name_warnings(schema: dict[str, Any], locations: Any, root_key: str,
                         environment: str) -> list[dict[str, Any]]:
    """Name warnings for one request/environment from the resolved snapshot."""
    environment = str(environment).upper()
    scenes = [item for item in locations.locations if item.get("role_kind") == "SCENE"]
    warnings: list[dict[str, Any]] = []
    parent_label = "Run Option" if environment == "DISTRIBUTION" else "Case"

    # 1. Siblings under one Run Option (DISTRIBUTION) or Case (USAGE).
    by_parent: dict[str, list[dict[str, Any]]] = {}
    for scene in scenes:
        by_parent.setdefault(str(scene.get("parent_path") or "").casefold(), []).append(scene)
    for siblings in by_parent.values():
        by_name = {str(item.get("name") or ""): item for item in siblings}
        for kind, severity, members in _clusters(by_name):
            ids = folder_schema_hierarchy.context_ids(schema, root_key, by_name[members[0]].get("hierarchy") or {})
            warnings.append({
                "kind": kind, "severity": severity,
                "message": f"같은 {parent_label} 아래 Scene 이름 {_quoted(members)}이(가) {_KIND_TEXT[kind]}. "
                           "결과가 서로 다른 Scene으로 나뉩니다.",
                "paths": [str(by_name[name].get("relative_path") or "") for name in members][:MAX_PATHS],
                "case_id": ids["case_id"] or None,
                "run_option_id": (ids["run_option_id"] or None) if environment == "DISTRIBUTION" else None,
            })

    # 2. Across Cases: the same Scene position spelled differently.
    case_labels: dict[str, str] = {}
    positions: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for scene in scenes:
        hierarchy = scene.get("hierarchy") or {}
        case = hierarchy.get("simulation_case") or {}
        case_path = str(case.get("relative_path") or "")
        case_id = str(case.get("target_id") or "")
        parent_path = str(scene.get("parent_path") or "")
        if not case_id or not case_path or not parent_path.casefold().startswith(case_path.casefold()):
            continue
        case_labels[case_id] = str(case.get("name") or case_path.rsplit("/", 1)[-1])
        position = "/".join(_fold(part) for part in parent_path[len(case_path):].strip("/").split("/") if part)
        positions.setdefault(position, {}).setdefault(case_id, []).append(scene)
    for by_case in positions.values():
        if len(by_case) < 2:
            continue
        name_cases: dict[str, set[str]] = {}
        for case_id, items in by_case.items():
            for item in items:
                name_cases.setdefault(str(item.get("name") or ""), set()).add(case_id)
        for kind, severity, members in _clusters(name_cases):
            # Only a mismatch between Cases: every Case already spelling it the same way is §1's job.
            owners = [name_cases[name] for name in members]
            if all(owners[0] == other for other in owners[1:]):
                continue
            all_cases = set().union(*owners)
            if not any(len(all_cases - cases) for cases in owners):
                continue
            paths = [str(item.get("relative_path") or "") for case_id in sorted(all_cases)
                     for item in by_case[case_id] if str(item.get("name") or "") in members]
            detail = ", ".join(f"'{name}'({'·'.join(sorted(case_labels.get(c, 'Case') for c in name_cases[name]))})"
                               for name in members)
            warnings.append({
                "kind": "CASE_SCENE_MISMATCH", "severity": "warning",
                "message": f"Case마다 Scene 이름이 다르게 적혀 있습니다: {detail}. "
                           "Case 비교에서 같은 Scene으로 맞춰지지 않습니다.",
                "paths": paths[:MAX_PATHS], "case_id": None, "run_option_id": None,
            })

    # 3. Unexpected folders: snapshot deviations, unresolved nodes, and Scene-level
    #    folders that do not follow the sibling Scene number pattern.
    unexpected: list[dict[str, Any]] = []
    for node in schema.get("nodes", []):
        path = str(node.get("relative_path") or "")
        if not path or node.get("status") == "EXCLUDED":
            continue
        deviation = node.get("deviation") if isinstance(node.get("deviation"), dict) else None
        code = str((deviation or {}).get("code") or "")
        if code:
            text = DEVIATION_TEXT.get(code, "폴더 구조 규칙과 다른 폴더입니다.")
        elif node.get("status") == "UNRESOLVED" or node.get("role_basis") == "CONFLICT":
            text = "폴더 규칙으로 역할을 정할 수 없는 폴더입니다. 결과 화면에 표시되지 않습니다."
        else:
            continue
        unexpected.append({"kind": "UNEXPECTED_FOLDER", "severity": "warning",
                           "message": f"예상 밖 폴더: {text}", "paths": [path],
                           "case_id": None, "run_option_id": None})
    for siblings in by_parent.values():
        numbered = [item for item in siblings if _LEADING_NUMBER.match(str(item.get("name") or ""))]
        odd = [item for item in siblings if not _LEADING_NUMBER.match(str(item.get("name") or ""))]
        if not numbered or not odd:
            continue
        ids = folder_schema_hierarchy.context_ids(schema, root_key, odd[0].get("hierarchy") or {})
        unexpected.append({
            "kind": "UNEXPECTED_FOLDER", "severity": "info",
            "message": f"{parent_label} 아래 {_quoted(str(item.get('name') or '') for item in odd)} 폴더는 "
                       "다른 Scene과 달리 번호로 시작하지 않습니다. Scene이 아니면 다른 곳으로 옮기세요.",
            "paths": [str(item.get("relative_path") or "") for item in odd][:MAX_PATHS],
            "case_id": ids["case_id"] or None,
            "run_option_id": (ids["run_option_id"] or None) if environment == "DISTRIBUTION" else None,
        })
    # Merge identical messages (e.g. several unresolved folders) into one item.
    merged: dict[str, dict[str, Any]] = {}
    for item in unexpected:
        key = item["message"] + "|" + str(item.get("case_id")) + "|" + str(item.get("run_option_id"))
        if key in merged:
            merged[key]["paths"] = (merged[key]["paths"] + item["paths"])[:MAX_PATHS]
        else:
            merged[key] = item
    warnings.extend(merged.values())
    order = {"warning": 0, "info": 1}
    warnings.sort(key=lambda item: order[item["severity"]])
    return warnings[:MAX_WARNINGS]
