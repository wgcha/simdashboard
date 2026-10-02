"""Project-scoped projections of immutable result captures, never live files."""
from __future__ import annotations

import math
import hashlib
from typing import Any

from .dashboard_capture import DashboardCaptureError, _decode, get_capture

EDGES = {"LEFT": "LH", "RIGHT": "RH", "TOP": "TOP", "BOTTOM": "BOT"}
USAGE_KEYS = {
    "Settle": ("Settle", "Set Tilt Angle @ Settle (deg)", "deg"),
    "Wobble": ("Wobble", "Wobble Disp. (mm)", "mm"),
    "Horizontal_Force_Angle": ("Horizontal_Force_Angle", "Set Tilt Angle Difference (deg)", "deg"),
    "Slope_Angle": ("Slope_Angle", "Slope Angle (deg)", "deg"),
    "Slope_Angle_360": ("Slope_Angle_360", None, None),
}


def fail(message: str):
    raise DashboardCaptureError("DASHBOARD_CONTEXT_INVALID", message)


def context_key(context):
    return "|".join(str(context.get(k) or "") for k in (
        "project_id", "request_id", "simulation_case_id", "load_case_id",
        "execution_run_id", "run_option_id", "mode", "capture_id", "component_id", "basis", "scene_id"))


def number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def option_projection(run, capture_id):
    status = run.get("option_status") or ("UNRESOLVED" if run.get("mode") == "UNKNOWN" else "PRESENT")
    label = run.get("option_label")
    if status == "UNRESOLVED" and not label:
        label = "미확인(기존 자료)"
    option_id = run.get("run_option_id") or "legacy-option-" + hashlib.sha256(f"{capture_id}:{run['id']}:{run['mode']}".encode()).hexdigest()[:24]
    return option_id, label, status


def catalog(conn, request_id, environment, project_id=None):
    from . import folder_discovery, folder_discovery_environment, folder_schema_resolver

    result = {"contract_version": 1, "environment": environment, "cases": [], "captures": [],
              "load_cases": [], "execution_runs": [], "run_options": [], "modes": [], "components": [],
              "scenes": [], "final_history": [],
              "folder_schema": {"status": "UNAVAILABLE", "diagnostic": None, "snapshot_id": None},
              "bases": [{"id": "REPORTED_SUMMARY", "label": "원본 요약"}, {"id": "DETAIL", "label": "상세 추출값"}]}
    records = conn.execute("""SELECT dc.id,dc.source_name,dc.storage_root_id,dc.relative_path,c.id,c.created_at,c.payload_json
        FROM dashboard_cases dc LEFT JOIN dashboard_captures c ON c.case_id=dc.id
        WHERE dc.request_id=? AND dc.environment=? ORDER BY dc.source_name,c.created_at DESC""",
        [request_id, environment]).fetchall()
    schema_cases: dict[str, dict[str, Any]] = {}
    current_storage_root_id = None
    try:
        request_row = conn.execute("SELECT project_id FROM analysis_requests WHERE id=?", [request_id]).fetchone()
        if not request_row:
            raise folder_schema_resolver.FolderSchemaError(
                "FOLDER_SCHEMA_REQUEST_INVALID", "의뢰 문맥을 확인할 수 없습니다.", 404,
            )
        request_project_id = str(request_row[0])
        if project_id is not None and str(project_id) != request_project_id:
            raise folder_schema_resolver.FolderSchemaError(
                "FOLDER_SCHEMA_SCOPE_MISMATCH", "프로젝트와 의뢰 문맥이 일치하지 않습니다.",
            )
        project_id = request_project_id
        if not project_id:
            raise folder_schema_resolver.FolderSchemaError(
                "FOLDER_SCHEMA_PROJECT_REQUIRED", "프로젝트 문맥이 없어 현재 폴더 스키마를 읽을 수 없습니다."
            )
        root = folder_discovery.configured_root(conn)
        root_key = folder_discovery_environment.root_identity(root)
        from .dashboard_capture import _root_id
        current_storage_root_id = _root_id(root)
        locations = folder_schema_resolver.resolve_request_locations(
            conn, str(project_id), str(request_id), str(environment),
        )
        schema = locations.schema
        result["folder_schema"] = {
            "status": "AVAILABLE", "diagnostic": None,
            "snapshot_id": (schema.get("scan") or {}).get("id"),
        }

        def hierarchy_id(role: str, context: dict[str, Any]) -> str:
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
            load_case = hierarchy.get("load_case") or {}
            execution_run = hierarchy.get("execution_run") or {}
            run_option = hierarchy.get("run_option") or {}
            load_case_id = hierarchy_id("LOAD_CASE", load_case)
            execution_run_id = hierarchy_id("EXECUTION_RUN", execution_run)
            run_option_id = str(run_option.get("target_id") or "")
            if isinstance(hierarchy.get("run_option"), dict):
                run_option_id = hierarchy_id("RUN_OPTION", run_option)
            elif execution_run_id:
                run_option_id = folder_discovery_environment.stable(
                    "folder-option-absent", root_key,
                    str(execution_run.get("relative_path") or ""), "ABSENT",
                )
            result["scenes"].append({
                "id": str(location.get("scene_id") or location["id"]),
                "label": str(location.get("label") or location.get("name") or "Scene"),
                "case_id": str(case["target_id"]),
                "load_case_id": load_case_id,
                "execution_run_id": execution_run_id,
                "run_option_id": run_option_id,
                "relative_path": str(location.get("relative_path") or ""),
                "capture_id": None, "match_status": "UNCAPTURED",
                "source": "FOLDER_SCHEMA",
            })
    except folder_schema_resolver.FolderSchemaError as exc:
        result["folder_schema"] = {"status": "UNAVAILABLE",
                                    "diagnostic": {"code": exc.code, "message": str(exc)},
                                    "snapshot_id": None}
    except (OSError, folder_discovery.spdm_storage.SpdmStorageError):
        result["folder_schema"] = {
            "status": "UNAVAILABLE",
            "diagnostic": {"code": "FOLDER_SCHEMA_STORAGE_UNAVAILABLE",
                           "message": "현재 Folder Schema 저장소를 확인할 수 없습니다."},
            "snapshot_id": None,
        }

    cases_by_path = {key: value for key, value in schema_cases.items()}
    dashboard_case_ids: dict[str, str] = {}
    historical_cases: dict[str, dict[str, Any]] = {}
    final_case_ids: set[str] = set()
    for case_id, name, storage_root_id, relative_path, _capture_id, _created_at, _raw in records:
        folded = str(relative_path).casefold()
        case = cases_by_path.get(folded) if current_storage_root_id and str(storage_root_id) == current_storage_root_id else None
        if result["folder_schema"]["status"] == "AVAILABLE":
            from .folder_schema_locations import is_final_branch
            if is_final_branch(schema, str(relative_path)):
                final_case_ids.add(str(case_id))
                case = {"id": str(case_id), "label": str(name), "relative_path": str(relative_path),
                        "source": "FINAL_HISTORY", "match_status": "FINAL_HISTORY",
                        "dashboard_case_id": str(case_id), "capture_count": 0, "captures": []}
                result["final_history"].append(case)
                dashboard_case_ids[str(case_id)] = str(case_id)
                continue
        if case is None:
            case = historical_cases.get(str(case_id))
            if case is None:
                case = {"id": str(case_id), "label": str(name), "relative_path": str(relative_path),
                        "source": "HISTORY", "match_status": "HISTORY_ONLY",
                        "dashboard_case_id": str(case_id), "capture_count": 0}
                historical_cases[str(case_id)] = case
                result["cases"].append(case)
            dashboard_case_ids[str(case_id)] = str(case_id)
        else:
            case["dashboard_case_id"] = str(case_id)
            dashboard_case_ids[str(case_id)] = str(case["id"])
    for case in schema_cases.values():
        result["cases"].append(case)

    case_choices = {str(item["id"]): item for item in result["cases"]}
    for case_id, name, storage_root_id, relative_path, capture_id, created_at, raw in records:
        if str(case_id) in final_case_ids:
            case = next(item for item in result["final_history"] if item["id"] == str(case_id))
            if capture_id:
                case["capture_count"] += 1
                case["captures"].append({"id": str(capture_id), "label": str(created_at),
                                         "dashboard_case_id": str(case_id), "match_status": "FINAL_HISTORY"})
            continue
        canonical_case_id = dashboard_case_ids.get(str(case_id), str(case_id))
        if not capture_id:
            continue
        parent = {"case_id": canonical_case_id, "simulation_case_id": canonical_case_id,
                  "capture_id": str(capture_id)}
        case_choice = case_choices.get(canonical_case_id)
        if case_choice is not None:
            case_choice["capture_count"] = int(case_choice.get("capture_count") or 0) + 1
            case_choice["match_status"] = "CAPTURED" if case_choice.get("source") == "FOLDER_SCHEMA" else "HISTORY_ONLY"
        result["captures"].append({"id": str(capture_id), "label": str(created_at),
                                   "dashboard_case_id": str(case_id),
                                   "match_status": "MATCHED_TO_SCHEMA" if case_choice and case_choice.get("source") == "FOLDER_SCHEMA" else "HISTORY_ONLY",
                                   **parent})
        payload = _decode(raw)
        for run in payload.get("runs", []):
            option_id, option_label, option_status = option_projection(run, capture_id)
            scope = {**parent, "load_case_id": run["load_case_id"], "execution_run_id": run["id"], "run_option_id": option_id, "option_status": option_status, "option_label": option_label, "mode": run["mode"], "match_status": "CAPTURED"}
            result["load_cases"].append({"id": run["load_case_id"], "label": run.get("load_case_name", "하중경우"), **scope})
            result["execution_runs"].append({"id": run["id"], "label": run["source_name"], **scope})
            result["run_options"].append({"id": option_id, "label": option_label or "옵션 없음", **scope})
            result["modes"].append({"id": run["mode"], "label": run["mode"], **scope})
            for scene in run.get("scenes", []):
                result["scenes"].append({
                    "id": str(scene.get("id") or ""),
                    "label": str(scene.get("source_name") or scene.get("id") or "Scene"),
                    **scope,
                })
            components = sorted({o["component_id"] for s in run["scenes"] for o in s.get("observations", []) if o.get("component_id")}
                                | {m["component_id"] for s in run["scenes"] for m in s.get("media", []) if m.get("component_id")})
            result["components"].extend({"id": c, "label": c, **scope} for c in components)
    for key in ("load_cases", "execution_runs", "run_options", "modes", "components", "scenes"):
        unique = {}
        for item in result[key]:
            unique[tuple(sorted((key, str(value)) for key, value in item.items()))] = item
        result[key] = list(unique.values())
    return result


def usage_metric(entry, key, source_status, *, verdict=False):
    """Resolve exact key segments without mutating historical capture payloads."""
    if key is None:
        return None, "NOT_APPLICABLE", None
    path = (entry or {}).get("metric_paths", {}).get(key, [key])
    label = " / ".join(str(segment) for segment in path)
    override = (entry or {}).get("metric_statuses", {}).get(key)
    if override and override != "READY":
        return None, override, label
    if source_status != "READY":
        return None, source_status, label
    value = (entry or {}).get("values", {})
    for segment in path:
        if not isinstance(value, dict) or segment not in value:
            return None, "MISSING_FIELD", label
        value = value[segment]
    if value is None:
        return None, "NULL_VALUE", label
    if verdict:
        valid = isinstance(value, str) and value in {"OK", "NG"}
        return (value, "READY", label) if valid else (None, "INVALID_TYPE", label)
    # Reviewed JSON is strict; preserve the historical numeric-string contract.
    if (entry or {}).get("metric_paths") and not isinstance(value, (int, float)):
        return None, "INVALID_TYPE", label
    numeric = number(value)
    return numeric, "READY" if numeric is not None else "INVALID_TYPE", label


def usage(capture, case_id):
    if capture["environment"] != "USAGE" or capture["case_id"] != case_id:
        fail("선택 Case와 수집 버전이 일치하지 않습니다.")
    payload = capture["payload"]
    context = {**payload["context"], "simulation_case_id": case_id, "capture_id": capture["id"]}
    context["context_key"] = context_key(context)
    rows, issues = [], set()
    issues.update(payload.get("quality_issues", []))
    for evaluation, (label, key, unit) in USAGE_KEYS.items():
        entries = [e for e in payload.get("evaluations", []) if e.get("evaluation") == evaluation]
        row = {"id": evaluation, "name": label, "status": "READY", "common": None, "front": None, "rear": None, "media": []}
        for direction in (["common"] if evaluation == "Settle" else ["front", "back"]):
            candidates = [e for e in entries if e.get("direction", "common") == direction and not e.get("media_only")]
            entry = candidates[0] if len(candidates) == 1 else None
            status = entry.get("status", "MISSING") if entry else ("AMBIGUOUS" if candidates else "MISSING")
            value, value_status, value_key = usage_metric(entry, key, status)
            verdict, verdict_status, verdict_key = usage_metric(
                entry, "OK/NG" if evaluation in {"Slope_Angle", "Slope_Angle_360"} else None, status, verdict=True)
            field_errors = [s for s in (value_status, verdict_status) if s not in {"READY", "NOT_APPLICABLE"}]
            if status == "READY" and field_errors:
                status = field_errors[0]
            if status != "READY":
                issues.add(status)
                row["status"] = "PARTIAL"
            cell = {"value": value, "unit": unit, "status": status, "verdict": verdict,
                    "value_status": value_status, "verdict_status": verdict_status,
                    "value_key": value_key, "verdict_key": verdict_key,
                    "source": entry.get("source") if entry else None, "condition": entry.get("condition") if entry else None}
            row["rear" if direction == "back" else direction] = cell
            row["media"].extend(entry.get("media", []) if entry else [])
        row["media"].extend(asset for entry in entries if entry.get("media_only") for asset in entry.get("media", []))
        rows.append(row)
    return {"contract_version": 1, "context": context, "status": "PARTIAL" if issues else "READY", "evaluations": rows, "quality_issues": sorted(issues)}


def usage_reference(result, reference):
    """Only comparable source conditions/units may be placed alongside a value."""
    for row, other in zip(result["evaluations"], reference["evaluations"]):
        comparison = {"status": "READY", "reason": None, "context": reference["context"]}
        for direction in ("common", "front", "rear"):
            current, candidate = row.get(direction), other.get(direction)
            if current is None and candidate is None:
                comparison[direction] = None
            elif not current or not candidate:
                comparison[direction] = None
                comparison.update(status="INCOMPARABLE", reason="한쪽 결과가 없거나 읽기 오류입니다.")
            elif current.get("unit") != candidate.get("unit") or not current.get("condition") or current.get("condition") != candidate.get("condition"):
                comparison[direction] = None
                comparison.update(status="INCOMPARABLE", reason="동일 단위·조건 대응을 확인할 수 없습니다.")
            else:
                compared = dict(candidate)
                for field in ("value", "verdict"):
                    state = field + "_status"
                    if current.get(state, current["status"]) != "READY" or candidate.get(state, candidate["status"]) != "READY" or current.get(field + "_key") != candidate.get(field + "_key"):
                        compared[field] = None
                        compared[state] = "INCOMPARABLE"
                valid = any(compared.get(field) is not None for field in ("value", "verdict"))
                comparison[direction] = compared if valid else None
                if not valid:
                    comparison.update(status="INCOMPARABLE", reason="한쪽 결과가 없거나 읽기 오류입니다.")
        row["reference"] = comparison
    return result


def select_run(capture, run_id, mode, component, basis, run_option_id=None):
    if capture["environment"] != "DISTRIBUTION" or basis not in {"DETAIL", "REPORTED_SUMMARY"}:
        fail("유통환경과 집계 기준을 명시하세요.")
    candidates = [r for r in capture["payload"].get("runs", []) if r["id"] == run_id]
    runs = [r for r in candidates if option_projection(r, capture["id"])[0] == run_option_id] if run_option_id else [r for r in candidates if r["mode"] == mode]
    if len(runs) != 1:
        fail("선택 Run/Mode와 수집 버전이 일치하지 않습니다.")
    run = runs[0]
    all_components = {o.get("component_id") for s in run["scenes"] for o in [*s.get("observations", []), *s.get("media", [])]}
    if component not in all_components:
        fail("선택 Component가 해당 Run/Mode에 없습니다.")
    option_id, option_label, option_status = option_projection(run, capture["id"])
    context = {**capture["payload"]["context"], "simulation_case_id": capture["case_id"], "capture_id": capture["id"],
               "execution_run_id": run_id, "load_case_id": run["load_case_id"],
               "run_option_id": option_id, "option_label": option_label, "option_status": option_status,
               "mode": mode, "component_id": component, "basis": basis}
    context["context_key"] = context_key(context)
    return run, context


def observations(scene, component, basis, lines):
    return [o for o in scene.get("observations", []) if o.get("component_id") == component and o.get("basis") == basis
            and (o.get("kind") == "CORNER" or o.get("line_index") in lines)]


def peak(values, *, complete=False, basis=None, scope=None):
    numeric = [o for o in values if number(o.get("value")) is not None]
    maximum = max((number(o["value"]) for o in numeric), default=None)
    locations = [o for o in numeric if number(o["value"]) == maximum]
    return {"value": maximum, "unit": None, "unit_status": "UNCONFIRMED", "basis": basis, "scope": scope,
            "status": "MISSING" if maximum is None else "READY" if complete else "PARTIAL",
            "completeness": "COMPLETE" if complete else "PARTIAL", "locations": locations,
            "source_refs": [o.get("source_ref", {"row": o.get("row"), "column": o.get("column")}) for o in locations]}


def scene_edges(scene, component, basis, lines, member_id):
    source = observations(scene, component, basis, lines)
    result = []
    for edge, position in EDGES.items():
        vals = [o for o in source if o.get("kind") == "SIDE" and o.get("position") == position]
        complete = all(any(o.get("line_index") == line and number(o.get("value")) is not None for o in vals) for line in lines)
        complete = complete and bool(lines) and all(number(o.get("value")) is not None for o in vals)
        result.append({**peak(vals, complete=complete, basis=basis, scope="SELECTED_EDGE_LINES"), "edge": edge,
                       "scene_id": scene["id"], "member_id": member_id})
    return result


def scene_public(scene):
    return {"id": scene["id"], "label": scene["source_name"], **{k: scene.get(k) for k in (
        "scene_sequence_number", "scenario_number", "contact_code", "repetition", "order_status")},
        "description": " / ".join(str(scene.get(k) or "미확인") for k in ("contact_code", "repetition")),
        "match_key": scene.get("scene_match_key")}


def distribution(capture, run_id, mode, component, basis, edges, lines, run_option_id=None):
    run, context = select_run(capture, run_id, mode, component, basis, run_option_id)
    member_id = context_key(context)
    color_index = int(hashlib.sha256(capture["case_id"].encode()).hexdigest()[:8], 16) % 5 + 1
    member = {"id": member_id, "label": capture.get("source_name", capture["case_id"]),
              "color": f"var(--color-chart-series-{color_index})", **context}
    result = {"contract_version": 1, "context": context, "status": "READY", "members": [member], "scenes": [],
              "edge_peaks": [], "series": [], "contours": [], "behaviors": [], "location_peaks": [], "quality_issues": []}
    issues = set(capture["payload"].get("quality_issues", []))
    for scene in run["scenes"]:
        public = scene_public(scene)
        result["scenes"].append(public)
        values = scene_edges(scene, component, basis, lines, member_id)
        result["edge_peaks"].extend(values)
        extracted = observations(scene, component, basis, lines)
        if lines != {1, 2, 3, 4}:
            extracted = [o for o in extracted if o.get("kind") == "SIDE"]
        extracted_peak = peak(
            extracted,
            basis=basis,
            scope="EXTRACTED_SIDES_AND_CORNERS" if lines == {1, 2, 3, 4} else "EXTRACTED_SELECTED_LINES",
        )
        result["location_peaks"].append({**extracted_peak, "scene_id": scene["id"], "member_id": member_id})
        selected = [v for v in values if v["edge"] in edges]
        envelope = peak(selected, complete=bool(selected) and all(v["status"] == "READY" for v in selected), basis=basis, scope="SELECTED_EDGES_ENVELOPE")
        if not edges:
            envelope.update(status="NO_SELECTION", completeness="NO_SELECTION")
        for value in values:
            value["is_selected_maximum"] = value["edge"] in edges and value["value"] is not None and value["value"] == envelope["value"]
        result["series"].append({**envelope, "id": member_id + ":" + scene["id"], "scene_id": scene["id"],
                                  "scene_sequence_number": scene.get("scene_sequence_number"), "member_id": member_id,
                                  "edge": "ENVELOPE", "selected_edge_envelope": envelope["value"]})
        media = [m for m in scene.get("media", []) if m.get("component_id") == component]
        images = [m for m in media if m.get("kind") == "IMAGE"]
        asset = images[0] if len(images) == 1 else None
        result["contours"].append({"cell_id": member_id + ":" + scene["id"] + ":contour", "scene_id": scene["id"],
            "member_id": member_id, "asset": asset, "status": "READY" if asset else "AMBIGUOUS" if images else "MISSING",
            "value": extracted_peak if extracted_peak["value"] is not None else None,
            "reason": "프레임·수치/영상 시간 정합성 미확인", "scale_status": "UNCONFIRMED"})
        for role in ("CELL", "CUSHION", "BOX"):
            candidates = [m for m in media if m.get("subject_role") == role]
            result["behaviors"].append({"cell_id": member_id + ":" + scene["id"] + ":" + role, "scene_id": scene["id"],
                "member_id": member_id, "subject_role": role, "asset": candidates[0] if len(candidates) == 1 else None,
                "status": "READY" if len(candidates) == 1 else "MISSING", "reason": None if len(candidates) == 1 else "물리 대상 역할 매핑·자료 미제공"})
        issues.update(scene.get("quality_issues", []))
        if envelope["status"] not in {"READY", "NO_SELECTION"}:
            result["status"] = "PARTIAL"
    result["quality_issues"] = sorted(issues)
    return result


def comparison(parts):
    """Combine explicit members without guessing cross-Case Scene equivalence.

    Unknown transport profiles remain separate rows. This preserves exact
    scene IDs for graph clicks and transposed media cells.
    """
    result = {"contract_version": 1, "context": parts[0]["context"], "status": "READY",
              "members": [], "scenes": [], "edge_peaks": [], "series": [], "contours": [], "behaviors": [], "location_peaks": [],
              "quality_issues": ["CASE_SCENE_ALIGNMENT_UNCONFIRMED"]}
    for part in parts:
        result["members"].extend(part["members"])
        for scene in part["scenes"]:
            result["scenes"].append({**scene, "label": part["members"][0]["label"] + " · " + scene["label"]})
        for key in ("edge_peaks", "series", "contours", "behaviors", "location_peaks"):
            result[key].extend(part[key])
        result["quality_issues"].extend(part["quality_issues"])
        if part["status"] != "READY":
            result["status"] = "PARTIAL"
    for member in result["members"]:
        owned = {cell["scene_id"] for cell in result["contours"] if cell["member_id"] == member["id"]}
        for scene in result["scenes"]:
            if scene["id"] not in owned:
                result["contours"].append({"cell_id": member["id"] + ":" + scene["id"] + ":missing", "scene_id": scene["id"],
                    "member_id": member["id"], "asset": None, "status": "UNMATCHED", "reason": "운송 프로파일·자세·회차 대응 미확인"})
                for role in ("CELL", "CUSHION", "BOX"):
                    result["behaviors"].append({"cell_id": member["id"] + ":" + scene["id"] + ":" + role + ":missing", "scene_id": scene["id"],
                        "member_id": member["id"], "subject_role": role, "asset": None, "status": "UNMATCHED", "reason": "Scene 대응 미확인"})
    result["quality_issues"] = sorted(set(result["quality_issues"]))
    return result


def scene_detail(capture, scene_id, run_id, mode, component, basis, lines, position, run_option_id=None):
    run, context = select_run(capture, run_id, mode, component, basis, run_option_id)
    found = [s for s in run["scenes"] if s["id"] == scene_id]
    if len(found) != 1:
        fail("선택 Scene이 해당 Run/Mode/capture에 없습니다.")
    scene = found[0]
    context = {**context, "scene_id": scene_id}
    context["context_key"] = context_key(context)
    source = observations(scene, component, "DETAIL", lines)
    return {"contract_version": 1, "context": context, "scene": scene_public(scene),
            "edge_peaks": scene_edges(scene, component, basis, lines, context_key({**context, "scene_id": None})),
            "line_points": [{**o, "node_id": None, "alignment_node_id": o.get("alignment_node_id", o.get("node_id"))} for o in source if o.get("kind") == "SIDE" and o.get("position") == position],
            "corner_points": [o for o in source if o.get("kind") == "CORNER"],
            "assets": [m for m in scene.get("media", []) if m.get("component_id") == component],
            "quality_issues": sorted(set(capture["payload"].get("quality_issues", [])) | set(scene.get("quality_issues", [])))}
