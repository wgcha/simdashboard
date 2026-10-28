"""Issue #55, stage 2: DISTRIBUTION captures keep per-Scene summaries, not every csv value.

* the screens' numbers are the same as the old observation-based path (golden: a frozen copy of the
  0.3.2.4 algorithm on the old payload vs. the new path on the stored summaries);
* detail values are parsed on demand from the stored original csv bytes;
* old captures (observations in ``payload_json``) stay readable, also merged with new ones;
* a large synthetic Case stays small in the database;
* migration 0047's DuckDB parity and the capture-scoped cleanup.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.adapters.persistence.dashboard_schema import ensure_dashboard_schema
from app.database_connection import connect
from app.domains.dashboard import scene_summary
from app.domains.dashboard.units import stress_unit, unit_system
from app.services import dashboard_capture, dashboard_queries as queries
from app.services.dashboard_capture import (
    _distribution_payload, _json, _replace_asset_id, _root_id, _walk, attach_scene_values, create_capture,
    get_capture, get_latest_capture, merge_latest_payload,
)
from app.services.dashboard_queries import (
    EDGES, context_key, number, ordered_scenes, scene_label_text, scene_public, scene_sequence, select_run,
)

pytestmark = pytest.mark.duckdb_integration

CASE = "dist-case"
RUN = CASE + "/Drop/run/INDIVIDUAL"
LINE_SETS = [set(combo) for size in range(1, 5) for combo in itertools.combinations((1, 2, 3, 4), size)]
EDGE_SETS = [{"TOP"}, {"LEFT", "RIGHT"}, set(EDGES), set()]


# --- frozen copy of the 0.3.2.4 observation-based algorithm (golden reference) ----------------------------

def _old_observations(scene, component, basis, lines):
    return [o for o in scene.get("observations", []) if o.get("component_id") == component and o.get("basis") == basis
            and (o.get("kind") == "CORNER" or o.get("line_index") in lines)]


def _old_peak(values, *, complete=False, basis=None, scope=None):
    numeric = [o for o in values if number(o.get("value")) is not None]
    maximum = max((number(o["value"]) for o in numeric), default=None)
    locations = [o for o in numeric if number(o["value"]) == maximum]
    unit, unit_status = stress_unit(numeric)
    return {"value": maximum, "unit": unit, "unit_status": unit_status, "basis": basis, "scope": scope,
            "status": "MISSING" if maximum is None else "READY" if complete else "PARTIAL",
            "completeness": "COMPLETE" if complete else "PARTIAL", "locations": locations,
            "source_refs": [o.get("source_ref", {"row": o.get("row"), "column": o.get("column")}) for o in locations]}


def _old_scene_edges(scene, component, basis, lines, member_id):
    source = _old_observations(scene, component, basis, lines)
    result = []
    for edge, position in EDGES.items():
        vals = [o for o in source if o.get("kind") == "SIDE" and o.get("position") == position]
        complete = all(any(o.get("line_index") == line and number(o.get("value")) is not None for o in vals) for line in lines)
        complete = complete and bool(lines) and all(number(o.get("value")) is not None for o in vals)
        result.append({**_old_peak(vals, complete=complete, basis=basis, scope="SELECTED_EDGE_LINES"), "edge": edge,
                       "scene_id": scene["id"], "member_id": member_id})
    return result


def legacy_distribution(capture, run_id, mode, component, basis, edges, lines, run_option_id=None):
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
        values = _old_scene_edges(scene, component, basis, lines, member_id)
        result["edge_peaks"].extend(values)
        extracted = _old_observations(scene, component, basis, lines)
        if lines != {1, 2, 3, 4}:
            extracted = [o for o in extracted if o.get("kind") == "SIDE"]
        extracted_peak = _old_peak(
            extracted, basis=basis,
            scope="EXTRACTED_SIDES_AND_CORNERS" if lines == {1, 2, 3, 4} else "EXTRACTED_SELECTED_LINES")
        result["location_peaks"].append({**extracted_peak, "scene_id": scene["id"], "member_id": member_id})
        selected = [v for v in values if v["edge"] in edges]
        envelope = _old_peak(selected, complete=bool(selected) and all(v["status"] == "READY" for v in selected), basis=basis, scope="SELECTED_EDGES_ENVELOPE")
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


# --- fixtures ------------------------------------------------------------------------------------------

def _value(rng: random.Random) -> str:
    return rng.choice([str(rng.randint(0, 90)), f"{rng.uniform(0, 90):.3f}", "45", "45.0"])


def _layer_align(component: str, position: str, rows: int, rng: random.Random, *, constant: str | None = None) -> str:
    header = ["Node_id", "coord_x", "coord_y", "coord_z", "ref_coord",
              *(f"{component}_SIDE_{position}_L{line}" for line in (1, 2, 3, 4))]
    lines = [",".join(header)]
    for row in range(rows):
        values = [constant or _value(rng) for _ in range(4)]
        lines.append(",".join([f"N{row}", f"{row * 0.5}", "1.25", "0", f"{row}", *values]))
    return "\n".join(lines) + "\n"


def _write_scene(scene: Path, rng: random.Random, *, rows: int = 12, ties: bool = False, infix: str = "") -> None:
    scene.mkdir(parents=True, exist_ok=True)
    for position in ("TOP", "BOT", "LH", "RH"):
        (scene / f"Layer_Align_{infix}C23_{position}.csv").write_text(_layer_align("C23", position, rows, rng))
    for position in ("TOP", "LH"):   # C24 has two edges only: the others stay MISSING / incomplete
        constant = "0" if ties and position == "LH" else None   # 40 equal maxima: more than LOCATION_CAP
        (scene / f"Layer_Align_{infix}C24_{position}.csv").write_text(
            _layer_align("C24", position, 40 if constant else rows, rng, constant=constant))
    (scene / f"MAX_RESULT_{infix}C23.csv").write_text(
        "Position,Layer_1,Layer_2,Layer_3,Layer_4\n" + "".join(
            f"{p},{_value(rng)},{_value(rng)},{_value(rng)},{_value(rng)}\n" for p in ("TOP", "BOT", "LH", "RH")))
    for corner in ("TOP_LH", "BOT_RH"):
        (scene / f"Corner_Data_{infix}C23_CORNER_{corner}.csv").write_text(
            "Node_id,Value\n" + "".join(f"K{i},{_value(rng)}\n" for i in range(rows)))
    (scene / f"Corner_Max_Result_{infix}C23.csv").write_text(
        "Position,Max_Value\nTOP_LH,77\nTOP_RH,12.5\nBOT_LH,3\nBOT_RH,90\n")
    (scene / f"Layer_Align_{infix}C25_TOP.csv").write_text("bad,header\n1,2\n")   # SOURCE_PARSE_ERROR
    (scene / "unknown.csv").write_text("a,b\n1,2\n")                                  # ignored source
    (scene / "C23_contour.png").write_bytes(b"\x89PNG synthetic")


def _case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shared"
    rng = random.Random(55)
    _write_scene(root / RUN / "1_Face_Drop_Scene01", rng, ties=True)
    _write_scene(root / RUN / "2_Corner_Drop_Scene02", rng)
    _write_scene(root / RUN / "Edge_Drop", rng, rows=3)
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root))
    return root


def _payload(root: Path, connection, relative: str = CASE) -> dict:
    row = connection.execute("SELECT project_id,id FROM analysis_requests ORDER BY id LIMIT 1").fetchone()
    return {"project_id": str(row[0]), "request_id": str(row[1]), "simulation_case_id": None,
            "load_case_id": None, "execution_run_id": None, "run_display_name": None, "mode": None,
            "component_id": None, "storage_root_id": _root_id(root), "root_relative_path": relative,
            "environment": "DISTRIBUTION"}


def _old_format_payload(root: Path, relative: str, capture_id: str, context: dict) -> dict:
    """The payload 0.3.2.4 (dashboard-v3) stored for the same files: every value as an observation."""
    files = _walk(root, relative)
    parsed = {"environment": "DISTRIBUTION", "context": context,
              **_distribution_payload(relative, files, context, _root_id(root), scan_issues=[])}
    payload = {**parsed, "capture_id": capture_id, "status": "PARTIAL", "contract_version": 1}
    for path, _data, _media_type in files:
        _replace_asset_id(payload, path, "dashboard-asset-" + hashlib.sha256(f"{capture_id}:{path}".encode()).hexdigest()[:24])
    for run in payload["runs"]:
        for scene in run["scenes"]:
            for key in ("component_results", "peak", "edge_peaks", "sources"):
                scene.pop(key, None)
    payload.pop("scenes", None)
    payload.pop("run", None)
    return json.loads(_json(payload))


def _normal(value):
    return json.loads(json.dumps(value, sort_keys=True, default=str))


def _assert_same_numbers(new_capture: dict, old_capture: dict, run: dict) -> int:
    compared = 0
    for component in ("C23", "C24"):
        for basis in ("DETAIL", "REPORTED_SUMMARY"):
            for edges in EDGE_SETS:
                for lines in LINE_SETS:
                    args = (run["id"], run["mode"], component, basis, set(edges), set(lines), run["run_option_id"])
                    expected = _normal(legacy_distribution(old_capture, *args))
                    assert _normal(queries.distribution(new_capture, *args)) == expected, (component, basis, edges, lines)
                    # Old captures read through the new code give the same numbers too.
                    assert _normal(queries.distribution(old_capture, *args)) == expected
                    compared += 1
    return compared


# --- tests ---------------------------------------------------------------------------------------------

def test_summary_capture_shows_the_same_numbers_as_the_observation_path(tmp_path, monkeypatch):
    root = _case(tmp_path, monkeypatch)
    with connect() as conn:
        ensure_dashboard_schema(conn)
        created = create_capture(conn, _payload(root, conn), actor="test-actor")
        stored = conn.execute("SELECT recipe_version,payload_json FROM dashboard_captures WHERE id=?",
                              [created["id"]]).fetchone()
        rows = conn.execute("SELECT scene_id,observation_count,summary_json FROM dashboard_scene_summaries "
                            "WHERE capture_id=?", [created["id"]]).fetchall()
        new_capture = get_capture(conn, created["id"])
        attach_scene_values(conn, new_capture)
        _golden(root, created, stored, rows, new_capture)


def _golden(root, created, stored, rows, new_capture):
    assert stored[0] == "dashboard-v4"
    # Every Scene reads its summary; the tied group (> LOCATION_CAP) is only marked, not a Scene-wide fallback.
    assert all("_groups" in scene and "observations" not in scene for scene in new_capture["payload"]["runs"][0]["scenes"])
    header = json.loads(stored[1])
    scenes = [scene for run in header["runs"] for scene in run["scenes"]]
    assert len(rows) == len(scenes) == 3
    assert all("observations" not in scene and scene["values_storage"] == scene_summary.VALUES_STORAGE for scene in scenes)
    assert {source["status"] for scene in scenes for source in scene["sources"]} == {"READY", "SOURCE_PARSE_ERROR", "IGNORED"}
    assert all(source["asset_id"] for scene in scenes for source in scene["sources"])
    assert scenes[0]["value_components"] == ["C23", "C24"]
    # The 40 equal maxima of one group are more than LOCATION_CAP: only that group is marked truncated.
    assert any(json.loads(row[2])["groups"] and any(group.get("t") == 40 for group in json.loads(row[2])["groups"])
               for row in rows)
    assert created["inventory"]["observation_count"] == sum(int(row[1]) for row in rows) > 0

    old_capture = {**new_capture, "payload": {**_old_format_payload(root, CASE, created["id"], new_capture["payload"]["context"]),
                                              "context": new_capture["payload"]["context"]}}
    run = new_capture["payload"]["runs"][0]
    assert _assert_same_numbers(new_capture, old_capture, run) == 2 * 2 * len(EDGE_SETS) * len(LINE_SETS)

    with pytest.raises(dashboard_capture.DashboardCaptureError):   # a missing component fails on both paths
        queries.distribution(new_capture, run["id"], run["mode"], "C99", "DETAIL", {"TOP"}, {1}, run["run_option_id"])


def test_detail_values_are_parsed_on_demand_from_the_original_csv(tmp_path, monkeypatch):
    root = _case(tmp_path, monkeypatch)
    scene_summary.clear_cache()
    with connect() as conn:
        ensure_dashboard_schema(conn)
        created = create_capture(conn, _payload(root, conn), actor="test-actor")
        context = get_capture(conn, created["id"])["payload"]["context"]
        old_capture = {**get_capture(conn, created["id"]),
                       "payload": {**_old_format_payload(root, CASE, created["id"], context), "context": context}}
        run = old_capture["payload"]["runs"][0]
        points = 0
        for scene in run["scenes"]:
            for component, basis, position, lines in itertools.product(("C23", "C24"), ("DETAIL", "REPORTED_SUMMARY"),
                                                                       ("TOP", "BOT", "LH", "RH"), ({1, 2, 3, 4}, {2, 4})):
                capture = get_capture(conn, created["id"])
                with pytest.raises(dashboard_capture.DashboardCaptureError):   # never silently empty
                    queries.scene_detail(capture, scene["id"], run["id"], run["mode"], component, basis, lines, position,
                                         run["run_option_id"])
                attach_scene_values(conn, capture, run_id=run["id"], scene_id=scene["id"], detail=True,
                                    component_id=component)
                args = (scene["id"], run["id"], run["mode"], component, basis, lines, position, run["run_option_id"])
                detail = queries.scene_detail(capture, *args)
                assert _normal(detail) == _normal(queries.scene_detail(old_capture, *args))
                points += len(detail["line_points"]) + len(detail["corner_points"])
        assert points > 100
        # A missing summary row falls back to the csv files as well.
        conn.execute("DELETE FROM dashboard_scene_summaries WHERE capture_id=?", [created["id"]])
        fallback = attach_scene_values(conn, get_capture(conn, created["id"]), run_id=run["id"])
        args = (run["id"], run["mode"], "C23", "DETAIL", set(EDGES), {1, 2, 3, 4}, run["run_option_id"])
        assert _normal(queries.distribution(fallback, *args)) == _normal(legacy_distribution(old_capture, *args))


def test_old_format_captures_stay_readable_alone_and_merged_with_new_ones(tmp_path, monkeypatch):
    root = _case(tmp_path, monkeypatch)
    with connect() as conn:
        ensure_dashboard_schema(conn)
        created = create_capture(conn, _payload(root, conn), actor="test-actor")
        context = get_capture(conn, created["id"])["payload"]["context"]
        # An older dashboard-v3 capture of the same Case with one more Scene that the new capture lacks.
        legacy_id = "dashboard-capture-legacy-v3"
        legacy = _old_format_payload(root, CASE, legacy_id, {**context, "capture_id": legacy_id})
        extra = json.loads(json.dumps(legacy["runs"][0]["scenes"][0]))
        extra.update(id="scene-legacy-extra", label="9_Old_Scene", source_name="9_Old_Scene")
        legacy["runs"][0]["scenes"].append(extra)
        conn.execute("INSERT INTO dashboard_captures(id,case_id,fingerprint,recipe_version,manifest_json,payload_json,"
                     "created_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
                     [legacy_id, created["case_id"], "legacy-fingerprint", "dashboard-v3", "[]", _json(legacy),
                      "test-actor", datetime(2020, 1, 1) + timedelta(seconds=1)])
        alone = attach_scene_values(conn, get_capture(conn, legacy_id))   # nothing to load: observations inline
        latest = attach_scene_values(conn, get_latest_capture(conn, created["case_id"]))
        new_as_old = _old_format_payload(root, CASE, created["id"], context)
        _merged_checks(alone, latest, legacy, legacy_id, created, new_as_old)


def _merged_checks(alone, latest, legacy, legacy_id, created, new_as_old):
    run = alone["payload"]["runs"][0]
    args = (run["id"], run["mode"], "C23", "DETAIL", {"TOP", "LEFT"}, {1, 3}, run["run_option_id"])
    assert _normal(queries.distribution(alone, *args)) == _normal(legacy_distribution(alone, *args))
    assert len(queries.distribution(alone, *args)["scenes"]) == 4

    merged_scenes = latest["payload"]["runs"][0]["scenes"]
    assert {scene["source_capture_id"] for scene in merged_scenes} == {legacy_id, created["id"]}
    expected = {**latest, "payload": {**merge_latest_payload([(legacy_id, legacy), (created["id"], new_as_old)]),
                                      "context": latest["payload"]["context"]}}
    for component, lines in (("C23", {1, 2, 3, 4}), ("C24", {1})):
        args = (run["id"], run["mode"], component, "DETAIL", set(EDGES), lines, run["run_option_id"])
        result = queries.distribution(latest, *args)
        assert len(result["scenes"]) == 4
        assert _normal(result) == _normal(legacy_distribution(expected, *args))
    catalog_components = {o.get("component_id") for scene in merged_scenes
                          for o in scene.get("observations", [])} | set(merged_scenes[0].get("value_components") or [])
    assert {"C23", "C24"} <= catalog_components


def test_large_case_stays_small_in_the_database(tmp_path, monkeypatch):
    """Many Scenes x csv files with ~250-character paths: header and summary rows stay bounded."""
    root = tmp_path / "shared"
    rng = random.Random(7)
    long_run = CASE + "/Drop/" + "R" * 120 + "/INDIVIDUAL"
    scenes = 40
    for index in range(scenes):
        _write_scene(root / long_run / f"{index + 1:02d}_Face_Drop_{'S' * 60}", rng, rows=150,
                     infix="Max_Stress_P1_major_Mid_")
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root))
    with connect() as conn:
        ensure_dashboard_schema(conn)
        created = create_capture(conn, _payload(root, conn), actor="test-actor")
        header_bytes = len(conn.execute("SELECT payload_json FROM dashboard_captures WHERE id=?",
                                        [created["id"]]).fetchone()[0].encode())
        rows = conn.execute("SELECT length(summary_json),observation_count FROM dashboard_scene_summaries "
                            "WHERE capture_id=?", [created["id"]]).fetchall()
        csv_bytes = conn.execute("SELECT sum(octet_length(content)) FROM dashboard_assets WHERE capture_id=? "
                                 "AND relative_path LIKE '%.csv'", [created["id"]]).fetchone()[0]
        context = get_capture(conn, created["id"])["payload"]["context"]
    old_bytes = len(_json(_old_format_payload(root, CASE, created["id"], context)).encode())
    summary_bytes = sum(int(row[0]) for row in rows)
    observations = sum(int(row[1]) for row in rows)
    print(f"\nlarge case: scenes={scenes} csv={csv_bytes}B observations={observations} "
          f"old_payload={old_bytes}B new_header={header_bytes}B summaries={summary_bytes}B")
    assert len(rows) == scenes and observations > 150_000
    assert old_bytes > 20 * csv_bytes                       # the old format: ~27x the csv size
    assert header_bytes < csv_bytes and header_bytes < 512 * 1024
    assert max(int(row[0]) for row in rows) < 64 * 1024     # no single-row size cliff
    assert header_bytes + summary_bytes < old_bytes / 20
    assert created["inventory"]["payload_bytes"] == header_bytes
    inventory = created["inventory"]
    assert inventory["summary_rows"] == scenes and inventory["summary_bytes"] == summary_bytes
    assert inventory["summary_max_bytes"] == max(int(row[0]) for row in rows)


def test_usage_payload_is_unchanged_and_small(tmp_path, monkeypatch):
    root = tmp_path / "shared"
    target = root / "usage-case" / "Settle"
    target.mkdir(parents=True)
    (target / "model_settle_result.json").write_text(json.dumps({"Set Tilt Angle @ Settle (deg)": 1.5}))
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root))
    with connect() as conn:
        ensure_dashboard_schema(conn)
        created = create_capture(conn, {**_payload(root, conn, "usage-case"), "environment": "USAGE"}, actor="test-actor")
        recipe, = conn.execute("SELECT recipe_version FROM dashboard_captures WHERE id=?", [created["id"]]).fetchone()
        summaries = conn.execute("SELECT count(*) FROM dashboard_scene_summaries WHERE capture_id=?",
                                 [created["id"]]).fetchone()[0]
    assert recipe == "dashboard-v3" and summaries == 0
    assert created["inventory"]["payload_bytes"] < 16 * 1024


def test_duckdb_bootstrap_adds_scene_summaries_to_empty_and_existing_databases():
    """DuckDB parity of migration 0047 (additive only)."""
    from app.config import database_settings
    from app.database import initialize_database

    if database_settings().backend != "duckdb":
        pytest.skip("DuckDB bootstrap only; PostgreSQL uses migration 0047")
    expected = {"id", "capture_id", "scene_id", "run_id", "run_option_id", "format_version", "observation_count",
                "summary_json", "created_at"}

    def columns():
        with connect() as conn:
            return {row[0] for row in conn.execute("SELECT column_name FROM information_schema.columns "
                                                   "WHERE table_name='dashboard_scene_summaries'").fetchall()}

    assert expected <= columns()   # a fresh (seeded) database has the table
    with connect() as conn:
        conn.execute("DROP TABLE IF EXISTS dashboard_scene_summaries")
        conn.execute("INSERT INTO projects(id,name,product_name,description,created_at) VALUES "
                     "('scene-summary-upgrade-project','keep','p','d',CURRENT_TIMESTAMP) ON CONFLICT DO NOTHING")
        before = conn.execute("SELECT count(*) FROM dashboard_captures").fetchone()[0]
    initialize_database()
    assert expected <= columns()
    with connect() as conn:
        assert conn.execute("SELECT name FROM projects WHERE id='scene-summary-upgrade-project'").fetchone() == ("keep",)
        assert conn.execute("SELECT count(*) FROM dashboard_captures").fetchone()[0] == before
    initialize_database()   # idempotent
    assert expected <= columns()


def test_project_cleanup_removes_scene_summaries_with_their_capture(tmp_path, monkeypatch):
    from app.services import project_cleanup

    root = _case(tmp_path, monkeypatch)
    with connect() as conn:
        ensure_dashboard_schema(conn)
        payload = _payload(root, conn)
        created = create_capture(conn, payload, actor="test-actor")
        assert conn.execute("SELECT count(*) FROM dashboard_scene_summaries WHERE capture_id=?",
                            [created["id"]]).fetchone()[0] == 3
        preview = project_cleanup.preview(conn, [payload["project_id"]], force_project_ids=[payload["project_id"]])
        assert preview["items"][0]["counts"].get("dashboard_scene_summaries") == 3
        project_cleanup.delete(conn, [payload["project_id"]], preview["confirm_token"],
                               acknowledged_project_ids=[payload["project_id"]],
                               force_project_ids=[payload["project_id"]])
        assert conn.execute("SELECT count(*) FROM dashboard_scene_summaries WHERE capture_id=?",
                            [created["id"]]).fetchone()[0] == 0


def test_truncated_group_reparses_only_when_its_peak_is_requested(tmp_path, monkeypatch):
    root = _case(tmp_path, monkeypatch)
    loads = []
    original = dashboard_capture.scene_observations
    monkeypatch.setattr(dashboard_capture, "scene_observations",
                        lambda conn, scene, component_id=None: loads.append(scene["id"]) or original(conn, scene, component_id))
    with connect() as conn:
        ensure_dashboard_schema(conn)
        created = create_capture(conn, _payload(root, conn), actor="test-actor")
        capture = attach_scene_values(conn, get_capture(conn, created["id"]))
        run = capture["payload"]["runs"][0]
        base = (run["id"], run["mode"])
        for basis in ("DETAIL", "REPORTED_SUMMARY"):
            queries.distribution(capture, *base, "C23", basis, set(EDGES), {1, 2, 3, 4}, run["run_option_id"])
        queries.distribution(capture, *base, "C24", "REPORTED_SUMMARY", set(EDGES), {1}, run["run_option_id"])
        assert loads == [], "groups without truncated ties never read csv files"
        # Every C24 DETAIL view shows the LEFT (LH) edge peak, which the tied group decides.
        result = queries.distribution(capture, *base, "C24", "DETAIL", {"LEFT"}, {1, 2, 3, 4}, run["run_option_id"])
        queries.distribution(capture, *base, "C24", "DETAIL", {"TOP"}, {2}, run["run_option_id"])
    tied = next(peak for peak in result["edge_peaks"] if peak["edge"] == "LEFT" and peak["value"] == 0)
    assert len(tied["locations"]) == 160 and len(loads) == 1   # 40 rows x 4 lines, one Scene parsed once


def test_unchanged_v3_case_is_not_captured_again_after_the_upgrade(tmp_path, monkeypatch):
    """The fingerprint recipe stays dashboard-v3: an existing capture keeps serving an unchanged Case."""
    root = _case(tmp_path, monkeypatch)
    with connect() as conn:
        ensure_dashboard_schema(conn)
        payload = _payload(root, conn)
        created = create_capture(conn, payload, actor="test-actor")
        # Turn the row into what 0.3.2.4 stored for the same files (v3, observations inline, no summaries).
        context = get_capture(conn, created["id"])["payload"]["context"]
        conn.execute("UPDATE dashboard_captures SET recipe_version='dashboard-v3',payload_json=? WHERE id=?",
                     [_json(_old_format_payload(root, CASE, created["id"], context)), created["id"]])
        conn.execute("DELETE FROM dashboard_scene_summaries WHERE capture_id=?", [created["id"]])
        before = get_latest_capture(conn, created["case_id"])["fingerprint"]
        again = create_capture(conn, payload, actor="test-actor")
        assert again["id"] == created["id"] and again["inventory"]["outcome"] == "UNCHANGED"
        assert conn.execute("SELECT count(*) FROM dashboard_captures WHERE case_id=?", [created["case_id"]]).fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM dashboard_scene_summaries").fetchone()[0] == 0
        assert get_latest_capture(conn, created["case_id"])["fingerprint"] == before   # finalization identity
        assert conn.execute("SELECT recipe_version FROM dashboard_captures WHERE id=?", [created["id"]]).fetchone() == ("dashboard-v3",)
        # A changed Case is captured as v4.
        (root / RUN / "Edge_Drop" / "Corner_Max_Result_C23.csv").write_text("Position,Max_Value\nTOP_LH,1\n")
        changed = create_capture(conn, payload, actor="test-actor")
        assert changed["id"] != created["id"]
        assert conn.execute("SELECT recipe_version FROM dashboard_captures WHERE id=?", [changed["id"]]).fetchone() == ("dashboard-v4",)
