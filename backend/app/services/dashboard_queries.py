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
    from . import folder_discovery, folder_discovery_environment, folder_schema_hierarchy, folder_schema_resolver

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

        projected = folder_schema_hierarchy.project_schema_hierarchy(schema, locations, root_key)
        schema_cases.update(projected["cases"])
        for key in ("load_cases", "execution_runs", "run_options", "scenes"):
            result[key].extend(projected[key])
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

    def emit(capture_id, label, dashboard_case_id, canonical_case_id, payload, match_status):
        parent = {"case_id": canonical_case_id, "simulation_case_id": canonical_case_id,
                  "capture_id": str(capture_id)}
        capture_entry = {"id": str(capture_id), "label": label, "dashboard_case_id": str(dashboard_case_id),
                         "match_status": match_status, **parent}
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
                    "label": str(scene.get("source_name") or scene.get("label") or scene.get("id") or "Scene"),
                    **scope,
                })
            with_values = {o["component_id"] for s in run["scenes"] for o in s.get("observations", []) if o.get("component_id")}
            media_only = {m["component_id"] for s in run["scenes"] for m in s.get("media", []) if m.get("component_id")} - with_values
            # Components with values come first so the screen's default shows numbers,
            # not a media-only component (each item says whether it has values).
            result["components"].extend({"id": c, "label": c, "has_values": True, **scope} for c in sorted(with_values))
            result["components"].extend({"id": c, "label": c, "has_values": False, **scope} for c in sorted(media_only))
        return capture_entry

    from .dashboard_capture import latest_capture_id, merge_latest_payload
    history_by_case: dict[str, list[tuple[str, Any]]] = {}
    latest_meta: dict[str, tuple[str, str]] = {}
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
        case_choice = case_choices.get(canonical_case_id)
        if case_choice is not None:
            case_choice["capture_count"] = int(case_choice.get("capture_count") or 0) + 1
            case_choice["match_status"] = "CAPTURED" if case_choice.get("source") == "FOLDER_SCHEMA" else "HISTORY_ONLY"
        match_status = "MATCHED_TO_SCHEMA" if case_choice and case_choice.get("source") == "FOLDER_SCHEMA" else "HISTORY_ONLY"
        result["captures"].append(emit(capture_id, str(created_at), case_id, canonical_case_id, _decode(raw), match_status))
        # Records are newest first; the merge wants oldest first.
        history_by_case.setdefault(str(case_id), []).insert(0, (str(capture_id), raw))
        latest_meta[str(case_id)] = (canonical_case_id, match_status)
    # The default view of every Case: newest result of each Scene folder
    # merged across all captures (user decision 2026-10-02). Individual
    # captures stay listed as update history.
    latest_entries = []
    for dashboard_case_id, entries in history_by_case.items():
        canonical_case_id, match_status = latest_meta[dashboard_case_id]
        merged = merge_latest_payload(entries)
        entry = emit(latest_capture_id(dashboard_case_id), "최신 결과", dashboard_case_id, canonical_case_id,
                     merged, match_status)
        entry["kind"] = "LATEST"
        entry["merged_capture_count"] = len(merged.get("merged_capture_ids") or [])
        latest_entries.append(entry)
    result["captures"] = latest_entries + result["captures"]
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


VIDEO_PAGE_SIZE_MAX = 20


def select_run_option(capture, run_id, mode=None, run_option_id=None):
    """Pick one Run option of a distribution capture without requiring a Component."""
    if capture["environment"] != "DISTRIBUTION":
        fail("유통환경 수집 버전을 선택하세요.")
    candidates = [r for r in capture["payload"].get("runs", []) if r["id"] == run_id]
    if run_option_id:
        runs = [r for r in candidates if option_projection(r, capture["id"])[0] == run_option_id]
    elif mode:
        runs = [r for r in candidates if r["mode"] == mode]
    else:
        runs = candidates
    if len(runs) != 1:
        fail("선택 Run/Run Option과 수집 버전이 일치하지 않습니다.")
    run = runs[0]
    option_id, option_label, option_status = option_projection(run, capture["id"])
    context = {**capture["payload"]["context"], "simulation_case_id": capture["case_id"], "capture_id": capture["id"],
               "execution_run_id": run_id, "load_case_id": run["load_case_id"],
               "run_option_id": option_id, "option_label": option_label, "option_status": option_status,
               "mode": run["mode"]}
    context["context_key"] = context_key(context)
    context["load_case_name"] = run.get("load_case_name")
    context["run_label"] = run.get("source_name")
    return run, context


def run_videos(capture, run_id, *, mode=None, run_option_id=None, page=1, page_size=VIDEO_PAGE_SIZE_MAX):
    """Every VIDEO media of every Scene of one Run option, in Scene order."""
    run, context = select_run_option(capture, run_id, mode, run_option_id)
    videos = []
    for scene in run.get("scenes", []):
        label = scene.get("label") or scene.get("source_name") or scene["id"]
        for media in scene.get("media", []):
            if media.get("kind") != "VIDEO" or not media.get("asset_id"):
                continue
            name = str(media.get("relative_path") or "").replace("\\", "/").rsplit("/", 1)[-1]
            videos.append({"video_id": f"{scene['id']}:{media['asset_id']}", "asset_id": media["asset_id"],
                           "scene_id": scene["id"], "scene_label": label,
                           "scene_sequence_number": scene.get("scene_sequence_number"),
                           "title": media.get("title") or name or label, "component_id": media.get("component_id"),
                           "frame_role": media.get("frame_role"), "status": media.get("status") or "READY",
                           "source_capture_id": scene.get("source_capture_id") or capture["id"]})
    total_items = len(videos)
    total_pages = (total_items + page_size - 1) // page_size if total_items else 0
    start = (page - 1) * page_size
    return {"contract_version": 1, "context": context,
            "pagination": {"page": page, "page_size": page_size, "total_items": total_items, "total_pages": total_pages,
                           "has_previous": page > 1 and total_pages > 0, "has_next": page < total_pages},
            "videos": videos[start:start + page_size] if start < total_items else []}
