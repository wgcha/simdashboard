"""Folder Schema hierarchy projection shared by the Case results and materials catalogs.

Both screens select Simulation Case -> Load case -> Execution run -> Run option -> Scene
from the same confirmed Folder Schema. Keeping one projection guarantees that a URL
selection (case, case_load, case_run, case_option, scene) means the same folders in
both tabs.
"""
from __future__ import annotations

from typing import Any

from . import folder_discovery_environment


def _hierarchy_id(schema: dict[str, Any], root_key: str, role: str, context: dict[str, Any]) -> str:
    relative_path = str(context.get("relative_path") or "")
    if role == "RUN_OPTION" and relative_path:
        option_node = next((node for node in schema.get("nodes", [])
                            if str(node.get("role_kind") or "") == role
                            and str(node.get("relative_path") or "").casefold() == relative_path.casefold()), None)
        if option_node and option_node.get("run_option_id"):
            return str(option_node["run_option_id"])
    target_id = context.get("target_id")
    if target_id:
        return str(target_id)
    return folder_discovery_environment.stable(
        "folder-location-" + role.casefold(), root_key, relative_path, role,
    ) if relative_path else ""


def context_ids(schema: dict[str, Any], root_key: str, hierarchy: dict[str, Any]) -> dict[str, str]:
    """Case/Load/Run/Option ids for a location's Folder Schema hierarchy.

    An explicit Run option uses its option id; a Run without an option level
    uses the stable ``옵션 없음`` id so both catalogs agree.
    """
    case = hierarchy.get("simulation_case") or {}
    load_case = hierarchy.get("load_case") or {}
    execution_run = hierarchy.get("execution_run") or {}
    run_option = hierarchy.get("run_option") or {}
    load_case_id = _hierarchy_id(schema, root_key, "LOAD_CASE", load_case)
    execution_run_id = _hierarchy_id(schema, root_key, "EXECUTION_RUN", execution_run)
    run_option_id = str(run_option.get("target_id") or "")
    if isinstance(hierarchy.get("run_option"), dict):
        run_option_id = _hierarchy_id(schema, root_key, "RUN_OPTION", run_option)
    elif execution_run_id:
        run_option_id = folder_discovery_environment.stable(
            "folder-option-absent", root_key,
            str(execution_run.get("relative_path") or ""), "ABSENT",
        )
    return {"case_id": str(case.get("target_id") or ""), "load_case_id": load_case_id,
            "execution_run_id": execution_run_id, "run_option_id": run_option_id}


def project_schema_hierarchy(schema: dict[str, Any], locations: Any, root_key: str) -> dict[str, Any]:
    """Return schema-only choices (capture_id=None) and Scene locations.

    ``cases`` is keyed by folded Case relative path so callers can attach captures.
    """
    schema_cases: dict[str, dict[str, Any]] = {}
    result: dict[str, Any] = {"load_cases": [], "execution_runs": [], "run_options": [], "scenes": []}
    def hierarchy_id(role: str, context: dict[str, Any]) -> str:
        return _hierarchy_id(schema, root_key, role, context)

    role_keys = {"SIMULATION_CASE": "cases", "LOAD_CASE": "load_cases",
                 "EXECUTION_RUN": "execution_runs", "RUN_OPTION": "run_options"}
    for node in schema.get("nodes", []):
        role = str(node.get("role_kind") or "")
        key = role_keys.get(role)
        if not key or node.get("status") not in {"CONFIRMED", "LINKED"}:
            continue
        hierarchy = node.get("hierarchy") or {}
        case = hierarchy.get("simulation_case") or (node if role == "SIMULATION_CASE" else {})
        case_path = str(case.get("relative_path") or "")
        case_id = str(case.get("target_id") or "")
        if not case_path or not case_id:
            continue
        if role == "SIMULATION_CASE":
            schema_cases[case_path.casefold()] = {
                "id": case_id, "label": str(node.get("name") or "Case"),
                "relative_path": case_path, "source": "FOLDER_SCHEMA",
                "match_status": "UNCAPTURED", "dashboard_case_id": None,
                "capture_count": 0,
            }
            continue
        choice_id = str(node.get("run_option_id") or node.get("target_id") or "")
        if not choice_id:
            choice_id = folder_discovery_environment.stable(
                "folder-location-" + role.casefold(), root_key,
                str(node.get("relative_path") or ""), role,
            )
        if not choice_id:
            continue
        choice = {"id": choice_id, "label": str(node.get("name") or role),
                  "case_id": case_id, "relative_path": str(node.get("relative_path") or ""),
                  "capture_id": None, "match_status": "UNCAPTURED"}
        if role == "LOAD_CASE":
            result["load_cases"].append(choice)
        elif role == "EXECUTION_RUN":
            load = hierarchy.get("load_case") or {}
            if load.get("target_id"):
                choice["load_case_id"] = str(load["target_id"])
                result["execution_runs"].append(choice)
        else:
            run = hierarchy.get("execution_run") or {}
            if run.get("target_id"):
                choice["execution_run_id"] = str(run["target_id"])
                choice["run_option_id"] = choice_id
                choice["option_label"] = choice["label"]
                choice["option_status"] = node.get("option_status") or "PRESENT"
                choice["mode"] = choice["label"].upper()
                result["run_options"].append(choice)
    absent_scene_runs = set()
    for scene in locations.locations:
        hierarchy = scene.get("hierarchy") or {}
        run = hierarchy.get("execution_run") or {}
        if run.get("target_id") and not isinstance(hierarchy.get("run_option"), dict):
            absent_scene_runs.add(str(run["target_id"]))
    for run_choice in [item for item in result["execution_runs"] if item.get("capture_id") is None]:
        run_choice_id = str(run_choice.get("id") or "")
        if run_choice_id in absent_scene_runs:
            option_id = folder_discovery_environment.stable(
                "folder-option-absent", root_key, str(run_choice.get("relative_path") or ""), "ABSENT",
            )
            result["run_options"].append({
                "id": option_id, "label": "옵션 없음", "case_id": run_choice.get("case_id"),
                "execution_run_id": run_choice_id, "run_option_id": option_id,
                "option_label": None, "option_status": "ABSENT", "mode": None,
                "capture_id": None, "match_status": "UNCAPTURED",
            })
    for location in locations.locations:
        if location.get("role_kind") != "SCENE":
            continue
        hierarchy = location.get("hierarchy") or {}
        case = hierarchy.get("simulation_case") or {}
        if not case.get("target_id"):
            continue
        ids = context_ids(schema, root_key, hierarchy)
        result["scenes"].append({
            "id": str(location.get("scene_id") or location["id"]),
            "label": str(location.get("label") or location.get("name") or "Scene"),
            **ids,
            "relative_path": str(location.get("relative_path") or ""),
            "capture_id": None, "match_status": "UNCAPTURED",
            "source": "FOLDER_SCHEMA",
        })
    result["cases"] = schema_cases
    return result
