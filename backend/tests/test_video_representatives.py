"""Representative video per (Case, Scene), project keyword rule, unplayable-format files (issue #55, migration 0048)."""
from __future__ import annotations

import struct
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.database import initialize_database
from app.database_connection import connect
from app.main import app
from app.services import dashboard_capture, dashboard_queries as queries, project_cleanup, video_representatives as vr
from tests.test_dashboard_api_slice import _capture_body, _create_user, _login, _request_context

CSV = "MAX_RESULT_Max_Stress_P1 (major)_Mid_C23_scene.h3d.csv"
CSV_TEXT = "Position,Layer_1,Layer_2,Layer_3,Layer_4\nTOP,10,20,30,40\n"
SCENE = "1_Face_Drop_Scene01_Face1_1st"


def _mp4(fourcc: bytes, pad: int = 0) -> bytes:
    entry = struct.pack(">I", 86) + fourcc + b"\x00" * 78
    stsd = b"\x00\x00\x00\x00" + struct.pack(">I", 1) + entry
    moov = struct.pack(">I", 8 + len(stsd)) + b"stsd" + stsd
    return b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x00" * pad + moov


def _media(name, asset=None, kind="VIDEO"):
    return {"asset_id": asset or "a-" + name, "relative_path": f"case/Drop/run-a/INDIVIDUAL/{SCENE}/{name}", "kind": kind,
            "status": "READY", "component_id": None, "frame_role": "UNKNOWN"}


def _capture(names, *, unplayable=(), case_id="case-1"):
    scene = {"id": "s1", "label": SCENE, "scene_sequence_number": 1, "observations": [],
             "media": [_media(name) for name in names],
             **({"unplayable_videos": [{"relative_path": f"case/Drop/run-a/INDIVIDUAL/{SCENE}/{n}", "size_bytes": 1234,
                                        "format": n.rsplit('.', 1)[-1], "reason": "FORMAT"} for n in unplayable]} if unplayable else {})}
    run = {"id": "run-1", "source_name": "run-a", "load_case_id": "load-1", "load_case_name": "Drop", "mode": "INDIVIDUAL",
           "run_option_id": "opt-i", "option_label": "Individual", "option_status": "PRESENT", "scenes": [scene]}
    return {"id": "cap-1", "case_id": case_id, "environment": "DISTRIBUTION", "source_name": "case",
            "payload": {"context": {}, "runs": [run], "scenes": [scene], "quality_issues": []}}


def _key(capture):
    run = capture["payload"]["runs"][0]
    return vr.scene_key(run, run["scenes"][0])


# ---------------------------------------------------------------- pure rules

def test_default_is_first_file_name_natural_and_case_insensitive():
    capture = _capture(["view10.mp4", "View2.mp4", "b.mp4"])
    result = queries.run_videos(capture, "run-1", run_option_id="opt-i")
    representative = [v for v in result["videos"] if v["is_representative"]]
    assert [v["file_name"] for v in representative] == ["b.mp4"]   # b < view2 < view10
    assert representative[0]["representative_source"] == "DEFAULT"
    assert vr.natural_key("a2.mp4") < vr.natural_key("a10.mp4") < vr.natural_key("B.mp4")


def test_rule_beats_default_and_user_choice_beats_rule():
    capture = _capture(["a_first.mp4", "x_Mid_view.mp4", "z_Max_Stress.mp4"])
    key = _key(capture)
    rule = {"keywords": ["max_stress", "Mid"], "choices": {}}
    by_rule = queries.run_videos(capture, "run-1", run_option_id="opt-i", selection=rule)
    chosen = next(v for v in by_rule["videos"] if v["is_representative"])
    assert (chosen["file_name"], chosen["representative_source"]) == ("z_Max_Stress.mp4", "RULE")   # keyword order, not file order
    both = {"keywords": ["max_stress"], "choices": {key: {"relative_path": capture["payload"]["runs"][0]["scenes"][0]["media"][0]["relative_path"],
                                                             "chosen_by": "u1", "chosen_at": "2026-10-29T10:00:00"}}}
    by_user = queries.run_videos(capture, "run-1", run_option_id="opt-i", selection=both)
    chosen = next(v for v in by_user["videos"] if v["is_representative"])
    assert (chosen["file_name"], chosen["representative_source"]) == ("a_first.mp4", "USER")
    assert chosen["choice"]["chosen_by"] == "u1" and chosen["choice"]["chosen_at"] == "2026-10-29T10:00:00"


def test_choice_of_a_missing_file_falls_back_to_rule_then_default():
    capture = _capture(["a.mp4", "b_Mid.mp4"])
    key = _key(capture)
    gone = {"keywords": ["Mid"], "choices": {key: {"relative_path": "case/gone.mp4"}}}
    video = next(v for v in queries.run_videos(capture, "run-1", run_option_id="opt-i", selection=gone)["videos"] if v["is_representative"])
    assert (video["file_name"], video["representative_source"], video["choice_missing"]) == ("b_Mid.mp4", "RULE", True)
    video = next(v for v in queries.run_videos(capture, "run-1", run_option_id="opt-i",
                                               selection={"keywords": [], "choices": gone["choices"]})["videos"] if v["is_representative"])
    assert (video["file_name"], video["representative_source"]) == ("a.mp4", "DEFAULT")


def test_representative_only_scene_filter_and_unplayable_tiles():
    capture = _capture(["a.mp4", "b.mp4"], unplayable=["c.avi"])
    full = queries.run_videos(capture, "run-1", run_option_id="opt-i")
    assert [(v["file_name"], v["status"]) for v in full["videos"]] == [("a.mp4", "READY"), ("b.mp4", "READY"), ("c.avi", "UNPLAYABLE")]
    avi = full["videos"][2]
    assert avi["asset_id"] is None and avi["playable"] is False and avi["unplayable_reason"] == "FORMAT"
    assert avi["format"] == "avi" and avi["size_bytes"] == 1234 and avi["scene_video_count"] == 3
    only = queries.run_videos(capture, "run-1", run_option_id="opt-i", representative_only=True)
    assert [v["file_name"] for v in only["videos"]] == ["a.mp4"] and only["pagination"]["total_items"] == 1
    assert queries.run_videos(capture, "run-1", run_option_id="opt-i", scene_label=SCENE.lower())["pagination"]["total_items"] == 3
    assert queries.run_videos(capture, "run-1", run_option_id="opt-i", scene_id="other")["videos"] == []
    avi_only = _capture([], unplayable=["c.avi"])
    tile = queries.run_videos(avi_only, "run-1", run_option_id="opt-i", representative_only=True)["videos"]
    assert [v["file_name"] for v in tile] == ["c.avi"]
    scenes = queries.video_scenes(capture, "run-1", run_option_id="opt-i")["scenes"]
    assert scenes[0]["video_count"] == 2 and scenes[0]["unplayable_count"] == 1 and scenes[0]["representative_source"] == "DEFAULT"


def test_keyword_cleaning_and_codec_hint():
    assert vr.clean_keywords("Max_Stress, Mid\nmid ;  ") == ["Max_Stress", "Mid"]
    assert vr.clean_keywords(["a", "A", " b "]) == ["a", "b"]
    for bad in ([f"k{n}" for n in range(21)], ["a/b"], ["x" * 65]):
        with pytest.raises(vr.VideoRepresentativeError):
            vr.clean_keywords(bad)
    assert vr.codec_hint(_mp4(b"avc1")) == {"codec": "avc1", "playable": True}
    assert vr.codec_hint(_mp4(b"mp4v")) == {"codec": "mp4v", "playable": False}
    assert vr.codec_hint(_mp4(b"hvc1"))["playable"] is False
    assert vr.codec_hint(b"not an mp4") == {"codec": None, "playable": None}


# ---------------------------------------------------------------- walk + parser: avi listed, not stored

def test_capture_lists_unplayable_containers_without_storing_them(tmp_path: Path, monkeypatch):
    root = tmp_path / "shared"
    scene = root / "avi-case" / "Drop" / "run-a" / "INDIVIDUAL" / SCENE
    scene.mkdir(parents=True)
    (scene / CSV).write_text(CSV_TEXT, encoding="utf-8")
    (scene / "a.mp4").write_bytes(_mp4(b"avc1"))
    (scene / "b.avi").write_bytes(b"RIFF....AVI " + b"\x00" * 50)
    orphan = root / "avi-case" / "Drop" / "run-a" / "INDIVIDUAL" / "2_Only_Avi_Scene"
    orphan.mkdir()
    (orphan / "c.avi").write_bytes(b"RIFF")
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root))
    initialize_database()
    project_id, request_id = _request_context()
    with connect() as conn:
        created = dashboard_capture.create_capture(
            conn, {**_capture_body(project_id, request_id, "avi-case", "DISTRIBUTION"), "storage_root_id": dashboard_capture._root_id(dashboard_capture._provider(conn).root),
                   "simulation_case_id": "x"}, actor="t")
        capture = dashboard_capture.get_capture(conn, created["id"])
        assert conn.execute("SELECT count(*) FROM dashboard_assets WHERE capture_id=? AND relative_path LIKE '%.avi'", [created["id"]]).fetchone()[0] == 0
    scenes = capture["payload"]["runs"][0]["scenes"]
    assert [s["label"] for s in scenes] == [SCENE]   # the avi-only folder does not become a Scene
    assert scenes[0]["unplayable_videos"] == [{"relative_path": f"avi-case/Drop/run-a/INDIVIDUAL/{SCENE}/b.avi", "size_bytes": 62, "format": "avi", "reason": "FORMAT"}]
    # the existing 미처리 파일 notice for the avi stays as it was
    assert any("b.avi" in issue and "UNSUPPORTED_EXTENSION" in issue for issue in capture["payload"].get("quality_issues", []))


# ---------------------------------------------------------------- API: choice, rule, permissions, re-capture

def _setup(tmp_path, monkeypatch):
    root = tmp_path / "shared"
    scene = root / "rep-case" / "Drop" / "run-a" / "INDIVIDUAL" / SCENE
    scene.mkdir(parents=True)
    (scene / CSV).write_text(CSV_TEXT, encoding="utf-8")
    (scene / "A_view.mp4").write_bytes(_mp4(b"avc1", 10))
    (scene / "B_Max_Stress.mp4").write_bytes(_mp4(b"mp4v", 10))
    (scene / "C_Mid.mp4").write_bytes(_mp4(b"avc1", 300 * 1024))   # moov after the first 128 KiB: tail read
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root))
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "video-representative-test-secret-key-at-least-32-characters")
    initialize_database()
    vr.reset_probe_cache()
    return root, scene


def test_choice_rule_permissions_audit_and_recapture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root, scene = _setup(tmp_path, monkeypatch)
    project_id, request_id = _request_context()
    admin = _create_user(project_id, role="admin", global_admin=True)
    power = _create_user(project_id, role="power")
    general = _create_user(project_id, role="general")
    with TestClient(app) as client:
        h_admin, h_power, h_general = (_login(client, user) for user in (admin, power, general))
        body = _capture_body(project_id, request_id, "rep-case", "DISTRIBUTION")
        first = client.post("/api/dashboard/captures", headers=h_admin, json=body)
        assert first.status_code == 200, first.text
        case_id = first.json()["case_id"]
        latest = dashboard_capture.latest_capture_id(case_id)
        catalog = client.get("/api/dashboard/catalog", headers=h_admin, params={"request_id": request_id, "environment": "DISTRIBUTION"}).json()
        option = next(item for item in catalog["run_options"] if item["capture_id"] == latest)
        url = f"/api/dashboard/distribution/runs/{option['execution_run_id']}/videos"
        params = {"capture_id": latest, "run_option_id": option["id"]}

        def videos(headers=h_admin, **extra):
            response = client.get(url, headers=headers, params={**params, **extra})
            assert response.status_code == 200, response.text
            return response.json()

        listing = videos()
        by_name = {v["file_name"]: v for v in listing["videos"]}
        assert [v["file_name"] for v in listing["videos"] if v["is_representative"]] == ["A_view.mp4"]
        assert by_name["B_Max_Stress.mp4"]["playable"] is False and by_name["B_Max_Stress.mp4"]["unplayable_reason"] == "CODEC"
        assert by_name["B_Max_Stress.mp4"]["codec"] == "mp4v" and by_name["C_Mid.mp4"]["codec"] == "avc1"
        assert by_name["A_view.mp4"]["size_bytes"] > 0 and listing["case_id"] == case_id
        assert listing["video_rule"]["can_choose"] is True and listing["video_rule"]["can_edit_rule"] is True
        assert videos(headers=h_general)["video_rule"]["can_choose"] is False
        assert videos(headers=h_power)["video_rule"]["can_edit_rule"] is False
        scene_key = by_name["A_view.mp4"]["scene_key"]
        chosen_path = by_name["C_Mid.mp4"]["relative_path"]

        # permissions: general cannot choose; power (result.import) can; invalid files are refused
        payload = {"case_id": case_id, "scene_key": scene_key, "relative_path": chosen_path}
        assert client.put("/api/dashboard/distribution/video-representatives", headers=h_general, json=payload).status_code == 403
        assert client.put("/api/dashboard/distribution/video-representatives", headers=h_power, json={**payload, "relative_path": "rep-case/nope.mp4"}).status_code == 422
        assert client.put("/api/dashboard/distribution/video-representatives", headers=h_power, json={**payload, "scene_key": "x|y|z"}).status_code == 422
        saved = client.put("/api/dashboard/distribution/video-representatives", headers=h_power, json=payload)
        assert saved.status_code == 200, saved.text
        assert saved.json()["source"] == "USER" and saved.json()["relative_path"] == chosen_path and saved.json()["chosen_by"] == power
        again = videos()
        picked = [v for v in again["videos"] if v["is_representative"]]
        assert [v["file_name"] for v in picked] == ["C_Mid.mp4"] and picked[0]["representative_source"] == "USER"
        assert picked[0]["choice"]["chosen_by"] == power and picked[0]["choice"]["chosen_at"]
        assert [v["file_name"] for v in videos(representative_only="true")["videos"]] == ["C_Mid.mp4"]

        # the project rule: viewers read, only admins write; the user's choice still wins
        rule_url = f"/api/projects/{project_id}/video-rule"
        assert client.get(rule_url, headers=h_general).json()["keywords"] == []
        assert client.put(rule_url, headers=h_power, json={"keywords": ["Max_Stress"]}).status_code == 403
        assert client.put(rule_url, headers=h_admin, json={"keywords": ["a/b"]}).status_code == 422
        rule = client.put(rule_url, headers=h_admin, json={"keywords": ["Max_Stress", "Mid", "max_stress"]})
        assert rule.status_code == 200 and rule.json()["keywords"] == ["Max_Stress", "Mid"] and rule.json()["updated_by"] == admin
        assert [v["file_name"] for v in videos()["videos"] if v["is_representative"]] == ["C_Mid.mp4"]

        # reset: the rule decides now (Max_Stress before Mid)
        reset = client.delete("/api/dashboard/distribution/video-representatives", headers=h_general, params={"case_id": case_id, "scene_key": scene_key})
        assert reset.status_code == 403
        reset = client.delete("/api/dashboard/distribution/video-representatives", headers=h_power, params={"case_id": case_id, "scene_key": scene_key})
        assert reset.status_code == 200 and reset.json()["source"] == "RULE"
        assert reset.json()["relative_path"].endswith("B_Max_Stress.mp4")

        # the choice survives a re-capture (new capture and asset ids) and falls back when its file is gone
        client.put("/api/dashboard/distribution/video-representatives", headers=h_power, json=payload)
        (scene / "D_new.mp4").write_bytes(_mp4(b"avc1"))
        second = client.post("/api/dashboard/captures", headers=h_admin, json=body)
        assert second.status_code == 200 and second.json()["id"] != first.json()["id"]
        after = videos()
        assert {v["file_name"] for v in after["videos"]} >= {"D_new.mp4"}
        assert [(v["file_name"], v["representative_source"]) for v in after["videos"] if v["is_representative"]] == [("C_Mid.mp4", "USER")]
        (scene / "C_Mid.mp4").unlink()
        third = client.post("/api/dashboard/captures", headers=h_admin, json=body)
        assert third.status_code == 200
        fallback = [v for v in videos()["videos"] if v["is_representative"]]
        assert [(v["file_name"], v["representative_source"], v["choice_missing"]) for v in fallback] == [("B_Max_Stress.mp4", "RULE", True)]
        scenes = client.get(f"/api/dashboard/distribution/runs/{option['execution_run_id']}/video-scenes", headers=h_general, params=params).json()["scenes"]
        assert scenes[0]["scene_key"] == scene_key and scenes[0]["representative_source"] == "RULE"

        with connect() as conn:
            actions = [row[0] for row in conn.execute(
                "SELECT action FROM audit_events WHERE action IN ('DASHBOARD_VIDEO_REPRESENTATIVE_SET','DASHBOARD_VIDEO_REPRESENTATIVE_RESET','PROJECT_VIDEO_RULE_UPDATED')").fetchall()]
        assert actions.count("DASHBOARD_VIDEO_REPRESENTATIVE_SET") == 2 and "DASHBOARD_VIDEO_REPRESENTATIVE_RESET" in actions
        assert "PROJECT_VIDEO_RULE_UPDATED" in actions


# ---------------------------------------------------------------- schema + cleanup

def test_duckdb_bootstrap_adds_video_tables_to_empty_and_existing_databases():
    from app.config import database_settings

    if database_settings().backend != "duckdb":
        pytest.skip("DuckDB bootstrap only; PostgreSQL uses migration 0048")
    expected = {"case_scene_video_choices": {"id", "case_id", "scene_key", "relative_path", "chosen_by", "chosen_at"},
                "project_video_rules": {"project_id", "keywords_json", "updated_by", "updated_at"}}

    def columns(table):
        with connect() as conn:
            return {row[0] for row in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name=?", [table]).fetchall()}

    initialize_database()
    for table, wanted in expected.items():
        assert wanted <= columns(table)
    with connect() as conn:
        for table in expected:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.execute("INSERT INTO projects(id,name,product_name,description,created_at) VALUES "
                     "('video-upgrade-project','keep','p','d',CURRENT_TIMESTAMP) ON CONFLICT DO NOTHING")
    initialize_database()
    for table, wanted in expected.items():
        assert wanted <= columns(table)
    with connect() as conn:
        assert conn.execute("SELECT name FROM projects WHERE id='video-upgrade-project'").fetchone() == ("keep",)
    initialize_database()   # idempotent


def test_migration_0048_chain_and_tables():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    script = ScriptDirectory.from_config(config)
    revision = script.get_revision("0048_video_representatives")
    assert revision and revision.down_revision == "0047_dashboard_scene_summaries"
    assert tuple(script.get_heads()) == ("0048_video_representatives",)
    text = Path(revision.path).read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS case_scene_video_choices" in text and "CREATE TABLE IF NOT EXISTS project_video_rules" in text


def test_project_cleanup_removes_choices_and_rule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root, _scene = _setup(tmp_path, monkeypatch)
    project_id, request_id = _request_context()
    with connect() as conn:
        created = dashboard_capture.create_capture(
            conn, {**_capture_body(project_id, request_id, "rep-case", "DISTRIBUTION"),
                   "storage_root_id": dashboard_capture._root_id(dashboard_capture._provider(conn).root), "simulation_case_id": "x"}, actor="t")
        vr.save_choice(conn, created["case_id"], "run|opt|scene", "rep-case/A_view.mp4", "u")
        vr.save_rule(conn, project_id, ["Mid"], "u")
        preview = project_cleanup.preview(conn, [project_id], force_project_ids=[project_id])
        assert preview["items"][0]["counts"].get("case_scene_video_choices") == 1
        assert preview["items"][0]["counts"].get("project_video_rules") == 1
        project_cleanup.delete(conn, [project_id], preview["confirm_token"], acknowledged_project_ids=[project_id], force_project_ids=[project_id])
        assert conn.execute("SELECT count(*) FROM case_scene_video_choices WHERE case_id=?", [created["case_id"]]).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM project_video_rules WHERE project_id=?", [project_id]).fetchone()[0] == 0
