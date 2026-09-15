"""Stored empty definitions must remain reusable when later files contain values."""
import base64
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app

pytestmark = pytest.mark.duckdb_integration


def test_empty_vector_configuration_activates_and_imports_later_values_without_redefining():
    with TestClient(app) as client:
        definition = {"key": "total_cg", "label": "Total CG Coord. @1deg", "kind": "vector", "data_type": "FLOAT", "unit": "mm", "dimensions": [], "components": ["X", "Y", "Z"]}
        result = client.post("/api/semantic-mapping/items", json={"definition": definition})
        assert result.status_code == 201, result.text
        entry = result.json()
        recipe = {"reader_version": 2, "format": "json", "input_layout": "json_object", "mappings": [{"result_item_id": entry["id"], "source": "#/coord", "missing": "preserve"}]}
        widgets = [{"id": "table", "type": "table", "item_ids": [entry["id"]]}, {"id": "y", "type": "kpi", "item_ids": [entry["id"]], "vector_component": "Y", "display_unit": "m"}]
        result = client.post("/api/semantic-mapping/configurations", json={
            "recipe": {"name": "Nullable vector", "definition": recipe, "sample_filename": "sample.json", "sample_content_base64": base64.b64encode(b'{"coord":[null,null,null]}').decode()},
            "template": {"name": "Vector views", "definition": {"widgets": widgets}},
        })
        assert result.status_code == 201, result.text
        configuration = result.json()
        bundle = {"recipe_id": configuration["recipe"]["id"], "recipe_version": 1, "template_id": configuration["template"]["id"], "template_version": 1, "expected_recipe_active_version": None, "expected_template_active_version": None}
        impact = client.post("/api/semantic-mapping/impact-bundle", json=bundle)
        assert impact.status_code == 200 and impact.json()["activation_allowed"], impact.text
        activated = client.post("/api/semantic-mapping/activate-bundle", json=bundle)
        assert activated.status_code == 200, activated.text
        changed = client.post("/api/semantic-mapping/items", json={"id": entry["id"], "expected_version": 1, "definition": {**definition, "components": ["Y", "X", "Z"]}})
        assert changed.status_code == 422 and changed.json()["detail"]["code"] == "SEMANTIC_ITEM_MEANING_IMMUTABLE", changed.text
        run_ids = []
        for values in ([None, None, None], [-.008, -46.656, 3.649]):
            imported = client.post("/api/semantic-mapping/import", data={"recipe_id": bundle["recipe_id"], "template_id": bundle["template_id"], "load_case_id": "loadcase-drop-bottom-001"}, files={"file": ("sample.json", json.dumps({"coord": values}).encode(), "application/json")})
            assert imported.status_code == 200, imported.text
            run_ids.append(imported.json()["run_id"])
        for index, run_id in enumerate(run_ids):
            result = client.get("/api/semantic-mapping/results", params={"load_case_id": "loadcase-drop-bottom-001", "run_id": run_id})
            assert result.status_code == 200, result.text
            displayed = result.json()["widgets"]
            assert displayed[1]["status"] == ("NO_VALUE" if index == 0 else "READY")
            assert displayed[0]["data"][0]["components"] == ["X", "Y", "Z"]
            assert displayed[0]["data"][0]["value"] == ([None, None, None] if index == 0 else [-.008, -46.656, 3.649])
            if index == 1:
                assert displayed[1]["data"][0]["value"] == pytest.approx(-.046656)
