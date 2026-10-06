"""W6 folder name warnings on the Case results catalog (synthetic SPDM trees only)."""
from __future__ import annotations

from uuid import uuid4

import pytest

from app.database_connection import connect
from app.services.folder_name_warnings import _clusters, _similar
from tests.test_new_scene_registration import ENV, admin_client  # noqa: F401  (fixture)

pytestmark = pytest.mark.duckdb_integration

REQUEST = "75R9J_PV/[WR-0001]_[유통_환경]"
WORKING = f"{REQUEST}/Working"
CSV = "MAX_RESULT_Max_Stress_P1 (major)_Mid_C24_scene.h3d.csv"
CSV_BYTES = b"Position,Layer_1,Layer_2,Layer_3,Layer_4\nTOP,20,20,20,20\n"


def _option(case: str) -> str:
    return f"{WORKING}/{case}/Drop/85qn80h_ref/INDIVIDUAL"


def _post(client, route, payload):
    response = client.post(route, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _register(client, root, tree: dict[str, list[str]]):
    for case, scenes in tree.items():
        for scene in scenes:
            folder = root / _option(case) / scene
            folder.mkdir(parents=True)
            (folder / CSV).write_bytes(CSV_BYTES)
    scan = _post(client, ENV + "/scan", {"environment": "DISTRIBUTION", "relative_path": ""})
    preview = _post(client, ENV + "/previews", {"scan_id": scan["id"], "assignments": []})
    assert preview["can_apply"], preview
    registered = _post(client, ENV + "/registrations", {
        "preview_id": preview["id"], "idempotency_key": f"w6-{uuid4()}", "capture": False,
    })
    with connect() as conn:
        request_id = conn.execute("SELECT request_id FROM folder_environment_registrations WHERE id=?",
                                  [registered["registration_id"]]).fetchone()[0]
    response = client.get("/api/dashboard/catalog", params={
        "project_id": registered["project_id"], "request_id": request_id, "environment": "DISTRIBUTION"})
    assert response.status_code == 200, response.text
    catalog = response.json()
    assert catalog["folder_schema"]["status"] == "AVAILABLE", catalog["folder_schema"]
    return catalog


def _names(warning):
    return sorted(path.rsplit("/", 1)[-1] for path in warning["paths"])


def _assert_shape(warnings):
    for item in warnings:
        assert set(item) == {"kind", "severity", "message", "paths", "case_id", "run_option_id"}
        assert item["severity"] in {"warning", "info"}
        # Korean text only; internal codes never reach the message.
        assert not any(code in item["message"] for code in ("UNRESOLVED", "UNEXPECTED", "DEVIATION", "SCENE_NAME"))
        assert item["paths"]


def test_clean_tree_has_no_name_warnings(admin_client):
    client, root = admin_client
    catalog = _register(client, root, {"CaseA": ["2_Face", "4_Edge"], "CaseB": ["2_Face", "4_Edge"]})
    assert catalog["name_warnings"] == []


def test_sibling_scene_names_that_differ_only_by_case_or_suffix(admin_client):
    client, root = admin_client
    catalog = _register(client, root, {"CaseA": ["2_Face", "2_face", "4_Edge", "4_edge2"]})
    warnings = catalog["name_warnings"]
    _assert_shape(warnings)
    by_names = {tuple(_names(item)): item for item in warnings}
    face = by_names[("2_Face", "2_face")]
    edge = by_names[("4_Edge", "4_edge2")]
    assert (face["kind"], face["severity"]) == ("SCENE_NAME_CASE", "warning")
    assert (edge["kind"], edge["severity"]) == ("SCENE_NAME_SUFFIX", "warning")
    option_ids = {item["id"] for item in catalog["run_options"]}
    case_ids = {item["id"] for item in catalog["cases"]}
    assert face["run_option_id"] in option_ids and face["case_id"] in case_ids
    assert "같은 Run Option 아래" in face["message"]
    # Folders are never changed.
    assert sorted(p.name for p in (root / _option("CaseA")).iterdir()) == ["2_Face", "2_face", "4_Edge", "4_edge2"]


def test_scene_spelled_differently_between_cases(admin_client):
    client, root = admin_client
    catalog = _register(client, root, {"CaseA": ["2_Face", "6_Corner"], "CaseB": ["2_Face", "6_corner"]})
    warnings = catalog["name_warnings"]
    _assert_shape(warnings)
    assert [item["kind"] for item in warnings] == ["CASE_SCENE_MISMATCH"]
    mismatch = warnings[0]
    assert _names(mismatch) == ["6_Corner", "6_corner"]
    assert "CaseA" in mismatch["message"] and "CaseB" in mismatch["message"]


def test_unexpected_folder_under_run_option(admin_client):
    client, root = admin_client
    catalog = _register(client, root, {"CaseA": ["2_Face", "3_Face", "backup"]})
    warnings = catalog["name_warnings"]
    _assert_shape(warnings)
    assert [(item["kind"], item["severity"], _names(item)) for item in warnings] == [
        ("UNEXPECTED_FOLDER", "info", ["backup"])]
    assert "번호로 시작하지 않습니다" in warnings[0]["message"]


def test_unexpected_request_child_deviation_is_shown_in_korean():
    from app.services.folder_name_warnings import folder_name_warnings

    class Locations:
        locations: tuple = ()

    schema = {"nodes": [
        {"relative_path": f"{REQUEST}/temp", "status": "UNRESOLVED",
         "deviation": {"code": "UNEXPECTED_REQUEST_CHILD", "message": "x"}},
        {"relative_path": f"{WORKING}/CaseA/odd", "status": "UNRESOLVED", "deviation": None},
        {"relative_path": f"{WORKING}/CaseA/skip", "status": "EXCLUDED", "deviation": None},
    ]}
    warnings = folder_name_warnings(schema, Locations(), "root", "DISTRIBUTION")
    assert [item["paths"] for item in warnings] == [[f"{REQUEST}/temp"], [f"{WORKING}/CaseA/odd"]]
    assert "Working·Final 이외" in warnings[0]["message"]
    assert all("UNEXPECTED" not in item["message"] for item in warnings)


@pytest.mark.parametrize(("first", "second", "expected"), [
    ("4_Edge", "4_edge2", ("SCENE_NAME_SUFFIX", "warning")),
    ("4_Edge", "4_Edge2", ("SCENE_NAME_SUFFIX", "info")),
    ("2_Face", "2_face", ("SCENE_NAME_CASE", "warning")),
    ("2_Face", "2-Face", ("SCENE_NAME_CASE", "warning")),
    ("6_Corner", "6_Cornre", ("SCENE_NAME_SPELLING", "warning")),
    ("1_Face", "2_Face", None),
    ("1_Face", "11_Face", None),
    ("Scene1", "Scene2", None),
    ("2_Face", "4_Edge", None),
])
def test_similarity_rules(first, second, expected):
    assert _similar(first, second) == expected


def test_clusters_join_transitive_variants():
    assert _clusters(["4_Edge", "4_edge", "4_edge2", "5_Edge"]) == [
        ("SCENE_NAME_CASE", "warning", ["4_Edge", "4_edge", "4_edge2"])]
