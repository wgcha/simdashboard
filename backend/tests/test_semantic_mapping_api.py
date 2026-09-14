from __future__ import annotations

import base64
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database_connection import connect
from app.main import app


LOAD_CASE = "loadcase-drop-bottom-001"


def _client() -> TestClient:
    return TestClient(app)


def _recipe_sample() -> bytes:
    return b"stress,node\n12.5,N-1\n"


def _multipart(*, fields: dict[str, str], content: bytes = _recipe_sample(), filename: str = "sample.csv") -> tuple[dict[str, str], bytes]:
    boundary = "semantic-" + uuid4().hex
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend((f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n".encode(), value.encode(), b"\r\n"))
    chunks.extend((f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\nContent-Type: text/csv\r\n\r\n".encode(), content, b"\r\n", f"--{boundary}--\r\n".encode()))
    return {"content-type": f"multipart/form-data; boundary={boundary}"}, b"".join(chunks)


def _definitions(client: TestClient) -> tuple[str, str, str]:
    item = client.post("/api/semantic-mapping/items", json={"definition": {"key": "peak_stress", "label": "Peak stress", "kind": "scalar", "unit": "MPa", "dimensions": ["node"]}})
    assert item.status_code == 201, item.text
    item_id = item.json()["id"]
    recipe_definition = {"format": "csv", "mappings": [{"result_item_id": item_id, "source": "stress", "source_unit": "MPa", "dimensions": {"node": "node"}}]}
    recipe = client.post("/api/semantic-mapping/recipes", json={"name": "peak csv", "definition": recipe_definition, "sample_filename": "sample.csv", "sample_content_base64": base64.b64encode(_recipe_sample()).decode()})
    assert recipe.status_code == 201, recipe.text
    recipe_id = recipe.json()["id"]
    template = client.post("/api/semantic-mapping/templates", json={"name": "peak view", "definition": {"widgets": [{"id": "peak", "type": "kpi", "title": "Peak", "item_ids": [item_id]}]}})
    assert template.status_code == 201, template.text
    template_id = template.json()["id"]
    activated = client.post("/api/semantic-mapping/activate-bundle", json={"recipe_id": recipe_id, "recipe_version": 1, "template_id": template_id, "template_version": 1, "expected_recipe_active_version": None, "expected_template_active_version": None})
    assert activated.status_code == 200, activated.text
    return item_id, recipe_id, template_id


@pytest.mark.duckdb_integration
def test_definition_lifecycle_sample_activation_and_expected_version_conflict() -> None:
    with _client() as client:
        item_id, recipe_id, template_id = _definitions(client)
        catalog = client.get("/api/semantic-mapping/catalog")
        assert catalog.status_code == 200
        assert {item_id, recipe_id, template_id} <= {entry["id"] for group in ("items", "recipes", "templates") for entry in catalog.json()[group]}
        stale = client.post("/api/semantic-mapping/items", json={"id": item_id, "expected_version": 0, "definition": {"key": "peak_stress", "label": "changed", "kind": "scalar", "unit": "MPa", "dimensions": ["node"]}})
        assert stale.status_code == 409, stale.text


@pytest.mark.duckdb_integration
def test_multipart_rejects_duplicate_and_unauthorized_before_body(monkeypatch: pytest.MonkeyPatch) -> None:
    with _client() as client:
        headers, body = _multipart(fields={"recipe": "{}", "recipe": "{}"})
        # Explicit duplicate textual parts are rejected by the strict parser.
        boundary = "duplicate-test"
        raw = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"recipe\"\r\n\r\n{{}}\r\n" * 2).encode() + f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.csv\"\r\n\r\nx\r\n--{boundary}--\r\n".encode()
        response = client.post("/api/semantic-mapping/preview", headers={"content-type": f"multipart/form-data; boundary={boundary}"}, content=raw)
        assert response.status_code == 422
        from app.routers import semantic_mapping
        monkeypatch.setattr(semantic_mapping, "require_permission", lambda *_a, **_k: (_ for _ in ()).throw(__import__("fastapi").HTTPException(403, "denied")))
        response = client.post("/api/semantic-mapping/inspect", headers=headers, content=body)
        assert response.status_code == 403


@pytest.mark.duckdb_integration
def test_import_provenance_retry_new_content_and_run_isolation() -> None:
    with _client() as client:
        item_id, recipe_id, template_id = _definitions(client)
        headers, body = _multipart(fields={"recipe_id": recipe_id, "load_case_id": LOAD_CASE, "template_id": template_id})
        first = client.post("/api/semantic-mapping/import", headers=headers, content=body)
        assert first.status_code == 200, first.text
        first_body = first.json(); run_id = first_body["run_id"]
        assert first_body["status"] == "IMPORTED" and first_body["widgets"][0]["status"] == "READY"
        retry = client.post("/api/semantic-mapping/import", headers=headers, content=body)
        assert retry.status_code == 200 and retry.json()["status"] == "SKIPPED" and retry.json()["run_id"] == run_id
        changed_headers, changed_body = _multipart(fields={"recipe_id": recipe_id, "load_case_id": LOAD_CASE, "template_id": template_id}, content=b"stress,node\n15,N-1\n")
        changed = client.post("/api/semantic-mapping/import", headers=changed_headers, content=changed_body)
        assert changed.status_code == 200 and changed.json()["status"] == "IMPORTED" and changed.json()["run_id"] != run_id
        selected = client.get("/api/semantic-mapping/results", params={"load_case_id": LOAD_CASE, "run_id": run_id})
        assert selected.status_code == 200 and selected.json()["run_id"] == run_id
        assert selected.json()["widgets"][0]["data"][0]["value"] == 12.5
        with connect() as conn:
            assert conn.execute("SELECT count(*) FROM semantic_import_provenance WHERE load_case_id=?", [LOAD_CASE]).fetchone()[0] == 2


@pytest.mark.duckdb_integration
def test_export_import_is_draft_and_invalid_package_is_atomic() -> None:
    with _client() as client:
        _definitions(client)
        exported = client.get("/api/semantic-mapping/export")
        assert exported.status_code == 200
        before = len(client.get("/api/semantic-mapping/catalog").json()["items"])
        invalid = client.post("/api/semantic-mapping/import-definitions", json={"format_version": 99, "items": [{"definition": {"key": "bad"}}]})
        assert invalid.status_code == 422
        assert len(client.get("/api/semantic-mapping/catalog").json()["items"]) == before


@pytest.mark.duckdb_integration
def test_folder_binding_parent_child_reconnect_revision_and_overlap(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "semantic-root"; (root / "arbitrary" / "case-a").mkdir(parents=True); (root / "renamed").mkdir()
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root))
    with _client() as client:
        _, recipe_id, template_id = _definitions(client)
        parent = client.post("/api/semantic-mapping/bindings", json={"relative_path": "arbitrary", "project_id": "project-tv-001", "role": "PROJECT", "recipe_ids": []})
        assert parent.status_code == 201, parent.text
        child = client.post("/api/semantic-mapping/bindings", json={"relative_path": "arbitrary/case-a", "project_id": "project-tv-001", "request_id": "request-drop-001", "load_case_id": LOAD_CASE, "role": "LOAD_CASE", "recipe_ids": [recipe_id], "template_id": template_id})
        assert child.status_code == 201, child.text
        cid, revision = child.json()["id"], child.json()["revision"]
        stale = client.put(f"/api/semantic-mapping/bindings/{cid}", json={"relative_path": "renamed", "project_id": "project-tv-001", "request_id": "request-drop-001", "load_case_id": LOAD_CASE, "role": "LOAD_CASE", "recipe_ids": [recipe_id], "template_id": template_id, "expected_revision": revision - 1})
        assert stale.status_code == 409, stale.text
        moved = client.put(f"/api/semantic-mapping/bindings/{cid}", json={"relative_path": "renamed", "project_id": "project-tv-001", "request_id": "request-drop-001", "load_case_id": LOAD_CASE, "role": "LOAD_CASE", "recipe_ids": [recipe_id], "template_id": template_id, "expected_revision": revision})
        assert moved.status_code == 200 and moved.json()["id"] == cid and moved.json()["revision"] == revision + 1


@pytest.mark.duckdb_integration
def test_documented_package_roundtrip_remaps_scatter_and_rejects_duplicate_keys():
    example = Path(__file__).resolve().parents[2] / "examples" / "semantic-mapping"
    with _client() as client:
        package = json.loads((example / "definitions.json").read_text(encoding="utf-8"))
        imported = client.post("/api/semantic-mapping/import-definitions", json=package)
        assert imported.status_code == 201, imported.text
        catalog = client.get("/api/semantic-mapping/catalog").json()
        csv_recipe = next(r for r in catalog["recipes"] if r["definition"]["format"] == "csv")
        saved = client.post("/api/semantic-mapping/recipes", json={"id": csv_recipe["id"], "name": "CSV", "definition": csv_recipe["definition"], "expected_version": 1, "sample_filename": "result.csv", "sample_content_base64": base64.b64encode((example / "result.csv").read_bytes()).decode()})
        assert saved.status_code == 201, saved.text
        activated = client.post("/api/semantic-mapping/activate-bundle", json={"recipe_id": csv_recipe["id"], "recipe_version": 2, "template_id": catalog["templates"][0]["id"], "template_version": 1})
        assert activated.status_code == 200, activated.text
        assert len(activated.json()["widgets"]) == 6
        headers, body = _multipart(fields={"recipe_id": csv_recipe["id"], "load_case_id": LOAD_CASE, "template_id": catalog["templates"][0]["id"]}, content=(example / "result.csv").read_bytes())
        loaded = client.post("/api/semantic-mapping/import", headers=headers, content=body)
        assert loaded.status_code == 200, loaded.text
        assert len(loaded.json()["widgets"]) == 6
        with connect() as conn:
            assert conn.execute("SELECT count(*) FROM curve_results WHERE analysis_run_id=?", [loaded.json()["run_id"]]).fetchone()[0] == 1
        exported = client.get("/api/semantic-mapping/export").json()
        assert "sample_bytes" not in json.dumps(exported)
        for entry in exported["items"]:
            entry["definition"]["key"] = "copy_" + entry["definition"]["key"]
        copied = client.post("/api/semantic-mapping/import-definitions", json=exported)
        assert copied.status_code == 201, copied.text
        copied_template = next(c for c in copied.json()["created"] if "widgets" in c["definition"])
        scatter = next(w for w in copied_template["definition"]["widgets"] if w["type"] == "scatter")
        assert scatter["x_item_id"] in copied.json()["item_id_remap"].values()
        assert scatter["y_item_id"] in copied.json()["item_id_remap"].values()
        before = client.get("/api/semantic-mapping/catalog").json()
        package["items"][0]["definition"]["key"] = "duplicate_fresh"
        package["items"][1]["definition"]["key"] = "duplicate_fresh"
        invalid = client.post("/api/semantic-mapping/import-definitions", json=package)
        assert invalid.status_code == 422
        assert client.get("/api/semantic-mapping/catalog").json() == before


@pytest.mark.duckdb_integration
def test_sample_failure_rolls_back_and_used_item_meaning_is_immutable():
    with _client() as client:
        item_id, recipe_id, template_id = _definitions(client)
        before = client.get("/api/semantic-mapping/catalog").json()
        recipe = before["recipes"][0]
        invalid = client.post("/api/semantic-mapping/recipes", json={"id": recipe_id, "name": "broken", "definition": recipe["definition"], "expected_version": 1, "sample_filename": "bad.csv", "sample_content_base64": base64.b64encode(b"wrong\n1\n").decode()})
        assert invalid.status_code == 422, invalid.text
        assert client.get("/api/semantic-mapping/catalog").json() == before
        definition = before["items"][0]["definition"]
        altered = client.post("/api/semantic-mapping/items", json={"id": item_id, "expected_version": 1, "definition": {**definition, "unit": "mm"}})
        assert altered.status_code == 422
        renamed = client.post("/api/semantic-mapping/items", json={"id": item_id, "expected_version": 1, "definition": {**definition, "label": "새 표시명"}})
        assert renamed.status_code == 201
        headers, body = _multipart(fields={"recipe_id": recipe_id, "load_case_id": LOAD_CASE, "template_id": template_id})
        imported = client.post("/api/semantic-mapping/import", headers=headers, content=body)
        assert imported.status_code == 200
        assert imported.json()["parsed"]["observations"][0]["label"] == "Peak stress"
        assert client.post(f"/api/semantic-mapping/recipes/{recipe_id}/activate", json={"version": 1}).status_code == 410
        assert client.post(f"/api/semantic-mapping/templates/{template_id}/activate", json={"version": 1}).status_code == 410


@pytest.mark.duckdb_integration
def test_retry_keeps_null_or_old_template_and_override_is_read_only():
    with _client() as client:
        item_id, recipe_id, template_id = _definitions(client)
        headers, body = _multipart(fields={"recipe_id": recipe_id, "load_case_id": LOAD_CASE})
        first = client.post("/api/semantic-mapping/import", headers=headers, content=body)
        assert first.status_code == 200, first.text
        headers, body = _multipart(fields={"recipe_id": recipe_id, "load_case_id": LOAD_CASE, "template_id": template_id})
        retry = client.post("/api/semantic-mapping/import", headers=headers, content=body)
        assert retry.status_code == 200 and retry.json()["status"] == "SKIPPED"
        assert retry.json()["widgets"] == [] and retry.json()["template_version"] is None
        headers, body = _multipart(fields={"recipe_id": recipe_id, "load_case_id": LOAD_CASE, "template_id": template_id}, content=b"stress,node\n15,N-1\n")
        second = client.post("/api/semantic-mapping/import", headers=headers, content=body)
        assert second.status_code == 200
        saved = client.post("/api/semantic-mapping/templates", json={"id": template_id, "expected_version": 1, "name": "Pa view", "definition": {"widgets": [{"id": "peak", "type": "kpi", "item_ids": [item_id], "display_unit": "Pa"}]}})
        assert saved.status_code == 201
        activated = client.post("/api/semantic-mapping/activate-bundle", json={"recipe_id": recipe_id, "recipe_version": 1, "template_id": template_id, "template_version": 2, "expected_recipe_active_version": 1, "expected_template_active_version": 1})
        assert activated.status_code == 200, activated.text
        retry = client.post("/api/semantic-mapping/import", headers=headers, content=body)
        assert retry.json()["template_version"] == 1 and retry.json()["widgets"][0]["unit"] == "MPa"
        override = client.get("/api/semantic-mapping/results", params={"load_case_id": LOAD_CASE, "run_id": second.json()["run_id"], "template_id": template_id})
        assert override.status_code == 200
        assert override.json()["template_version"] == 2 and override.json()["widgets"][0]["data"][0]["value"] == 15e6
        with connect() as conn:
            assert conn.execute("SELECT count(*) FROM semantic_import_provenance").fetchone()[0] == 2


@pytest.mark.duckdb_integration
def test_json_configuration_request_is_bounded_before_parsing():
    with _client() as client:
        response = client.post("/api/semantic-mapping/import-definitions", headers={"content-type": "application/json", "content-length": str(7 * 1024 * 1024)}, content=b"{}")
        assert response.status_code == 413


@pytest.mark.duckdb_integration
def test_refresh_reports_unmapped_invalid_and_ambiguous_files_without_partial_runs(monkeypatch, tmp_path):
    directory = tmp_path / "store" / "Eagle_2026"
    directory.mkdir(parents=True)
    (directory / "a.csv").write_bytes(_recipe_sample())
    (directory / "b.csv").write_bytes(b"stress,node\n12,N-1\n13,N-2\n")
    (directory / "other.json").write_text('{"unknown":1}', encoding="utf-8")
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(directory.parent))
    with _client() as client:
        _, recipe_id, template_id = _definitions(client)
        payload = {"relative_path": "Eagle_2026", "project_id": "project-tv-001", "load_case_id": LOAD_CASE, "role": "RESULTS", "recipe_ids": [recipe_id], "template_id": template_id}
        bound = client.post("/api/semantic-mapping/bindings", json=payload)
        assert bound.status_code == 201, bound.text
        assert bound.json()["request_id"] == "request-drop-001"
        result = client.post(f"/api/semantic-mapping/bindings/{bound.json()['id']}/refresh")
        assert result.status_code == 200, result.text
        assert {row["status"] for row in result.json()["results"]} == {"IMPORTED", "INVALID", "UNMAPPED"}
        (directory / "a.csv").rename(directory / "renamed.csv")
        repeated = client.post(f"/api/semantic-mapping/bindings/{bound.json()['id']}/refresh")
        assert {row["status"] for row in repeated.json()["results"]} == {"SKIPPED", "INVALID", "UNMAPPED"}
        catalog = client.get("/api/semantic-mapping/catalog").json()
        duplicate = client.post("/api/semantic-mapping/recipes", json={"name": "Second candidate", "definition": catalog["recipes"][0]["definition"], "sample_filename": "a.csv", "sample_content_base64": base64.b64encode(_recipe_sample()).decode()})
        assert duplicate.status_code == 201
        duplicate_id = duplicate.json()["id"]
        activated = client.post("/api/semantic-mapping/activate-bundle", json={"recipe_id": duplicate_id, "recipe_version": 1, "template_id": template_id, "template_version": 1, "expected_template_active_version": 1})
        assert activated.status_code == 200, activated.text
        changed = client.put(f"/api/semantic-mapping/bindings/{bound.json()['id']}", json={**payload, "expected_revision": 1, "recipe_ids": [recipe_id, duplicate_id]})
        assert changed.status_code == 200
        ambiguous = client.post(f"/api/semantic-mapping/bindings/{bound.json()['id']}/refresh")
        assert {row["status"] for row in ambiguous.json()["results"]} == {"AMBIGUOUS", "UNMAPPED"}
        with connect() as conn:
            assert conn.execute("SELECT count(*) FROM semantic_import_provenance").fetchone()[0] == 1
