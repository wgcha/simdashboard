"""Project-scoped projections of immutable result captures, never live files."""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from ..domains.dashboard import scene_summary
from ..domains.dashboard.parser import scene_sequence_label, scene_sequence_number, scene_sort_key
from ..domains.dashboard.units import stress_unit, stress_unit_of, unit_system
from . import video_representatives
from .dashboard_capture import DashboardCaptureError, _decode

EDGES = {"LEFT": "LH", "RIGHT": "RH", "TOP": "TOP", "BOTTOM": "BOT"}
USAGE_KEYS = {
    "Settle": ("Settle", "Set Tilt Angle @ Settle (deg)", "deg"),
    "Wobble": ("Wobble", "Wobble Disp. (mm)", "mm"),
    "Horizontal_Force_Angle": ("Horizontal_Force_Angle", "Set Tilt Angle Difference (deg)", "deg"),
    "Slope_Angle": ("Slope_Angle", "Slope Angle (deg)", "deg"),
    "Slope_Angle_360": ("Slope_Angle_360", None, None),
}


logger = logging.getLogger(__name__)
RESULT_ENVIRONMENTS = ("USAGE", "DISTRIBUTION")


def result_environments(conn, project_id: str, request_id: str) -> dict[str, Any] | None:
    """Environments of a request's registered Cases (case-results-environment.md §2).

    Returns ``None`` when the request does not exist in the project.
    """
    if not conn.execute("SELECT 1 FROM analysis_requests WHERE id=? AND project_id=?", [request_id, project_id]).fetchone():
        return None
    counts = dict.fromkeys(RESULT_ENVIRONMENTS, 0)
    for environment, count in conn.execute(
            "SELECT environment,count(*) FROM dashboard_cases WHERE project_id=? AND request_id=? GROUP BY environment",
            [project_id, request_id]).fetchall():
        if str(environment) in counts:
            counts[str(environment)] = int(count)
    # Environments of the request's folder registrations (no Cases needed): the default
    # environment for result registration of a request that has no Cases yet.
    folder = {str(row[0]) for row in conn.execute(
        "SELECT DISTINCT environment FROM folder_environment_registrations "
        "WHERE project_id=? AND request_id=? AND deleted_at IS NULL", [project_id, request_id]).fetchall()}
    return {"environments": [env for env in RESULT_ENVIRONMENTS if counts[env] > 0], "case_counts": counts,
            "folder_environments": [env for env in RESULT_ENVIRONMENTS if env in folder]}


def fail(message: str):
    raise DashboardCaptureError("DASHBOARD_CONTEXT_INVALID", message)


def context_key(context):
    return "|".join(str(context.get(k) or "") for k in (
        "project_id", "request_id", "simulation_case_id", "load_case_id",
        "execution_run_id", "run_option_id", "mode", "capture_id", "component_id", "basis", "scene_id"))


number = scene_summary.finite_number
ALL_LINES = frozenset({1, 2, 3, 4})


def scene_groups(scene):
    """Value groups of a Scene: from its observations (old captures, on-demand detail) or its summary.

    A summary Scene (``values_storage``) must have been loaded by ``dashboard_capture.attach_scene_values``.
    """
    if "observations" in scene:
        return scene_summary.groups_from_observations(scene.get("observations") or [])
    if "_groups" in scene:
        return scene["_groups"]
    if scene.get("values_storage") == scene_summary.VALUES_STORAGE:
        raise DashboardCaptureError("DASHBOARD_VALUES_NOT_LOADED", "Scene 결과값을 불러오지 못했습니다.")
    return {}


def scene_value_components(scene):
    """Components with values in a Scene (catalog ``has_values``)."""
    if "observations" not in scene and "value_components" in scene:
        return {str(item) for item in scene.get("value_components") or [] if item}
    return {o["component_id"] for o in scene.get("observations", []) if o.get("component_id")}


def option_projection(run, capture_id):
    status = run.get("option_status") or ("UNRESOLVED" if run.get("mode") == "UNKNOWN" else "PRESENT")
    label = run.get("option_label")
    if status == "UNRESOLVED" and not label:
        label = "미확인(기존 자료)"
    option_id = run.get("run_option_id") or "legacy-option-" + hashlib.sha256(f"{capture_id}:{run['id']}:{run['mode']}".encode()).hexdigest()[:24]
    return option_id, label, status


def _name_warnings(schema, locations, root_key, environment):
    """W6 folder name warnings; a fault here must never hide the results."""
    from .folder_name_warnings import folder_name_warnings
    try:
        return folder_name_warnings(schema, locations, root_key, environment)
    except Exception:  # noqa: BLE001 - advisory only
        logger.exception("folder name warnings failed")
        return []


def _add_registry_hierarchy(conn, request_id, environment, result, captured_cases) -> None:
    """Load case / Run case / Run option folders of the live registrations, for Cases without choices.

    ``folder_environment_registry`` keeps every non-Scene folder the registration was made from, so a
    Case whose capture job is pending or failed (no captured Run yet, Folder Schema unavailable) still
    offers its known levels.  Cases that already have schema choices or a capture are left alone.
    """
    rows = conn.execute(
        "SELECT g.relative_path,g.role_kind,g.target_id,g.raw_name,g.option_status FROM folder_environment_registry g "
        "JOIN folder_environment_registrations r ON r.id=g.registration_id "
        "WHERE r.request_id=? AND r.environment=? AND r.status<>'DELETED' "
        "AND g.role_kind IN ('LOAD_CASE','EXECUTION_RUN','RUN_OPTION') ORDER BY r.created_at,g.relative_path",
        [request_id, str(environment).upper()]).fetchall()
    if not rows:
        return
    have = {str(item.get("case_id")) for item in result["load_cases"]} | {str(item) for item in captured_cases}
    cases = {str(item["relative_path"]).casefold(): item for item in result["cases"]
             if item.get("relative_path") and str(item["id"]) not in have}
    if not cases:
        return

    def parent(path: str, candidates: dict[str, Any]) -> tuple[str, Any] | None:
        parts = path.casefold().split("/")
        for size in range(len(parts) - 1, 0, -1):
            key = "/".join(parts[:size])
            if key in candidates:
                return key, candidates[key]
        return None

    loads: dict[str, Any] = {}
    runs: dict[str, Any] = {}
    seen: set[tuple[str, str]] = set()
    for relative_path, role, target_id, raw_name, option_status in rows:
        path = str(relative_path)
        if (role, path.casefold()) in seen:
            continue
        seen.add((role, path.casefold()))
        base = {"id": str(target_id), "label": str(raw_name), "relative_path": path,
                "capture_id": None, "match_status": "UNCAPTURED"}
        if role == "LOAD_CASE":
            found = parent(path, cases)
            if found:
                choice = {**base, "case_id": str(found[1]["id"])}
                loads[path.casefold()] = choice
                result["load_cases"].append(choice)
        elif role == "EXECUTION_RUN":
            found = parent(path, loads)
            if found:
                choice = {**base, "case_id": found[1]["case_id"], "load_case_id": found[1]["id"]}
                runs[path.casefold()] = choice
                result["execution_runs"].append(choice)
        else:
            found = parent(path, runs)
            if found:
                result["run_options"].append({
                    **base, "case_id": found[1]["case_id"], "execution_run_id": found[1]["id"],
                    "run_option_id": base["id"], "option_label": base["label"],
                    "option_status": option_status or "PRESENT", "mode": base["label"].upper()})


def catalog(conn, request_id, environment, project_id=None):
    from . import folder_discovery, folder_discovery_environment, folder_schema_hierarchy, folder_schema_resolver

    result = {"contract_version": 1, "environment": environment, "cases": [], "captures": [],
              "load_cases": [], "execution_runs": [], "run_options": [], "modes": [], "components": [],
              "scenes": [], "final_history": [], "name_warnings": [],
              "folder_schema": {"status": "UNAVAILABLE", "diagnostic": None, "snapshot_id": None},
              "bases": [{"id": "REPORTED_SUMMARY", "label": "원본 요약"}, {"id": "DETAIL", "label": "상세 추출값"}]}
    records = conn.execute("""SELECT dc.id,dc.source_name,dc.storage_root_id,dc.relative_path,c.id,c.created_at,c.payload_json
        FROM dashboard_cases dc LEFT JOIN dashboard_captures c ON c.case_id=dc.id
        WHERE dc.request_id=? AND dc.environment=? ORDER BY dc.source_name,c.created_at DESC,c.id DESC""",
        # Same capture order as get_latest_capture (created_at,id), reversed: one "latest" rule.
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
        result["name_warnings"] = _name_warnings(schema, locations, root_key, environment)
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
    # SCX drive: a Case whose capture job is still pending is shown as 결과 받는 중 (capture_jobs).
    from .capture_jobs import pending_cases
    from .dashboard_capture import _case_id
    pending = pending_cases(conn, request_id, environment)
    for case in result["cases"] if pending else []:
        dashboard_id = case.get("dashboard_case_id") or (
            _case_id(current_storage_root_id, str(case["relative_path"]))
            if current_storage_root_id and case.get("relative_path") else None)
        if dashboard_id in pending:
            case["capture_pending"] = True
    from .capture_jobs import failed_cases
    failed = failed_cases(conn, request_id, environment)
    for case in result["cases"] if failed else []:
        dashboard_id = case.get("dashboard_case_id") or (
            _case_id(current_storage_root_id, str(case["relative_path"]))
            if current_storage_root_id and case.get("relative_path") else None)
        if dashboard_id in failed and not case.get("capture_pending"):
            case["capture_failed"] = True

    # A registered Case shows the hierarchy known from its registration even before (or without) a
    # capture or a readable Folder Schema snapshot (drive busy/auth errors leave captures pending).
    captured_cases = {dashboard_case_ids.get(str(row[0]), str(row[0])) for row in records if row[4]}
    _add_registry_hierarchy(conn, request_id, environment, result, captured_cases)
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
            with_values = {c for s in run["scenes"] for c in scene_value_components(s)}
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
    all_components = ({c for s in run["scenes"] for c in scene_value_components(s)}
                      | {o.get("component_id") for s in run["scenes"] for o in [*s.get("observations", []), *s.get("media", [])]})
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
    # Stress of the ton-mm-s unit system unless the source states a unit (2026-10-17).
    unit, unit_status = stress_unit(numeric)
    return {"value": maximum, "unit": unit, "unit_status": unit_status, "basis": basis, "scope": scope,
            "status": "MISSING" if maximum is None else "READY" if complete else "PARTIAL",
            "completeness": "COMPLETE" if complete else "PARTIAL", "locations": locations,
            "source_refs": [o.get("source_ref", {"row": o.get("row"), "column": o.get("column")}) for o in locations]}


def _full_groups(scene):
    """Groups of the Scene's csv files parsed again (only for a peak decided by a truncated group)."""
    if "_full_groups" not in scene:
        loader = scene.get("_load_observations")
        if loader is None:
            raise DashboardCaptureError("DASHBOARD_VALUES_NOT_LOADED", "Scene 결과값을 불러오지 못했습니다.")
        scene["_full_groups"] = scene_summary.groups_from_observations(loader())
    return scene["_full_groups"]


def keyed_peak(scene, groups, keys, *, complete=False, basis=None, scope=None):
    """``group_peak`` of these group keys; a truncated group at the maximum is read in full from the csv files."""
    selected = [groups.get(key) for key in keys]
    maximum = max((g.maximum for g in selected if g is not None and g.maximum is not None), default=None)
    if maximum is not None and any(g is not None and g.truncated and g.maximum == maximum for g in selected):
        full = _full_groups(scene)
        selected = [full.get(key) for key in keys]
    return group_peak(selected, complete=complete, basis=basis, scope=scope)


def group_peak(groups, *, complete=False, basis=None, scope=None):
    """``peak`` of the observations of these groups, from their reduced form (same value, locations, unit)."""
    present = [g for g in groups if g is not None and g.maximum is not None]
    maximum = max((g.maximum for g in present), default=None)
    ranked = sorted((location for g in present if g.maximum == maximum for location in g.locations),
                    key=lambda location: location[0])
    locations = [observation for _sequence, observation in ranked]
    unit, unit_status = stress_unit_of({unit for g in present for unit in g.units})
    return {"value": maximum, "unit": unit, "unit_status": unit_status, "basis": basis, "scope": scope,
            "status": "MISSING" if maximum is None else "READY" if complete else "PARTIAL",
            "completeness": "COMPLETE" if complete else "PARTIAL", "locations": locations,
            "source_refs": [o.get("source_ref", {"row": o.get("row"), "column": o.get("column")}) for o in locations]}


def scene_edges(scene, component, basis, lines, member_id, groups=None):
    groups = scene_groups(scene) if groups is None else groups
    result = []
    for edge, position in EDGES.items():
        keys = [(component, basis, "SIDE", position, line) for line in lines]
        selected = [groups.get(key) for key in keys]
        present = [g for g in selected if g is not None]
        complete = all(g is not None and g.numeric > 0 for g in selected)
        complete = complete and bool(lines) and all(g.numeric == g.count for g in present)
        peak_value = keyed_peak(scene, groups, keys, complete=complete, basis=basis, scope="SELECTED_EDGE_LINES")
        result.append({**peak_value, "edge": edge,
                       "scene_id": scene["id"], "member_id": member_id})
    return result


def extracted_keys(groups, component, basis, lines):
    """Group keys of ``observations(scene, component, basis, lines)``, SIDE only unless every line is selected."""
    every_line = set(lines) == ALL_LINES
    return [key for key in groups if key[0] == component and key[1] == basis
            and (key[2] == "CORNER" or key[4] in lines) and (every_line or key[2] == "SIDE")]


def scene_name(scene):
    return str(scene.get("source_name") or scene.get("label") or "")


def scene_sequence(scene):
    """순번 from the Scene folder name at read time; stored captures need no migration."""
    name = scene_name(scene)
    return scene_sequence_number(name) if name else scene.get("scene_sequence_number")


def scene_label_text(scene):
    """순번 display text: the number, or the name's first token (<= 4 chars) when it has no digit."""
    name = scene_name(scene)
    if name:
        return scene_sequence_label(name)
    stored = scene.get("scene_sequence_number")
    return str(stored) if stored is not None else None


def ordered_scenes(scenes):
    return sorted(scenes, key=lambda scene: scene_sort_key(scene_name(scene)))


def scene_public(scene):
    sequence = scene_sequence(scene)
    return {"id": scene["id"], "label": scene["source_name"], **{k: scene.get(k) for k in (
        "scenario_number", "contact_code", "repetition")},
        "scene_sequence_number": sequence, "scene_sequence_label": scene_label_text(scene),
        "order_status": "CONFIRMED" if sequence is not None else "UNCONFIRMED",
        "description": " / ".join(str(scene.get(k) or "미확인") for k in ("contact_code", "repetition")),
        "match_key": scene.get("scene_match_key")}


def distribution(capture, run_id, mode, component, basis, edges, lines, run_option_id=None):
    run, context = select_run(capture, run_id, mode, component, basis, run_option_id)
    member_id = context_key(context)
    color_index = int(hashlib.sha256(capture["case_id"].encode()).hexdigest()[:8], 16) % 5 + 1
    member = {"id": member_id, "label": capture.get("source_name", capture["case_id"]),
              "color": f"var(--color-chart-series-{color_index})", **context}
    result = {"contract_version": 1, "context": context, "status": "READY", "members": [member], "scenes": [],
              "edge_peaks": [], "series": [], "contours": [], "behaviors": [], "location_peaks": [], "quality_issues": [],
              "unit_system": unit_system()}
    issues = set(capture["payload"].get("quality_issues", []))
    for scene in ordered_scenes(run["scenes"]):
        public = scene_public(scene)
        result["scenes"].append(public)
        groups = scene_groups(scene)
        values = scene_edges(scene, component, basis, lines, member_id, groups)
        result["edge_peaks"].extend(values)
        extracted_peak = keyed_peak(
            scene, groups, extracted_keys(groups, component, basis, lines),
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
                                  "scene_sequence_number": scene_sequence(scene),
                                  "scene_sequence_label": scene_label_text(scene), "member_id": member_id,
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
              "quality_issues": ["CASE_SCENE_ALIGNMENT_UNCONFIRMED"], "unit_system": unit_system()}
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
    if "observations" not in scene and scene.get("values_storage") == scene_summary.VALUES_STORAGE:
        # Summary captures (issue #55): the router parses this Scene's csv files on demand first.
        raise DashboardCaptureError("DASHBOARD_VALUES_NOT_LOADED", "Scene 상세 결과값을 불러오지 못했습니다.")
    context = {**context, "scene_id": scene_id}
    context["context_key"] = context_key(context)
    source = observations(scene, component, "DETAIL", lines)
    return {"contract_version": 1, "context": context, "scene": scene_public(scene),
            "edge_peaks": scene_edges(scene, component, basis, lines, context_key({**context, "scene_id": None})),
            "line_points": [{**o, "node_id": None, "alignment_node_id": o.get("alignment_node_id", o.get("node_id"))} for o in source if o.get("kind") == "SIDE" and o.get("position") == position],
            "corner_points": [o for o in source if o.get("kind") == "CORNER"],
            "assets": [m for m in scene.get("media", []) if m.get("component_id") == component],
            "quality_issues": sorted(set(capture["payload"].get("quality_issues", [])) | set(scene.get("quality_issues", []))),
            "unit_system": unit_system()}


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


def _scene_videos(capture, run_id, *, mode=None, run_option_id=None, selection=None):
    """Per Scene (in Scene order): its stored videos, the unplayable-format files and the representative."""
    run, context = select_run_option(capture, run_id, mode, run_option_id)
    selection = selection or {}
    keywords = list(selection.get("keywords") or [])
    choices = selection.get("choices") or {}
    groups = []
    for scene in ordered_scenes(run.get("scenes", [])):
        label = scene.get("label") or scene.get("source_name") or scene["id"]
        key = video_representatives.scene_key(run, scene)
        stored = []
        for media in scene.get("media", []):
            if media.get("kind") != "VIDEO" or not media.get("asset_id"):
                continue
            stored.append({**media, "relative_path": str(media.get("relative_path") or "")})
        unplayable = [item for item in scene.get("unplayable_videos") or [] if isinstance(item, dict) and item.get("relative_path")]
        choice = choices.get(key)
        representative, source, choice_missing = video_representatives.pick_representative(
            stored, keywords, choice.get("relative_path") if choice else None)
        groups.append({"scene": scene, "label": label, "key": key, "stored": stored, "unplayable": unplayable,
                       "representative": representative, "source": source, "choice": choice if source == "USER" else None,
                       "choice_missing": choice_missing})
    return run, context, groups


def _video_item(capture, group, media, probe):
    scene = group["scene"]
    name = video_representatives.file_name(media["relative_path"])
    info = probe.get(media["asset_id"], {})
    is_representative = group["representative"] is not None and media["asset_id"] == group["representative"]["asset_id"]
    codec_blocked = info.get("playable") is False
    return {"video_id": f"{scene['id']}:{media['asset_id']}", "asset_id": media["asset_id"],
            "scene_id": scene["id"], "scene_label": group["label"], "scene_key": group["key"],
            "scene_sequence_number": scene_sequence(scene), "scene_sequence_label": scene_label_text(scene),
            "title": media.get("title") or name or group["label"], "component_id": media.get("component_id"),
            "frame_role": media.get("frame_role"), "status": media.get("status") or "READY",
            "source_capture_id": scene.get("source_capture_id") or capture["id"],
            "file_name": name, "relative_path": media["relative_path"], "size_bytes": info.get("size_bytes"),
            "codec": info.get("codec"), "playable": not codec_blocked,
            "unplayable_reason": "CODEC" if codec_blocked else None,
            "scene_video_count": len(group["stored"]) + len(group["unplayable"]),
            "is_representative": is_representative,
            "representative_source": group["source"] if is_representative else None}


def _unplayable_item(capture, group, entry):
    scene = group["scene"]
    path = str(entry["relative_path"])
    name = video_representatives.file_name(path)
    suffix = name.rsplit(".", 1)[-1].casefold() if "." in name else ""
    digest = hashlib.sha256(path.encode()).hexdigest()[:12]
    return {"video_id": f"{scene['id']}:unplayable:{digest}", "asset_id": None, "scene_id": scene["id"],
            "scene_label": group["label"], "scene_key": group["key"], "scene_sequence_number": scene_sequence(scene),
            "scene_sequence_label": scene_label_text(scene), "title": name, "component_id": None, "frame_role": None,
            "status": "UNPLAYABLE", "source_capture_id": scene.get("source_capture_id") or capture["id"],
            "file_name": name, "relative_path": path, "size_bytes": entry.get("size_bytes"), "codec": None,
            "format": str(entry.get("format") or suffix) or None, "playable": False,
            "unplayable_reason": str(entry.get("reason") or "FORMAT"),
            "scene_video_count": len(group["stored"]) + len(group["unplayable"]),
            "is_representative": False, "representative_source": None}


def run_videos(capture, run_id, *, mode=None, run_option_id=None, page=1, page_size=VIDEO_PAGE_SIZE_MAX,
               scene_id=None, scene_label=None, representative_only=False, selection=None, probe=None):
    """Every VIDEO media of every Scene of one Run option, in Scene order.

    ``selection`` (``keywords``, ``choices``) decides each Scene's representative (see ``video_representatives``).
    ``scene_id`` / ``scene_label`` keep one Scene; ``representative_only`` keeps one tile per Scene (its
    representative, or its first unplayable-format file when it has no stored video).  ``probe`` maps the
    asset ids of the returned page to ``{size_bytes, codec, playable}`` (database access stays in the router).
    Files the capture does not store (``avi`` ...) are listed with ``status: UNPLAYABLE`` and no ``asset_id``.
    """
    run, context, groups = _scene_videos(capture, run_id, mode=mode, run_option_id=run_option_id, selection=selection)
    wanted = str(scene_label).casefold() if scene_label else None
    entries = []   # (group, kind, media) in display order
    for group in groups:
        if scene_id and group["scene"]["id"] != scene_id:
            continue
        if wanted is not None and str(group["label"]).casefold() != wanted:
            continue
        if representative_only:
            if group["representative"] is not None:
                entries.append((group, "stored", group["representative"]))
            elif group["unplayable"]:
                entries.append((group, "unplayable", group["unplayable"][0]))
            continue
        entries.extend((group, "stored", media) for media in group["stored"])
        entries.extend((group, "unplayable", entry) for entry in group["unplayable"])
    total_items = len(entries)
    total_pages = (total_items + page_size - 1) // page_size if total_items else 0
    start = (page - 1) * page_size
    window = entries[start:start + page_size] if start < total_items else []
    info = probe({m["asset_id"]: m["relative_path"] for _g, kind, m in window if kind == "stored"}) if probe and window else {}
    videos = [_video_item(capture, group, media, info) if kind == "stored" else _unplayable_item(capture, group, media)
              for group, kind, media in window]
    names = (selection or {}).get("names") or {}
    for video, (group, _kind, _media) in zip(videos, window):
        choice = group["choice"]
        video["choice"] = {"relative_path": choice["relative_path"], "chosen_by": choice.get("chosen_by"),
                           "chosen_by_name": names.get(str(choice.get("chosen_by"))),
                           "chosen_at": choice.get("chosen_at")} if choice and video["is_representative"] else None
        video["choice_missing"] = bool(group["choice_missing"]) and video["is_representative"]
    return {"contract_version": 1, "context": context, "case_id": capture.get("case_id"),
            "video_rule": {"keywords": list((selection or {}).get("keywords") or [])},
            "pagination": {"page": page, "page_size": page_size, "total_items": total_items, "total_pages": total_pages,
                           "has_previous": page > 1 and total_pages > 0, "has_next": page < total_pages},
            "videos": videos}


def find_scene_by_key(capture, key):
    """``(run, scene)`` of a merged/exact capture by its re-capture-stable Scene key, or ``(None, None)``."""
    for run in capture["payload"].get("runs", []):
        for scene in run.get("scenes", []):
            if video_representatives.scene_key(run, scene) == key:
                return run, scene
    return None, None


def video_scenes(capture, run_id, *, mode=None, run_option_id=None, selection=None):
    """One row per Scene of the Run option with its video counts and representative (no asset reads)."""
    run, context, groups = _scene_videos(capture, run_id, mode=mode, run_option_id=run_option_id, selection=selection)
    rows = []
    for group in groups:
        scene = group["scene"]
        representative = group["representative"]
        rows.append({"scene_id": scene["id"], "scene_key": group["key"], "label": group["label"],
                     "scene_sequence_number": scene_sequence(scene), "scene_sequence_label": scene_label_text(scene),
                     "video_count": len(group["stored"]), "unplayable_count": len(group["unplayable"]),
                     "representative_asset_id": representative["asset_id"] if representative else None,
                     "representative_path": representative["relative_path"] if representative else None,
                     "representative_source": group["source"] if representative else None})
    return {"contract_version": 1, "context": context, "case_id": capture.get("case_id"), "scenes": rows}
