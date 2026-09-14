import base64

import pytest
from fastapi.testclient import TestClient

from app.database_connection import connect
from app.main import app

pytestmark = pytest.mark.duckdb_integration


def pair(client, missing=False):
    item_ids = []
    for key in ("first", "second"):
        response = client.post("/api/semantic-mapping/items", json={"definition": {"key": key, "label": key, "kind": "scalar", "unit": "MPa"}})
        assert response.status_code == 201, response.text
        item_ids.append(response.json()["id"])
    recipe = client.post("/api/semantic-mapping/recipes", json={
        "name": "read", "definition": {"format": "csv", "mappings": [{"source": "S", "result_item_id": item_ids[0]}]},
        "sample_filename": "a.csv", "sample_content_base64": base64.b64encode(b"S\n120\n").decode(),
    })
    assert recipe.status_code == 201, recipe.text
    template = client.post("/api/semantic-mapping/templates", json={"name": "view", "definition": {"widgets": [{"id": "kpi", "type": "kpi", "item_ids": [item_ids[1 if missing else 0]]}]}})
    assert template.status_code == 201, template.text
    return {"recipe_id": recipe.json()["id"], "recipe_version": 1, "template_id": template.json()["id"], "template_version": 1}


def active_versions(payload):
    with connect() as conn:
        return [conn.execute(f"SELECT active_version FROM semantic_{kind}s WHERE id=?", [payload[f"{kind}_id"]]).fetchone()[0] for kind in ("recipe", "template")]


def test_bundle_activation_requires_sample_to_produce_all_widget_inputs():
    with TestClient(app) as client:
        payload = pair(client, missing=True)
        result = client.post("/api/semantic-mapping/activate-bundle", json=payload)
        assert result.status_code == 422, result.text
        assert result.json()["detail"]["code"] == "SEMANTIC_WIDGET_VALIDATION_FAILED"
        assert active_versions(payload) == [None, None]


def test_bundle_activation_is_atomic_and_detects_stale_active_versions():
    with TestClient(app) as client:
        payload = pair(client)
        result = client.post("/api/semantic-mapping/activate-bundle", json=payload)
        assert result.status_code == 200, result.text
        assert result.json()["widgets"][0]["data"][0]["value"] == 120
        assert active_versions(payload) == [1, 1]
        stale = client.post("/api/semantic-mapping/activate-bundle", json=payload)
        assert stale.status_code == 409
        assert active_versions(payload) == [1, 1]


def test_audit_failure_rolls_back_both_active_pointers(monkeypatch):
    from app.routers import semantic_activation
    with TestClient(app) as client:
        payload = pair(client)

        def fail_audit(**kwargs):
            raise RuntimeError("injected audit failure")

        monkeypatch.setattr(semantic_activation, "write_audit_event", fail_audit)
        with pytest.raises(RuntimeError, match="injected audit failure"):
            client.post("/api/semantic-mapping/activate-bundle", json=payload)
        assert active_versions(payload) == [None, None]
