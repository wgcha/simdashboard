"""DEPTH_V1 depth-based folder role schema (docs/contracts/depth-schema.md §4, §5, §6, §11)."""
from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.database_connection import connect
from app.services import environment_folder_profiles as profiles
from app.services import folder_discovery_environment as fde
from tests.test_new_scene_registration import CSV, CSV_BYTES, admin_client  # noqa: F401

ENV = "/api/folder-discovery/environments"
PROJECT = "75R9J_PV"
DIST = f"{PROJECT}/[WR-0002]_[유통_환경]"
USAGE = f"{PROJECT}/[WR-0001]_[사용_환경]"
DIST_CASE = "Package_Model_SetCase3_CushionCase3_조건표시"
DIST_TAIL = "Drop/85qn80h_ref_organized/INDIVIDUAL"
FINAL_ID = "0bb7b4f6567f4ce985bba010c7f569e7"
USAGE_CASE = "Assy_RES_Model_SetCase1_StandCase1_Inner"
COND = "75R9J_Set0907_Stand0907_Force_spg0.1"
USAGE_FILES = {
    "Settle": {f"{COND}_settle_result.json": {"Set Tilt Angle @ Settle (deg)": 1.18}},
    "Wobble": {f"{COND}_wobble_center_{d}_result.json": {"Wobble Disp. (mm)": 2.5} for d in ("front", "back")},
    "Horizontal_Force_Angle": {f"{COND}_horizontal_force_angle_{d}_result.json": {"Set Tilt Angle Difference (deg)": 0.4}
                               for d in ("front", "back")},
    "Slope_Angle": {f"{COND}_slope_angle_{d}_result.json": {"Slope Angle (deg)": 10.0, "OK/NG": "OK"} for d in ("front", "back")},
    "Slope_Angle_360": {f"{COND}_slope_angle_{d}_360_result.json": {"OK/NG": "OK"} for d in ("front", "back")},
}


def dist_rules(**kwargs):
    return profiles.default_depth_rules("DISTRIBUTION", "dss-test", **kwargs)


def usage_rules(**kwargs):
    return profiles.default_depth_rules("USAGE", "dss-test", **kwargs)


def dist_tree_paths():
    case = f"{DIST}/Working/{DIST_CASE}"
    paths = [PROJECT, DIST, f"{DIST}/Working", case]
    parts = DIST_TAIL.split("/")
    for index in range(len(parts)):
        paths.append(f"{case}/{'/'.join(parts[:index + 1])}")
    paths.append(f"{case}/{DIST_TAIL}/2_Face")
    final = f"{DIST}/Final"
    paths += [final, f"{final}/CAD", f"{final}/.finalizations", f"{final}/.finalizations/{FINAL_ID}"]
    for branch in ("CAE", "Report"):
        base = f"{final}/{branch}/{DIST_CASE}/{FINAL_ID}"
        paths += [f"{final}/{branch}", f"{final}/{branch}/{DIST_CASE}", base]
        for index in range(len(parts)):
            paths.append(f"{base}/{'/'.join(parts[:index + 1])}")
    return paths


def usage_tree_paths():
    case = f"{USAGE}/Working/{USAGE_CASE}"
    return [PROJECT, USAGE, f"{USAGE}/Working", case, *[f"{case}/{scene}" for scene in USAGE_FILES]]


def _json(value):
    """PostgreSQL returns JSONB columns as dict/list; DuckDB returns JSON text."""
    return json.loads(value) if isinstance(value, (str, bytes)) else value


def by_path(entries):
    return {entry["relative_path"]: entry for entry in entries}


# ---- pure resolve_path / annotate_tree ---------------------------------------

@pytest.mark.unit
def test_distribution_real_tree_has_no_deviation_and_matches_standard_roles():
    resolved = by_path(profiles.resolve_tree(dist_tree_paths(), dist_rules()))
    assert not [e for e in resolved.values() if e["deviation"]]
    case = f"{DIST}/Working/{DIST_CASE}"
    expected = {PROJECT: ("UPPER", "PROJECT"), DIST: ("UPPER", "REQUEST"), f"{DIST}/Working": ("LOWER", "WORKING"),
                case: ("LOWER", "SIMULATION_CASE"), f"{case}/Drop": ("LOWER", "LOAD_CASE"),
                f"{case}/Drop/85qn80h_ref_organized": ("LOWER", "EXECUTION_RUN"),
                f"{case}/{DIST_TAIL}": ("LOWER", "RUN_OPTION"), f"{case}/{DIST_TAIL}/2_Face": ("LOWER", "SCENE")}
    for path, (segment, role) in expected.items():
        assert (resolved[path]["segment"], resolved[path]["role_kind"], resolved[path]["status"]) == (segment, role, "CONFIRMED"), path
    assert resolved[f"{case}/{DIST_TAIL}/2_Face"]["level"] == 6
    # §11.7 Final fixture
    final = f"{DIST}/Final"
    assert resolved[final]["role_kind"] == "FINAL"
    assert resolved[f"{final}/CAE"]["role_kind"] == "FINAL_CAE"
    assert resolved[f"{final}/Report"]["role_kind"] == "FINAL_REPORTS"
    assert resolved[f"{final}/CAD"]["role_kind"] == "FINAL_CAD"
    assert f"{final}/.finalizations" not in resolved and f"{final}/.finalizations/{FINAL_ID}" not in resolved
    base = f"{final}/CAE/{DIST_CASE}"
    roles = [resolved[p]["role_kind"] for p in (base, f"{base}/{FINAL_ID}", f"{base}/{FINAL_ID}/Drop",
                                                 f"{base}/{FINAL_ID}/Drop/85qn80h_ref_organized",
                                                 f"{base}/{FINAL_ID}/{DIST_TAIL}")]
    assert roles == ["SIMULATION_CASE", "FINAL_VERSION", "LOAD_CASE", "EXECUTION_RUN", "RUN_OPTION"]
    assert all(resolved[p]["segment"] == "FINAL" for p in resolved if p.startswith(final))


@pytest.mark.unit
def test_usage_real_tree_has_no_deviation_and_scenes_by_depth():
    resolved = by_path(profiles.resolve_tree(usage_tree_paths(), usage_rules()))
    assert not [e for e in resolved.values() if e["deviation"]]
    case = f"{USAGE}/Working/{USAGE_CASE}"
    assert resolved[case]["role_kind"] == "SIMULATION_CASE"
    assert {resolved[f"{case}/{scene}"]["role_kind"] for scene in USAGE_FILES} == {"SCENE"}


@pytest.mark.unit
def test_any_name_at_run_option_level_is_run_option():
    case = f"{DIST}/Working/{DIST_CASE}/Drop/run"
    rules = dist_rules()
    for name in ("INDIVIDUAL", "CUMULATIVE", "ALL", "2_Face"):
        resolved = profiles.resolve_path(f"{case}/{name}".split("/"), rules)
        assert (resolved["role_kind"], resolved["deviation"]) == ("RUN_OPTION", None)


@pytest.mark.unit
@pytest.mark.parametrize("name,code", [("[WR-0003]_[사용_유통]", "ENV_KEYWORD_BOTH"), ("[WR-0003]_[기타]", "ENV_KEYWORD_NONE")])
def test_request_keyword_both_or_none_blocks_and_leaves_lower_unresolved(name, code):
    request = f"{PROJECT}/{name}"
    resolved = by_path(profiles.resolve_tree([PROJECT, request, f"{request}/Working", f"{request}/Working/Case"], dist_rules()))
    assert resolved[request]["deviation"]["code"] == code and resolved[request]["status"] == "UNRESOLVED"
    assert code in profiles.BLOCKING_DEVIATIONS
    assert {resolved[p]["status"] for p in (f"{request}/Working", f"{request}/Working/Case")} == {"UNRESOLVED"}
    assert {resolved[p]["role_kind"] for p in (f"{request}/Working", f"{request}/Working/Case")} == {None}


@pytest.mark.unit
def test_working_missing_and_unexpected_children():
    resolved = by_path(profiles.resolve_tree([PROJECT, DIST, f"{DIST}/Final", f"{DIST}/Misc"], dist_rules()))
    assert resolved[DIST]["deviation"]["code"] == "WORKING_MISSING"
    assert resolved[f"{DIST}/Misc"]["deviation"]["code"] == "UNEXPECTED_REQUEST_CHILD"
    final = profiles.resolve_tree([PROJECT, DIST, f"{DIST}/Working", f"{DIST}/Final", f"{DIST}/Final/Other",
                                   f"{DIST}/Final/CAE/C/not-an-id"], dist_rules())
    codes = {e["relative_path"]: (e["deviation"] or {}).get("code") for e in final}
    assert codes[f"{DIST}/Final/Other"] == "UNEXPECTED_FINAL_CHILD"
    assert codes[f"{DIST}/Final/CAE/C/not-an-id"] == "FINAL_VERSION_INVALID"
    assert {"UNEXPECTED_FINAL_CHILD", "FINAL_VERSION_INVALID"} <= profiles.WARNING_DEVIATIONS


@pytest.mark.unit
def test_legacy_final_reports_is_unexpected_and_project_cad_report_are_ignored():
    """§15 D21/D22."""
    final = f"{DIST}/Final"
    resolved = by_path(profiles.resolve_tree(
        [PROJECT, DIST, f"{DIST}/Working", final, f"{final}/Report", f"{final}/Reports", f"{final}/Reports/C",
         f"{PROJECT}/CAD", f"{PROJECT}/CAD/sub", f"{PROJECT}/report", f"{PROJECT}/REPORT/x"], dist_rules()))
    assert resolved[f"{final}/Report"]["role_kind"] == "FINAL_REPORTS" and not resolved[f"{final}/Report"]["deviation"]
    assert resolved[f"{final}/Reports"]["deviation"]["code"] == "UNEXPECTED_FINAL_CHILD"
    assert resolved[f"{final}/Reports"]["deviation"]["code"] in profiles.WARNING_DEVIATIONS
    assert resolved[f"{final}/Reports/C"]["status"] == "CONTENT" and not resolved[f"{final}/Reports/C"]["deviation"]
    assert not any(path.casefold().startswith((f"{PROJECT}/cad".casefold(), f"{PROJECT}/report".casefold()))
                   for path in resolved)
    assert [item["name"] for item in profiles.FINAL_BLOCK["children"]] == ["CAE", "Report", "CAD"]
    # A request folder merely containing the word is still a request.
    assert profiles.resolve_path([PROJECT, "[WR-0009]_[유통_Report]"], dist_rules())["role_kind"] == "REQUEST"


@pytest.mark.unit
def test_scene_subfolder_is_content_and_branch_incomplete_is_info_only():
    case = f"{USAGE}/Working/{USAGE_CASE}"
    resolved = by_path(profiles.resolve_tree([PROJECT, USAGE, f"{USAGE}/Working", case, f"{case}/Settle",
                                              f"{case}/Settle/images", f"{USAGE}/Working/EmptyCase"], usage_rules()))
    assert (resolved[f"{case}/Settle/images"]["status"], resolved[f"{case}/Settle/images"]["role_kind"],
            resolved[f"{case}/Settle/images"]["deviation"]) == ("CONTENT", None, None)
    assert resolved[f"{USAGE}/Working/EmptyCase"]["info"] == "BRANCH_INCOMPLETE"
    assert resolved[f"{USAGE}/Working/EmptyCase"]["deviation"] is None
    assert profiles.resolve_path([PROJECT, USAGE, "$RECYCLE", "x"], usage_rules()) is None
    assert profiles.resolve_path([PROJECT, "~tmp"], usage_rules()) is None


@pytest.mark.unit
def test_custom_upper_container_level():
    upper = {"levels": [{"level": 1, "role": "CONTAINER"}, {"level": 2, "role": "PROJECT"}, {"level": 3, "role": "REQUEST"}]}
    rules = dist_rules(upper=upper)
    assert profiles.resolve_path(["SPDM (Admin)"], rules)["role_kind"] == "CONTAINER"
    assert profiles.resolve_path(["SPDM (Admin)", *DIST.split("/"), "Working"], rules)["role_kind"] == "WORKING"


# ---- validation (§4.3) --------------------------------------------------------

def _definition(env="DISTRIBUTION", **overrides):
    rules = profiles.default_depth_rules(env, "dss-v")
    rules.update(overrides)
    return rules


@pytest.mark.unit
@pytest.mark.parametrize("upper", [
    {"levels": [{"level": 1, "role": "REQUEST"}, {"level": 2, "role": "PROJECT"}]},
    {"levels": [{"level": 1, "role": "PROJECT"}]},
    {"levels": [{"level": 1, "role": "PROJECT"}, {"level": 3, "role": "REQUEST"}]},
    {"levels": [{"level": 1, "role": "PROJECT"}, {"level": 2, "role": "REQUEST"}, {"level": 3, "role": "CONTAINER"}]},
    {"levels": [{"level": i + 1, "role": "CONTAINER"} for i in range(7)] + [{"level": 8, "role": "PROJECT"}, {"level": 9, "role": "REQUEST"}]},
    {"levels": [{"level": 1, "role": "PROJECT", "allowed_names": ["X"]}, {"level": 2, "role": "REQUEST"}]},
])
def test_invalid_upper_is_rejected(upper):
    with pytest.raises(ValueError):
        profiles.validate_rules("DISTRIBUTION", _definition(upper=upper))


@pytest.mark.unit
@pytest.mark.parametrize("env,levels", [
    ("DISTRIBUTION", ["WORKING", "SIMULATION_CASE", "LOAD_CASE", "EXECUTION_RUN", "SCENE"]),   # no RUN_OPTION
    ("USAGE", ["WORKING", "SIMULATION_CASE", "EVALUATION"]),
    ("USAGE", ["WORKING", "SIMULATION_CASE", "SCENE", "RESULTS"]),
    ("USAGE", ["WORKING", "SIMULATION_CASE", "RUN_OPTION", "SCENE"]),
    ("USAGE", ["SIMULATION_CASE", "WORKING", "SCENE"]),
    ("USAGE", ["WORKING", "SIMULATION_CASE", "CONTAINER"]),
])
def test_invalid_lower_is_rejected(env, levels):
    lower = {"levels": [{"level": i + 1, "role": role} for i, role in enumerate(levels)], "below_last": "CONTENT"}
    with pytest.raises(ValueError):
        profiles.validate_rules(env, _definition(env, lower=lower))


@pytest.mark.unit
def test_name_conditions_keyword_and_final_block():
    lower = json.loads(json.dumps(profiles.DEFAULT_LOWER["DISTRIBUTION"]))
    lower["levels"][4]["allowed_names"] = ["INDIVIDUAL", "CUMULATIVE"]
    with pytest.raises(ValueError, match="이름 조건"):
        profiles.validate_rules("DISTRIBUTION", _definition(lower=lower))
    with pytest.raises(ValueError):
        profiles.validate_rules("DISTRIBUTION", _definition(environment_keyword="사용"))
    tampered = profiles.validate_rules("DISTRIBUTION", _definition(final={"fixed": False, "children": []}))
    assert tampered["final"] == profiles.FINAL_BLOCK
    usage_with_container = {"levels": [{"level": 1, "role": "WORKING", "fixed_name": "Working"},
                                       {"level": 2, "role": "SIMULATION_CASE"}, {"level": 3, "role": "CONTAINER"},
                                       {"level": 4, "role": "SCENE"}], "below_last": "CONTENT"}
    assert profiles.validate_rules("USAGE", _definition("USAGE", lower=usage_with_container))["lower"] == usage_with_container


@pytest.mark.unit
def test_usage_sources_copy_rewrites_evaluation_references():
    copied = profiles.usage_sources_evaluation_to_scene(
        {"version": 1, "metric_paths": {"EVALUATION:Settle": ["EVALUATION", "x"], "Settle:y": ["EVALUATIONS"]}})
    assert copied["metric_paths"] == {"SCENE:Settle": ["SCENE", "x"], "Settle:y": ["EVALUATIONS"]}


# ---- schema sets, scans, registration (DuckDB integration) -------------------

@pytest.fixture(autouse=True)
def _restore_default_depth_schema(request):
    """PostgreSQL test runs share one database: put the default schema back after a test changed it."""
    yield
    if request.node.get_closest_marker("unit") is not None:
        return
    from app.services.folder_discovery import WRITE_LOCK
    from app.services.semantic_mapping import semantic_transaction
    with connect() as conn:
        current = profiles.get_depth_schema(conn)
        lowers = {env: item["lower"] for env, item in current["environments"].items()}
        if current["upper"] == profiles.DEFAULT_UPPER and lowers == profiles.DEFAULT_LOWER:
            return
        environments = {env: {"lower": profiles.DEFAULT_LOWER[env], "usage_sources": item["usage_sources"]}
                        for env, item in current["environments"].items()}
        with WRITE_LOCK, semantic_transaction(conn):
            profiles.save_depth_schema(conn, "test-restore", current["schema_set_id"], profiles.DEFAULT_UPPER, environments)



def _post(client, route, payload, status=200):
    response = client.post(route, json=payload)
    assert response.status_code == status, response.text
    return response.json()


def _build_distribution(root, *, final=True):
    scene = root / DIST / "Working" / DIST_CASE / DIST_TAIL / "2_Face"
    scene.mkdir(parents=True)
    (scene / CSV).write_bytes(CSV_BYTES)
    if final:
        for branch in ("CAE", "Report"):
            copy = root / DIST / "Final" / branch / DIST_CASE / FINAL_ID / DIST_TAIL
            copy.mkdir(parents=True)
            (copy / CSV).write_bytes(CSV_BYTES)
        (root / DIST / "Final" / "CAD").mkdir()
        meta = root / DIST / "Final" / ".finalizations" / FINAL_ID
        meta.mkdir(parents=True)
        (meta / "plan.json").write_text("{}", encoding="utf-8")


def _build_usage(root):
    case = root / USAGE / "Working" / USAGE_CASE
    for scene, files in USAGE_FILES.items():
        (case / scene).mkdir(parents=True)
        for name, payload in files.items():
            (case / scene / name).write_text(json.dumps(payload), encoding="utf-8")


def _register(client, environment, relative_path=""):
    scan = _post(client, ENV + "/scan", {"environment": environment, "relative_path": relative_path})
    preview = _post(client, ENV + "/previews", {"scan_id": scan["id"], "assignments": []})
    assert preview["can_apply"], preview
    registered = _post(client, ENV + "/registrations", {"preview_id": preview["id"], "idempotency_key": f"t-{uuid4()}",
                                                        "capture": True})
    return scan, preview, registered


@pytest.mark.duckdb_integration
def test_seeded_default_set_is_current_and_legacy_profiles_are_archived(admin_client):
    with connect() as conn:
        schema = profiles.get_depth_schema(conn)
        assert schema["upper"] == profiles.DEFAULT_UPPER
        assert schema["environments"]["DISTRIBUTION"]["lower"] == profiles.DEFAULT_LOWER["DISTRIBUTION"]
        assert schema["environments"]["USAGE"]["lower"] == profiles.DEFAULT_LOWER["USAGE"]
        legacy_rows = conn.execute("SELECT rules_json FROM folder_environment_profiles WHERE id LIKE '%-default'").fetchall()
        assert legacy_rows and all(_json(row[0])["profile_metadata"]["archived"] for row in legacy_rows)


@pytest.mark.duckdb_integration
def test_distribution_scan_preview_registers_working_only_and_final_is_not_captured(admin_client):
    client, root = admin_client
    _build_distribution(root)
    scan, preview, registered = _register(client, "DISTRIBUTION")
    nodes = by_path(scan["nodes"])
    assert all(node["role_basis"] == "DEPTH_SCHEMA" for node in nodes.values() if node["relative_path"])
    assert not [n for n in nodes.values() if n.get("deviation")]
    assert nodes[f"{DIST}/Final/CAE/{DIST_CASE}/{FINAL_ID}"]["role_kind"] == "FINAL_VERSION"
    assert nodes[f"{DIST}/Final/CAE/{DIST_CASE}/{FINAL_ID}/{DIST_TAIL}"]["role_kind"] == "RUN_OPTION"
    assert not [p for p in nodes if ".finalizations" in p]
    assert {(n["segment"], n["level"]) for n in [nodes[f"{DIST}/Working/{DIST_CASE}/{DIST_TAIL}/2_Face"]]} == {("LOWER", 6)}
    # Final rows are never business rows or capture jobs (D12).
    assert not [row for row in preview["rows"] if "/Final/" in row["relative_path"]]
    assert len(registered["capture_jobs"]) == 1 and registered["capture_jobs"][0]["status"] == "COMPLETED"
    with connect() as conn:
        manifest = conn.execute("SELECT manifest_json FROM dashboard_captures WHERE id=?",
                                [registered["capture_jobs"][0]["capture_id"]]).fetchone()[0]
    manifest = manifest if isinstance(manifest, str) else json.dumps(manifest, ensure_ascii=False)
    assert "/Final/" not in manifest and "2_Face" in manifest


@pytest.mark.duckdb_integration
def test_usage_case_registers_all_nine_scene_slots(admin_client):
    client, root = admin_client
    _build_usage(root)
    _scan, preview, registered = _register(client, "USAGE")
    assert {row["role_kind"] for row in preview["rows"]} >= {"SCENE", "SIMULATION_CASE"}
    job = registered["capture_jobs"][0]
    assert job["status"] == "COMPLETED", registered
    data = client.get(f"/api/dashboard/usage/cases/{job['case_id']}", params={"capture_id": job["capture_id"]}).json()
    assert data["status"] == "READY", data
    cells = [row[key] for row in data["evaluations"] for key in ("common", "front", "rear") if row.get(key)]
    assert len(cells) == 9 and all(cell["status"] == "READY" for cell in cells), data["evaluations"]


@pytest.mark.duckdb_integration
def test_blocking_deviation_cannot_apply_and_assignments_are_restricted(admin_client):
    client, root = admin_client
    (root / PROJECT / "[WR-0009]_[유통_환경]" / "Final").mkdir(parents=True)
    _build_distribution(root, final=False)
    scan = _post(client, ENV + "/scan", {"environment": "DISTRIBUTION", "relative_path": ""})
    missing = next(n for n in scan["nodes"] if n["name"] == "[WR-0009]_[유통_환경]")
    assert missing["deviation"]["code"] == "WORKING_MISSING"
    preview = _post(client, ENV + "/previews", {"scan_id": scan["id"], "assignments": []})
    assert preview["can_apply"] is False and preview["blocking_count"] == 1
    excluded = _post(client, ENV + "/previews", {"scan_id": scan["id"], "assignments": [
        {"node_id": missing["id"], "role_kind": "EXCLUDE", "confirm": True}]})
    assert excluded["can_apply"] is True
    scene = next(n for n in scan["nodes"] if n["name"] == "2_Face")
    for assignment in ({"node_id": scene["id"], "role_kind": "SCENE", "confirm": True},
                       {"node_id": scene["id"], "role_kind": "EXCLUDE", "confirm": True, "propagate_same_level": True}):
        response = client.post(ENV + "/previews", json={"scan_id": scan["id"], "assignments": [assignment]})
        assert response.status_code == 422 and response.json()["detail"]["code"] == "ASSIGNMENT_NOT_ALLOWED", response.text


@pytest.mark.duckdb_integration
def test_scan_ignores_requested_profile_id(admin_client):
    client, root = admin_client
    _build_distribution(root, final=False)
    scan = _post(client, ENV + "/scan", {"environment": "DISTRIBUTION", "relative_path": "",
                                         "profile_id": "environment-profile-distribution-default"})
    with connect() as conn:
        assert scan["profile_id"] == profiles.get_depth_schema(conn)["environments"]["DISTRIBUTION"]["profile_id"]


@pytest.mark.duckdb_integration
def test_save_creates_new_set_archives_previous_and_detects_conflict(admin_client):
    with connect() as conn:
        before = profiles.get_depth_schema(conn)
        environments = {env: {"environment_keyword": item["environment_keyword"], "lower": item["lower"],
                              "usage_sources": item["usage_sources"]} for env, item in before["environments"].items()}
        upper = {"levels": [{"level": 1, "role": "CONTAINER"}, {"level": 2, "role": "PROJECT"}, {"level": 3, "role": "REQUEST"}]}
        saved = profiles.save_depth_schema(conn, "admin", before["schema_set_id"], upper, environments)
        assert saved["schema_set_id"] != before["schema_set_id"] and saved["upper"] == upper
        assert {saved["environments"][e]["profile_id"] for e in saved["environments"]}.isdisjoint(
            {before["environments"][e]["profile_id"] for e in before["environments"]})
        for env in ("USAGE", "DISTRIBUTION"):
            old = _json(conn.execute("SELECT rules_json,revision FROM folder_environment_profiles WHERE id=?",
                                          [before["environments"][env]["profile_id"]]).fetchone()[0])
            assert old["profile_metadata"]["archived"] is True
            assert old["profile_metadata"]["superseded_by"] == saved["schema_set_id"]
            assert conn.execute("SELECT revision FROM folder_environment_profiles WHERE id=?",
                                [saved["environments"][env]["profile_id"]]).fetchone()[0] == 1
        with pytest.raises(HTTPException) as conflict:
            profiles.save_depth_schema(conn, "admin", before["schema_set_id"], upper, environments)
        assert conflict.value.status_code == 409 and conflict.value.detail["code"] == "DEPTH_SCHEMA_CONFLICT"
        with pytest.raises(ValueError):
            profiles.save_depth_schema(conn, "admin", saved["schema_set_id"], {"levels": []}, environments)


@pytest.mark.duckdb_integration
def test_schema_save_keeps_existing_refresh_until_reinterpret(admin_client):
    client, root = admin_client
    _build_distribution(root)
    _scan, _preview, registered = _register(client, "DISTRIBUTION")
    project_id, request_id = registered["project_id"], registered["request_id"]
    refresh = lambda: _post(client, ENV + "/refresh", {"project_id": project_id, "request_id": request_id,
                                                       "environment": "DISTRIBUTION"})
    first = refresh()
    roles_before = {n["relative_path"]: n["role_kind"] for n in first["nodes"]}
    with connect() as conn:
        current = profiles.get_depth_schema(conn)
        environments = {env: {"lower": item["lower"], "usage_sources": item["usage_sources"]}
                        for env, item in current["environments"].items()}
        environments["DISTRIBUTION"]["lower"] = {"levels": [
            {"level": 1, "role": "WORKING", "fixed_name": "Working"}, {"level": 2, "role": "SIMULATION_CASE"},
            {"level": 3, "role": "LOAD_CASE"}, {"level": 4, "role": "EXECUTION_RUN"},
            {"level": 5, "role": "RUN_OPTION"}, {"level": 6, "role": "CONTAINER"}, {"level": 7, "role": "SCENE"}],
            "below_last": "CONTENT"}
        saved = profiles.save_depth_schema(conn, "admin", current["schema_set_id"], current["upper"], environments)
    second = refresh()  # D9: no revision error and unchanged roles
    assert second["status"] in {"UNCHANGED", "REFRESHED"}
    assert {n["relative_path"]: n["role_kind"] for n in second["nodes"]} == roles_before
    with connect() as conn:
        result = fde.reinterpret_request(conn, _Principal(), request_id, root=root)
    assert result["registered"] is True and result["refresh_error"] is None, result
    third = refresh()
    scene = f"{DIST}/Working/{DIST_CASE}/{DIST_TAIL}/2_Face"
    assert {n["relative_path"]: n["role_kind"] for n in third["nodes"]}[scene] == "CONTAINER"
    with connect() as conn:
        snapshot = conn.execute("SELECT profile_id FROM folder_environment_scans WHERE id=?", [third["snapshot_id"]]).fetchone()
    assert snapshot[0] == saved["environments"]["DISTRIBUTION"]["profile_id"]


@pytest.mark.duckdb_integration
def test_reinterpret_with_blocking_deviation_registers_nothing(admin_client):
    client, root = admin_client
    _build_distribution(root, final=False)
    _scan, _preview, registered = _register(client, "DISTRIBUTION")
    (root / DIST / "Notes").mkdir()
    with connect() as conn:
        before = conn.execute("SELECT count(*) FROM folder_environment_registrations").fetchone()[0]
        result = fde.reinterpret_request(conn, _Principal(), registered["request_id"], root=root)
        after = conn.execute("SELECT count(*) FROM folder_environment_registrations").fetchone()[0]
    assert result["registered"] is False and before == after
    assert [d["code"] for d in result["deviations"]] == ["UNEXPECTED_REQUEST_CHILD"]


@pytest.mark.duckdb_integration
def test_samples_and_run_option_names_and_check(admin_client):
    _client, root = admin_client
    _build_distribution(root)
    other = root / PROJECT / "[WR-0003]_[유통_환경]" / "Working" / "Case" / "Drop" / "run"
    for name in ("ALL", "individual"):
        (other / name / "Scene").mkdir(parents=True)
    (root / PROJECT / "[WR-0004]_[유통_사용]" / "Working").mkdir(parents=True)
    # §15 D22: project-level CAD/Report folders are neither sampled nor checked.
    (root / PROJECT / "CAD" / "Working").mkdir(parents=True)
    (root / PROJECT / "Report").mkdir()
    with connect() as conn:
        samples = profiles.depth_schema_samples(conn, root, "DISTRIBUTION")
        names = {item["name"].casefold(): item for item in samples["run_option_names"]}
        assert set(names) == {"individual", "all"}
        assert (names["individual"]["count"], names["individual"]["request_count"]) == (2, 2)
        assert samples["requests_sampled"] == 2 and samples["levels"][0]["samples"][0]["name"] == "Working"
        upper = profiles.depth_schema_samples(conn, root, "UPPER")
        assert upper["levels"][0]["samples"] == [{"name": PROJECT, "count": 1}]
        assert {item["name"] for item in upper["levels"][1]["samples"]}.isdisjoint({"CAD", "Report"})
        schema = profiles.get_depth_schema(conn)
        draft = {env: {"lower": item["lower"]} for env, item in schema["environments"].items()}
        checked = profiles.depth_schema_check(conn, root, schema["upper"], draft)
    assert checked["by_code"] == {"ENV_KEYWORD_BOTH": 1}
    assert checked["examples"] == [{"relative_path": f"{PROJECT}/[WR-0004]_[유통_사용]", "code": "ENV_KEYWORD_BOTH"}]


class _Principal:
    user_id = "depth-schema-test"
    username = "depth-schema-test"
    display_name = "깊이 스키마 테스트"
    is_global_admin = True
    account_status = "ACTIVE"
