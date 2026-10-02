"""Case results and materials catalogs share one Folder Schema hierarchy.

The two tabs share URL keys (case, case_load, case_run, case_option). This only
works when both catalogs project identical ids for the same folders.
"""
from __future__ import annotations

import pytest

from tests.test_new_scene_registration import OPTION, CSV, CSV_BYTES, _refresh, _seed, admin_client  # noqa: F401

pytestmark = pytest.mark.duckdb_integration
KEYS = ("case_id", "load_case_id", "execution_run_id", "run_option_id")


def _catalogs(client, project_id, request_id):
    dash = client.get("/api/dashboard/catalog", params={"project_id": project_id, "request_id": request_id,
                                                        "environment": "DISTRIBUTION"})
    mats = client.get("/api/materials/catalog", params={"request_id": request_id, "environment": "DISTRIBUTION"})
    assert dash.status_code == 200, dash.text
    assert mats.status_code == 200, mats.text
    return dash.json(), mats.json()


def test_materials_and_case_results_share_hierarchy_ids(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    # A second Run option branch with a Scene of the same name.
    other = root / OPTION.replace("INDIVIDUAL", "STACK") / "2_Face"
    other.mkdir(parents=True)
    (other / CSV).write_bytes(CSV_BYTES)
    _refresh(client, project_id, request_id)
    dash, mats = _catalogs(client, project_id, request_id)

    dash_scenes = {s["relative_path"]: s for s in dash["scenes"] if s.get("capture_id") is None}
    assert mats["scenes"], mats
    for scene in mats["scenes"]:
        expected = dash_scenes[scene["relative_path"]]
        assert tuple(scene[key] for key in KEYS) == tuple(expected[key] for key in KEYS), scene["relative_path"]
        assert all(scene[key] for key in ("case_id", "load_case_id", "execution_run_id", "run_option_id"))

    def ids(items):
        return {item["id"] for item in items if item.get("capture_id") is None}

    hierarchy = mats["hierarchy"]
    assert {c["id"] for c in hierarchy["cases"]} <= {c["id"] for c in dash["cases"]}
    for key in ("load_cases", "execution_runs", "run_options"):
        assert ids(hierarchy[key]) == ids(dash[key]), key
    # Same Scene name under two Run options stays two distinct selections.
    same_name = [s for s in mats["scenes"] if s["label"] == "2_Face"]
    assert len({s["run_option_id"] for s in same_name}) == len(same_name) >= 2


def test_every_scene_option_is_listed_in_hierarchy(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    dash, mats = _catalogs(client, project_id, request_id)
    option_ids = {o["id"] for o in mats["hierarchy"]["run_options"]}
    assert all(scene["run_option_id"] in option_ids for scene in mats["scenes"])
