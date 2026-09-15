"""Stored-snapshot impact preview and publication guard contracts."""
import base64
import json
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.database_connection import connect
from app.main import app

from test_semantic_activation import active_versions, pair


pytestmark = pytest.mark.duckdb_integration


def _save_display_only_recipe(client: TestClient, payload: dict) -> int:
    catalog = client.get("/api/semantic-mapping/catalog").json()
    recipe = next(entry for entry in catalog["recipes"] if entry["id"] == payload["recipe_id"])
    response = client.post("/api/semantic-mapping/recipes", json={
        "id": payload["recipe_id"], "expected_version": recipe["latest_version"], "name": recipe["name"],
        "definition": {**recipe["definition"], "title": "display-only"}, "sample_filename": "a.csv",
        "sample_content_base64": base64.b64encode(b"S\n120\n").decode(),
    })
    assert response.status_code == 201, response.text
    return response.json()["version"]


def test_impact_preview_is_read_only_and_marks_display_only_recipe_change():
    with TestClient(app) as client:
        payload = pair(client)
        assert client.post("/api/semantic-mapping/activate-bundle", json=payload).status_code == 200
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with connect() as conn:
            conn.execute(
                "INSERT INTO semantic_import_provenance VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ["past-run", "past-load-case", payload["recipe_id"], 1, payload["template_id"], 1,
                 "saved.csv", "a" * 64, "[]", None, now],
            )
        version = _save_display_only_recipe(client, payload)
        candidate = {**payload, "recipe_version": version, "expected_recipe_active_version": 1, "expected_template_active_version": 1}
        with connect() as conn:
            before_runs = conn.execute("SELECT count(*) FROM semantic_import_provenance").fetchone()[0]
        response = client.post("/api/semantic-mapping/impact-bundle", json=candidate)
        assert response.status_code == 200, response.text
        impact = response.json()
        assert impact["recipe_change"]["classification"] == "PRESENTATION_ONLY"
        assert impact["validation"]["sample_coverage"]["source"] == "STORED_RECIPE_SAMPLES_ONLY"
        assert impact["protected_prior_runs"]["immutable"] is True
        assert impact["protected_prior_runs"]["count"] == 1
        assert active_versions(payload) == [1, 1]
        with connect() as conn:
            assert conn.execute("SELECT count(*) FROM semantic_import_provenance").fetchone()[0] == before_runs


def test_impact_endpoint_requires_catalog_permission(monkeypatch):
    from app.routers import semantic_impact

    def denied(*_args, **_kwargs):
        raise HTTPException(403, "denied")

    monkeypatch.setattr(semantic_impact, "require_permission", denied)
    with TestClient(app) as client:
        response = client.post("/api/semantic-mapping/impact-bundle", json={
            "recipe_id": "recipe", "recipe_version": 1, "template_id": "template", "template_version": 1,
        })
    assert response.status_code == 403


def test_activation_blocks_incompatible_other_active_bound_template():
    with TestClient(app) as client:
        payload = pair(client)
        assert client.post("/api/semantic-mapping/activate-bundle", json=payload).status_code == 200
        catalog = client.get("/api/semantic-mapping/catalog").json()
        second_item = next(entry["id"] for entry in catalog["items"] if entry["name"] == "second")
        other = client.post("/api/semantic-mapping/templates", json={
            "name": "other", "definition": {"widgets": [{"id": "other", "type": "kpi", "item_ids": [second_item]}]},
        })
        assert other.status_code == 201, other.text
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with connect() as conn:
            conn.execute("UPDATE semantic_templates SET active_version=1, updated_at=? WHERE id=?", [now, other.json()["id"]])
            conn.execute("UPDATE semantic_template_versions SET lifecycle_status='ACTIVE' WHERE template_id=? AND version=1", [other.json()["id"]])
            conn.execute(
                "INSERT INTO semantic_folder_bindings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ["bound-other", "project-for-impact", None, None, "impact-test", "PROJECT", json.dumps([payload["recipe_id"]]), other.json()["id"], now, now, "test", 1],
            )
        version = _save_display_only_recipe(client, payload)
        candidate = {**payload, "recipe_version": version, "expected_recipe_active_version": 1, "expected_template_active_version": 1}
        preview = client.post("/api/semantic-mapping/impact-bundle", json=candidate)
        assert preview.status_code == 200, preview.text
        assert preview.json()["activation_allowed"] is False
        assert any(check["reason_code"] == "WIDGET_INPUT_INVALID" for check in preview.json()["validation"]["checks"])
        blocked = client.post("/api/semantic-mapping/activate-bundle", json=candidate)
        assert blocked.status_code == 422, blocked.text
        assert blocked.json()["detail"]["code"] == "SEMANTIC_ACTIVATION_IMPACT_BLOCKED"
        assert active_versions(payload) == [1, 1]
