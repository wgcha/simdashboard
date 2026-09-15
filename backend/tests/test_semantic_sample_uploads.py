from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.database_connection import connect
from app.main import app
from app.services import semantic_sample_uploads as uploads


def test_sample_upload_ownership_expiry_and_integrity(tmp_path, monkeypatch):
    monkeypatch.setattr(uploads, "_root", lambda: tmp_path)
    ident = uploads.save("owner", "result.json", b'{"value":0}')
    assert uploads.load("owner", ident) == ("result.json", b'{"value":0}')
    with pytest.raises(HTTPException) as error:
        uploads.delete("other", ident)
    assert error.value.status_code == 404
    metadata, sample = uploads._paths(ident)
    sample.write_bytes(b"changed")
    with pytest.raises(HTTPException) as error:
        uploads.load("owner", ident)
    assert error.value.status_code == 409
    value = json.loads(metadata.read_text())
    value["expires_at"] = 0
    metadata.write_text(json.dumps(value))
    with pytest.raises(HTTPException) as error:
        uploads.load("owner", ident)
    assert error.value.status_code == 410
    assert not sample.exists() and not metadata.exists()
    with pytest.raises(HTTPException):
        uploads.load("owner", "../outside")


@pytest.mark.duckdb_integration
def test_inspect_pages_reference_save_and_reset_keep_saved_sample(tmp_path, monkeypatch):
    monkeypatch.setattr(uploads, "_root", lambda: tmp_path)
    source = json.dumps({f"field{i}": i for i in range(300)}).encode()
    with TestClient(app) as client:
        result = client.post("/api/semantic-mapping/inspect", files={"file": ("data.json", source, "application/json")})
        assert result.status_code == 200, result.text
        inspected = result.json()
        assert inspected["field_count"] == 300
        upload_id = inspected["upload_id"]
        page = client.get(f"/api/semantic-mapping/sample-uploads/{upload_id}", params={"field_offset": 250, "field_limit": 50})
        assert page.status_code == 200 and len(page.json()["field_details"]) == 50, page.text
        field = page.json()["field_details"][-1]
        item = client.post("/api/semantic-mapping/items", json={"definition": {"key": "sample_last", "label": "Last value", "kind": "scalar", "data_type": "FLOAT", "unit": "", "dimensions": []}})
        assert item.status_code == 201, item.text
        recipe = {**inspected["recipe_suggestion"], "mappings": [{"result_item_id": item.json()["id"], "source": field["source"], "dimensions": {}, "missing": "error"}]}
        preview = client.post("/api/semantic-mapping/preview", files={"upload_id": (None, upload_id), "recipe": (None, json.dumps(recipe))})
        assert preview.status_code == 200, preview.text
        saved = client.post("/api/semantic-mapping/recipes", json={"name": "Auto sample", "definition": recipe, "sample_upload_id": upload_id})
        assert saved.status_code == 201, saved.text
        recipe_id = saved.json()["id"]
        assert client.delete(f"/api/semantic-mapping/sample-uploads/{upload_id}").status_code == 200
        assert client.get(f"/api/semantic-mapping/sample-uploads/{upload_id}").status_code == 404
        with connect() as conn:
            stored = conn.execute("SELECT sample_bytes FROM semantic_recipe_versions WHERE recipe_id=? AND version=1", [recipe_id]).fetchone()
            assert bytes(stored[0]) == source


@pytest.mark.duckdb_integration
def test_large_sample_reference_does_not_use_legacy_base64_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(uploads, "_root", lambda: tmp_path)
    content = json.dumps({"value": 42, "padding": "x" * (300 * 1024)}).encode()
    with TestClient(app) as client:
        inspected = client.post("/api/semantic-mapping/inspect", files={"file": ("large.json", content)}).json()
        item = client.post("/api/semantic-mapping/items", json={"definition": {"key": "large_sample_value", "label": "Value", "kind": "scalar", "unit": "", "dimensions": []}}).json()
        recipe = {**inspected["recipe_suggestion"], "mappings": [{"result_item_id": item["id"], "source": "#/value", "dimensions": {}}]}
        saved = client.post("/api/semantic-mapping/recipes", json={"name": "Large sample", "definition": recipe, "sample_upload_id": inspected["upload_id"]})
        assert saved.status_code == 201, saved.text
        with connect() as conn:
            size = conn.execute("SELECT octet_length(sample_bytes) FROM semantic_recipe_versions WHERE recipe_id=?", [saved.json()["id"]]).fetchone()[0]
            assert size == len(content)
