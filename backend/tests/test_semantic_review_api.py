from __future__ import annotations

import base64
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import semantic_review
from app.database_connection import connect
from app.security import hash_password
from app import database
from app.services import spdm_storage


def _client() -> TestClient:
    return TestClient(app)


def _setup(client: TestClient, root: str) -> tuple[str, str, str]:
    item = client.post("/api/semantic-mapping/items", json={"definition": {"key": "review_stress", "label": "Stress", "kind": "scalar", "unit": "MPa", "dimensions": ["node"]}}).json()
    recipe_definition = {"format": "csv", "mappings": [{"result_item_id": item["id"], "source": "stress", "source_unit": "MPa", "dimensions": {"node": "node"}}]}
    sample = base64.b64encode(b"stress,node\n12,N-1\n").decode()
    recipe = client.post("/api/semantic-mapping/recipes", json={"name": "review csv", "definition": recipe_definition, "sample_filename": "a.csv", "sample_content_base64": sample}).json()
    template = client.post("/api/semantic-mapping/templates", json={"name": "review view", "definition": {"widgets": [{"id": "stress", "type": "kpi", "item_ids": [item["id"]]}]}}).json()
    assert client.post("/api/semantic-mapping/activate-bundle", json={"recipe_id": recipe["id"], "recipe_version": 1, "template_id": template["id"], "template_version": 1}).status_code == 200
    binding = client.post("/api/semantic-mapping/bindings", json={"relative_path": root, "project_id": "project-tv-001", "request_id": "request-drop-001", "load_case_id": "loadcase-drop-bottom-001", "role": "RESULTS", "recipe_ids": [recipe["id"]], "template_id": template["id"]}).json()
    return binding["id"], recipe["id"], template["id"]


def _review_item(client: TestClient, binding_id: str) -> dict:
    listed = client.get(f"/api/semantic-mapping/bindings/{binding_id}/review-items")
    assert listed.status_code == 200
    return listed.json()["items"][0]


def _make_ready(client: TestClient, root, binding_id: str, recipe_id: str, filename: str = "unknown.csv") -> dict:
    item = _review_item(client, binding_id)
    (root / filename).write_text("stress,node\n12,N-1\n", encoding="utf-8")
    assert client.post(f"/api/semantic-mapping/bindings/{binding_id}/refresh").status_code == 200
    item = _review_item(client, binding_id)
    response = client.post(
        f"/api/semantic-mapping/review-items/{item['id']}/revalidate",
        json={"expected_revision": item["revision"], "recipe_id": recipe_id, "recipe_version": 1},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.duckdb_integration
def test_refresh_persists_unmapped_then_revalidate_and_confirm(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    root = tmp_path / "store" / "review"; root.mkdir(parents=True)
    (root / "unknown.csv").write_text("other\n1\n", encoding="utf-8")
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root.parent))
    with _client() as client:
        binding_id, recipe_id, _ = _setup(client, "review")
        refreshed = client.post(f"/api/semantic-mapping/bindings/{binding_id}/refresh")
        assert refreshed.status_code == 200 and refreshed.json()["results"][0]["status"] == "UNMAPPED"
        listed = client.get(f"/api/semantic-mapping/bindings/{binding_id}/review-items")
        assert listed.status_code == 200
        item = listed.json()["items"][0]
        (root / "unknown.csv").write_text("stress,node\n12,N-1\n", encoding="utf-8")
        revalidated = client.post(f"/api/semantic-mapping/review-items/{item['id']}/revalidate", json={"expected_revision": item["revision"], "recipe_id": recipe_id, "recipe_version": 1})
        assert revalidated.status_code == 409  # hash changed: a new scan is required
        refreshed = client.post(f"/api/semantic-mapping/bindings/{binding_id}/refresh")
        assert refreshed.status_code == 200 and refreshed.json()["results"][0]["status"] == "PENDING"
        item = client.get(f"/api/semantic-mapping/bindings/{binding_id}/review-items").json()["items"][0]
        ready = client.post(f"/api/semantic-mapping/review-items/{item['id']}/revalidate", json={"expected_revision": item["revision"], "recipe_id": recipe_id, "recipe_version": 1})
        assert ready.status_code == 200 and ready.json()["review_state"] == "READY"
        confirmed = client.post(f"/api/semantic-mapping/review-items/{item['id']}/confirm", json={"expected_revision": ready.json()["revision"]})
        assert confirmed.status_code == 200 and confirmed.json()["status"] == "IMPORTED"
        assert len(client.get(f"/api/semantic-mapping/review-items/{item['id']}/history").json()["events"]) >= 3


@pytest.mark.duckdb_integration
def test_review_revalidate_uses_recipe_exact_display_template_without_binding_override(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    root = tmp_path / "store" / "review"; root.mkdir(parents=True)
    (root / "unknown.csv").write_text("other\n1\n", encoding="utf-8")
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root.parent))
    with _client() as client:
        binding_id, recipe_id, template_id = _setup(client, "review")
        recipe = next(entry for entry in client.get("/api/semantic-mapping/catalog").json()["recipes"] if entry["id"] == recipe_id)
        sample = base64.b64encode(b"stress,node\n12,N-1\n").decode()
        linked = client.post("/api/semantic-mapping/recipes", json={"id": recipe_id, "name": recipe["name"], "expected_version": 1, "definition": {**recipe["definition"], "display_template_id": template_id, "display_template_version": 1}, "sample_filename": "a.csv", "sample_content_base64": sample})
        assert linked.status_code == 201, linked.text
        assert client.post("/api/semantic-mapping/activate-bundle", json={"recipe_id": recipe_id, "recipe_version": 2, "template_id": template_id, "template_version": 1, "expected_recipe_active_version": 1, "expected_template_active_version": 1}).status_code == 200
        disconnected = client.put(f"/api/semantic-mapping/bindings/{binding_id}", json={"id": binding_id, "expected_revision": 1, "relative_path": "review", "project_id": "project-tv-001", "request_id": "request-drop-001", "load_case_id": "loadcase-drop-bottom-001", "role": "RESULTS", "recipe_ids": [recipe_id], "template_id": None})
        assert disconnected.status_code == 200, disconnected.text
        assert client.post(f"/api/semantic-mapping/bindings/{binding_id}/refresh").json()["results"][0]["status"] == "UNMAPPED"
        (root / "unknown.csv").write_text("stress,node\n12,N-1\n", encoding="utf-8")
        assert client.post(f"/api/semantic-mapping/bindings/{binding_id}/refresh").json()["results"][0]["status"] == "PENDING"
        item = _review_item(client, binding_id)
        ready = client.post(f"/api/semantic-mapping/review-items/{item['id']}/revalidate", json={"expected_revision": item["revision"], "recipe_id": recipe_id, "recipe_version": 2})
        assert ready.status_code == 200, ready.text
        assert ready.json()["template_id"] == template_id and ready.json()["template_version"] == 1 and ready.json()["widgets"]
        confirmed = client.post(f"/api/semantic-mapping/review-items/{item['id']}/confirm", json={"expected_revision": ready.json()["revision"]})
        assert confirmed.status_code == 200 and confirmed.json()["status"] == "IMPORTED"


@pytest.mark.duckdb_integration
def test_terminal_refresh_uses_confirmed_no_template_provenance_not_new_recipe_link(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    root = tmp_path / "store" / "review"; root.mkdir(parents=True)
    (root / "unknown.csv").write_text("other\n1\n", encoding="utf-8")
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root.parent))
    with _client() as client:
        binding_id, recipe_id, template_id = _setup(client, "review")
        disconnected = client.put(f"/api/semantic-mapping/bindings/{binding_id}", json={"id": binding_id, "expected_revision": 1, "relative_path": "review", "project_id": "project-tv-001", "request_id": "request-drop-001", "load_case_id": "loadcase-drop-bottom-001", "role": "RESULTS", "recipe_ids": [recipe_id], "template_id": None})
        assert disconnected.status_code == 200
        assert client.post(f"/api/semantic-mapping/bindings/{binding_id}/refresh").json()["results"][0]["status"] == "UNMAPPED"
        (root / "unknown.csv").write_text("stress,node\n12,N-1\n", encoding="utf-8")
        assert client.post(f"/api/semantic-mapping/bindings/{binding_id}/refresh").json()["results"][0]["status"] == "PENDING"
        item = _review_item(client, binding_id)
        ready = client.post(f"/api/semantic-mapping/review-items/{item['id']}/revalidate", json={"expected_revision": item["revision"], "recipe_id": recipe_id, "recipe_version": 1}).json()
        confirmed = client.post(f"/api/semantic-mapping/review-items/{item['id']}/confirm", json={"expected_revision": ready["revision"]}).json()
        assert confirmed["status"] == "IMPORTED"
        recipe = next(entry for entry in client.get("/api/semantic-mapping/catalog").json()["recipes"] if entry["id"] == recipe_id)
        sample = base64.b64encode(b"stress,node\n12,N-1\n").decode()
        assert client.post("/api/semantic-mapping/recipes", json={"id": recipe_id, "name": recipe["name"], "expected_version": 1, "definition": {**recipe["definition"], "display_template_id": template_id, "display_template_version": 1}, "sample_filename": "a.csv", "sample_content_base64": sample}).status_code == 201
        assert client.post("/api/semantic-mapping/activate-bundle", json={"recipe_id": recipe_id, "recipe_version": 2, "template_id": template_id, "template_version": 1, "expected_recipe_active_version": 1, "expected_template_active_version": 1}).status_code == 200
        refreshed = client.post(f"/api/semantic-mapping/bindings/{binding_id}/refresh")
        assert refreshed.status_code == 200, refreshed.text
        result = refreshed.json()["results"][0]
        assert result["status"] == "IMPORTED" and result["run_id"] == confirmed["run_id"]
        assert result["review_available"] is False and result["clear_reason"] == "NO_DISPLAY_TEMPLATE"
        monkeypatch.setattr(spdm_storage, "read_stable_bytes", lambda *_args, **_kwargs: (_ for _ in ()).throw(spdm_storage.SpdmStorageError("SPDM_FILE_BUSY", "busy")))
        locked = client.post(f"/api/semantic-mapping/bindings/{binding_id}/refresh")
        assert locked.status_code == 200, locked.text
        locked_result = locked.json()["results"][0]
        assert locked_result["status"] == "IMPORTED" and locked_result["run_id"] == confirmed["run_id"]
        assert locked_result["review_available"] is False and locked_result["clear_reason"] == "NO_DISPLAY_TEMPLATE"


@pytest.mark.duckdb_integration
def test_review_list_paginates_and_history_reports_truncation(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    root = tmp_path / "store" / "review"; root.mkdir(parents=True)
    for name in ("a.csv", "b.csv", "c.csv"):
        (root / name).write_text("other\n1\n", encoding="utf-8")
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root.parent))
    with _client() as client:
        binding_id, _, _ = _setup(client, "review")
        assert client.post(f"/api/semantic-mapping/bindings/{binding_id}/refresh").status_code == 200
        first = client.get(f"/api/semantic-mapping/bindings/{binding_id}/review-items?limit=1").json()
        second = client.get(f"/api/semantic-mapping/bindings/{binding_id}/review-items?limit=1&cursor={first['next_cursor']}").json()
        assert len(first["items"]) == len(second["items"]) == 1
        assert first["items"][0]["id"] != second["items"][0]["id"]
        item = first["items"][0]
        (root / item["relative_path"]).write_text("other\n2\n", encoding="utf-8")
        assert client.post(f"/api/semantic-mapping/bindings/{binding_id}/refresh").status_code == 200
        history = client.get(f"/api/semantic-mapping/review-items/{item['id']}/history?limit=1")
        assert history.status_code == 200
        assert len(history.json()["events"]) == 1 and history.json()["truncated"] is True


@pytest.mark.duckdb_integration
def test_ambiguous_review_can_select_one_active_recipe_and_confirm(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    root = tmp_path / "store" / "review"; root.mkdir(parents=True)
    (root / "ambiguous.csv").write_text("stress,node\n12,N-1\n", encoding="utf-8")
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root.parent))
    with _client() as client:
        binding_id, recipe_id, template_id = _setup(client, "review")
        item_id = client.get("/api/semantic-mapping/catalog").json()["items"][0]["id"]
        sample = base64.b64encode(b"stress,node\n12,N-1\n").decode()
        recipe_two = client.post("/api/semantic-mapping/recipes", json={"name": "second review csv", "definition": {"format": "csv", "mappings": [{"result_item_id": item_id, "source": "stress", "source_unit": "MPa", "dimensions": {"node": "node"}}]}, "sample_filename": "a.csv", "sample_content_base64": sample}).json()
        activation = client.post("/api/semantic-mapping/activate-bundle", json={"recipe_id": recipe_two["id"], "recipe_version": 1, "template_id": template_id, "template_version": 1, "expected_template_active_version": 1})
        assert activation.status_code == 200, activation.text
        update = client.put(f"/api/semantic-mapping/bindings/{binding_id}", json={"id": binding_id, "expected_revision": 1, "relative_path": "review", "project_id": "project-tv-001", "request_id": "request-drop-001", "load_case_id": "loadcase-drop-bottom-001", "role": "RESULTS", "recipe_ids": [recipe_id, recipe_two["id"]], "template_id": template_id})
        assert update.status_code == 200, update.text
        refreshed = client.post(f"/api/semantic-mapping/bindings/{binding_id}/refresh")
        assert refreshed.status_code == 200 and refreshed.json()["results"][0]["status"] == "AMBIGUOUS"
        item = _review_item(client, binding_id)
        ready = client.post(f"/api/semantic-mapping/review-items/{item['id']}/revalidate", json={"expected_revision": item["revision"], "recipe_id": recipe_id, "recipe_version": 1})
        assert ready.status_code == 200 and ready.json()["widgets"]
        confirmed = client.post(f"/api/semantic-mapping/review-items/{item['id']}/confirm", json={"expected_revision": ready.json()["revision"]})
        assert confirmed.status_code == 200 and confirmed.json()["status"] == "IMPORTED"


@pytest.mark.duckdb_integration
def test_ready_source_removal_stales_with_event(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    root = tmp_path / "store" / "review"; root.mkdir(parents=True)
    (root / "unknown.csv").write_text("other\n1\n", encoding="utf-8")
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root.parent))
    with _client() as client:
        binding_id, recipe_id, _ = _setup(client, "review")
        assert client.post(f"/api/semantic-mapping/bindings/{binding_id}/refresh").status_code == 200
        ready = _make_ready(client, root, binding_id, recipe_id)
        (root / "unknown.csv").unlink()
        stale = client.post(f"/api/semantic-mapping/review-items/{ready['id']}/revalidate", json={"expected_revision": ready["revision"]})
        assert stale.status_code == 409 and stale.json()["detail"]["code"] == "SEMANTIC_REVIEW_SOURCE_STALE"
        item = _review_item(client, binding_id)
        assert item["review_state"] == "STALE"
        assert client.get(f"/api/semantic-mapping/review-items/{item['id']}/history").json()["events"][0]["new_state"] == "STALE"


@pytest.mark.duckdb_integration
def test_terminal_review_refuses_revalidate_and_preserves_confirmed_run(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    root = tmp_path / "store" / "review"; root.mkdir(parents=True)
    (root / "unknown.csv").write_text("other\n1\n", encoding="utf-8")
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root.parent))
    with _client() as client:
        binding_id, recipe_id, _ = _setup(client, "review")
        assert client.post(f"/api/semantic-mapping/bindings/{binding_id}/refresh").status_code == 200
        ready = _make_ready(client, root, binding_id, recipe_id)
        confirmed = client.post(f"/api/semantic-mapping/review-items/{ready['id']}/confirm", json={"expected_revision": ready["revision"]}).json()
        history_before = client.get(f"/api/semantic-mapping/review-items/{ready['id']}/history").json()["events"]
        refused = client.post(f"/api/semantic-mapping/review-items/{ready['id']}/revalidate", json={"expected_revision": confirmed["item"]["revision"]})
        assert refused.status_code == 409 and refused.json()["detail"]["code"] == "SEMANTIC_REVIEW_TERMINAL"
        after = _review_item(client, binding_id)
        assert after["confirmed_analysis_run_id"] == confirmed["run_id"] and after["review_state"] == "IMPORTED"
        assert len(client.get(f"/api/semantic-mapping/review-items/{ready['id']}/history").json()["events"]) == len(history_before)
        recipe = next(entry for entry in client.get("/api/semantic-mapping/catalog").json()["recipes"] if entry["id"] == recipe_id)
        sample = base64.b64encode(b"stress,node\n12,N-1\n").decode()
        assert client.post("/api/semantic-mapping/recipes", json={"id": recipe_id, "name": "review csv v2", "expected_version": 1, "definition": recipe["definition"], "sample_filename": "a.csv", "sample_content_base64": sample}).status_code == 201
        template_id = after["template_id"]
        assert client.post("/api/semantic-mapping/activate-bundle", json={"recipe_id": recipe_id, "recipe_version": 2, "template_id": template_id, "template_version": 1, "expected_recipe_active_version": 1, "expected_template_active_version": 1}).status_code == 200
        repeated = client.post(f"/api/semantic-mapping/bindings/{binding_id}/refresh")
        assert repeated.status_code == 200
        assert repeated.json()["results"][0]["status"] == "IMPORTED" and repeated.json()["partial"] is False


@pytest.mark.duckdb_integration
def test_active_recipe_change_does_not_reinterpret_already_ready_review(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    root = tmp_path / "store" / "review"; root.mkdir(parents=True)
    (root / "unknown.csv").write_text("other\n1\n", encoding="utf-8")
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root.parent))
    with _client() as client:
        binding_id, recipe_id, template_id = _setup(client, "review")
        assert client.post(f"/api/semantic-mapping/bindings/{binding_id}/refresh").status_code == 200
        ready = _make_ready(client, root, binding_id, recipe_id)
        recipe = next(entry for entry in client.get("/api/semantic-mapping/catalog").json()["recipes"] if entry["id"] == recipe_id)
        sample = base64.b64encode(b"stress,node\n12,N-1\n").decode()
        changed = client.post("/api/semantic-mapping/recipes", json={"id": recipe_id, "name": "review csv v2", "expected_version": 1, "definition": recipe["definition"], "sample_filename": "a.csv", "sample_content_base64": sample})
        assert changed.status_code == 201, changed.text
        activation = client.post("/api/semantic-mapping/activate-bundle", json={"recipe_id": recipe_id, "recipe_version": 2, "template_id": template_id, "template_version": 1, "expected_recipe_active_version": 1, "expected_template_active_version": 1})
        assert activation.status_code == 200, activation.text
        confirmed = client.post(f"/api/semantic-mapping/review-items/{ready['id']}/confirm", json={"expected_revision": ready["revision"]})
        assert confirmed.status_code == 200 and confirmed.json()["item"]["selected_recipe_version"] == 1


@pytest.mark.duckdb_integration
def test_terminal_review_explicit_reopen_keeps_prior_run_and_can_use_v2(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    root = tmp_path / "store" / "review"; root.mkdir(parents=True)
    (root / "unknown.csv").write_text("other\n1\n", encoding="utf-8")
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root.parent))
    with _client() as client:
        binding_id, recipe_id, template_id = _setup(client, "review")
        assert client.post(f"/api/semantic-mapping/bindings/{binding_id}/refresh").status_code == 200
        ready = _make_ready(client, root, binding_id, recipe_id)
        first = client.post(f"/api/semantic-mapping/review-items/{ready['id']}/confirm", json={"expected_revision": ready["revision"]}).json()
        recipe = next(entry for entry in client.get("/api/semantic-mapping/catalog").json()["recipes"] if entry["id"] == recipe_id)
        sample = base64.b64encode(b"stress,node\n12,N-1\n").decode()
        assert client.post("/api/semantic-mapping/recipes", json={"id": recipe_id, "name": "review csv v2", "expected_version": 1, "definition": recipe["definition"], "sample_filename": "a.csv", "sample_content_base64": sample}).status_code == 201
        assert client.post("/api/semantic-mapping/activate-bundle", json={"recipe_id": recipe_id, "recipe_version": 2, "template_id": template_id, "template_version": 1, "expected_recipe_active_version": 1, "expected_template_active_version": 1}).status_code == 200
        reopened = client.post(f"/api/semantic-mapping/review-items/{ready['id']}/reopen", json={"expected_revision": first["item"]["revision"]})
        assert reopened.status_code == 200 and reopened.json()["review_state"] == "OPEN"
        assert reopened.json()["previous_confirmed_analysis_run_id"] == first["run_id"]
        revalidated = client.post(f"/api/semantic-mapping/review-items/{ready['id']}/revalidate", json={"expected_revision": reopened.json()["revision"], "recipe_id": recipe_id, "recipe_version": 2})
        assert revalidated.status_code == 200 and revalidated.json()["selected_recipe_version"] == 2
        second = client.post(f"/api/semantic-mapping/review-items/{ready['id']}/confirm", json={"expected_revision": revalidated.json()["revision"]})
        assert second.status_code == 200 and second.json()["run_id"] != first["run_id"]
        assert second.json()["item"]["previous_confirmed_analysis_run_id"] == first["run_id"]


@pytest.mark.duckdb_integration
def test_reconnect_to_another_load_case_stales_old_ready_item_without_import(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    root = tmp_path / "store" / "review"; root.mkdir(parents=True)
    (root / "unknown.csv").write_text("other\n1\n", encoding="utf-8")
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root.parent))
    with _client() as client:
        binding_id, recipe_id, template_id = _setup(client, "review")
        assert client.post(f"/api/semantic-mapping/bindings/{binding_id}/refresh").status_code == 200
        ready = _make_ready(client, root, binding_id, recipe_id)
        reconnect = client.put(f"/api/semantic-mapping/bindings/{binding_id}", json={"id": binding_id, "expected_revision": 1, "relative_path": "review", "project_id": "project-tv-001", "request_id": "request-clamp-001", "load_case_id": "loadcase-clamp-left-001", "role": "RESULTS", "recipe_ids": [recipe_id], "template_id": template_id})
        assert reconnect.status_code == 200, reconnect.text
        (root / "unknown.csv").write_text("other\n2\n", encoding="utf-8")
        refreshed = client.post(f"/api/semantic-mapping/bindings/{binding_id}/refresh")
        assert refreshed.status_code == 200 and refreshed.json()["results"][0]["status"] == "UNMAPPED"
        current_target = _review_item(client, binding_id)
        assert current_target["id"] != ready["id"] and current_target["load_case_id"] == "loadcase-clamp-left-001"
        old_mutation = client.post(f"/api/semantic-mapping/review-items/{ready['id']}/confirm", json={"expected_revision": ready["revision"]})
        assert old_mutation.status_code == 409 and old_mutation.json()["detail"]["code"] == "SEMANTIC_REVIEW_TARGET_CHANGED"
        with connect() as conn:
            old = conn.execute("SELECT load_case_id, review_state, confirmed_analysis_run_id FROM semantic_import_review_items WHERE id=?", [ready["id"]]).fetchone()
            assert old[0] == "loadcase-drop-bottom-001" and old[1] == "READY" and old[2] is None


@pytest.mark.duckdb_integration
def test_stale_revalidate_revision_conflict_does_not_mutate_newer_item(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    root = tmp_path / "store" / "review"; root.mkdir(parents=True)
    (root / "unknown.csv").write_text("other\n1\n", encoding="utf-8")
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root.parent))
    with _client() as client:
        binding_id, recipe_id, template_id = _setup(client, "review")
        assert client.post(f"/api/semantic-mapping/bindings/{binding_id}/refresh").status_code == 200
        ready = _make_ready(client, root, binding_id, recipe_id)
        reconnect = client.put(f"/api/semantic-mapping/bindings/{binding_id}", json={"id": binding_id, "expected_revision": 1, "relative_path": "review", "project_id": "project-tv-001", "request_id": "request-drop-001", "load_case_id": "loadcase-drop-bottom-001", "role": "INPUT", "recipe_ids": [recipe_id], "template_id": template_id})
        assert reconnect.status_code == 200
        stale_request = client.post(f"/api/semantic-mapping/review-items/{ready['id']}/revalidate", json={"expected_revision": ready["revision"] - 1})
        assert stale_request.status_code == 409 and stale_request.json()["detail"]["code"] == "SEMANTIC_REVIEW_REVISION_CONFLICT"
        assert _review_item(client, binding_id)["review_state"] == "READY"


@pytest.mark.duckdb_integration
def test_revalidate_race_checks_current_revision_before_binding_stale_transition(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    root = tmp_path / "store" / "review"; root.mkdir(parents=True)
    (root / "unknown.csv").write_text("other\n1\n", encoding="utf-8")
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root.parent))
    with _client() as client:
        binding_id, recipe_id, _ = _setup(client, "review")
        assert client.post(f"/api/semantic-mapping/bindings/{binding_id}/refresh").status_code == 200
        item = _review_item(client, binding_id)
        (root / "unknown.csv").write_text("stress,node\n12,N-1\n", encoding="utf-8")
        assert client.post(f"/api/semantic-mapping/bindings/{binding_id}/refresh").status_code == 200
        item = _review_item(client, binding_id)
        original_preview = semantic_review.preview_recipe
        advanced = False
        def advance_during_parse(*args, **kwargs):
            nonlocal advanced
            if not advanced:
                advanced = True
                with connect() as conn:
                    conn.execute("UPDATE semantic_import_review_items SET revision=revision+1 WHERE id=?", [item["id"]])
                    conn.execute("UPDATE semantic_folder_bindings SET revision=revision+1 WHERE id=?", [binding_id])
            return original_preview(*args, **kwargs)
        monkeypatch.setattr(semantic_review, "preview_recipe", advance_during_parse)
        raced = client.post(f"/api/semantic-mapping/review-items/{item['id']}/revalidate", json={"expected_revision": item["revision"], "recipe_id": recipe_id, "recipe_version": 1})
        assert raced.status_code == 409 and raced.json()["detail"]["code"] == "SEMANTIC_REVIEW_REVISION_CONFLICT"
        with connect() as conn:
            current = conn.execute("SELECT review_state, revision FROM semantic_import_review_items WHERE id=?", [item["id"]]).fetchone()
            assert current[0] == "OPEN" and current[1] == item["revision"] + 1


@pytest.mark.duckdb_integration
def test_terminal_refresh_keeps_completed_item_when_source_is_temporarily_unreadable(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    root = tmp_path / "store" / "review"; root.mkdir(parents=True)
    (root / "unknown.csv").write_text("other\n1\n", encoding="utf-8")
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root.parent))
    with _client() as client:
        binding_id, recipe_id, _ = _setup(client, "review")
        assert client.post(f"/api/semantic-mapping/bindings/{binding_id}/refresh").status_code == 200
        ready = _make_ready(client, root, binding_id, recipe_id)
        confirmed = client.post(f"/api/semantic-mapping/review-items/{ready['id']}/confirm", json={"expected_revision": ready["revision"]}).json()
        before_history = client.get(f"/api/semantic-mapping/review-items/{ready['id']}/history").json()["events"]
        def unavailable(*_args, **_kwargs):
            raise spdm_storage.SpdmStorageError("SPDM_FILE_BUSY", "busy")
        monkeypatch.setattr(spdm_storage, "read_stable_bytes", unavailable)
        refreshed = client.post(f"/api/semantic-mapping/bindings/{binding_id}/refresh")
        assert refreshed.status_code == 200 and refreshed.json()["results"][0]["status"] == "IMPORTED"
        item = _review_item(client, binding_id)
        assert item["confirmed_analysis_run_id"] == confirmed["run_id"] and item["review_state"] == "IMPORTED"
        assert len(client.get(f"/api/semantic-mapping/review-items/{ready['id']}/history").json()["events"]) == len(before_history)


@pytest.mark.duckdb_integration
def test_revalidate_audit_failure_rolls_back_state_and_event(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    root = tmp_path / "store" / "review"; root.mkdir(parents=True)
    (root / "unknown.csv").write_text("other\n1\n", encoding="utf-8")
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root.parent))
    with _client() as client:
        binding_id, recipe_id, _ = _setup(client, "review")
        assert client.post(f"/api/semantic-mapping/bindings/{binding_id}/refresh").status_code == 200
        item = _review_item(client, binding_id)
        (root / "unknown.csv").write_text("stress,node\n12,N-1\n", encoding="utf-8")
        assert client.post(f"/api/semantic-mapping/bindings/{binding_id}/refresh").status_code == 200
        item = _review_item(client, binding_id)
        monkeypatch.setattr(semantic_review, "write_audit_event", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("audit failed")))
        with pytest.raises(RuntimeError, match="audit failed"):
            client.post(f"/api/semantic-mapping/review-items/{item['id']}/revalidate", json={"expected_revision": item["revision"], "recipe_id": recipe_id, "recipe_version": 1})
        after = _review_item(client, binding_id)
        assert after["review_state"] == "OPEN"
        assert all(event["new_state"] != "READY" for event in client.get(f"/api/semantic-mapping/review-items/{item['id']}/history").json()["events"])


@pytest.mark.duckdb_integration
def test_review_permission_is_checked_before_source_read(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    root = tmp_path / "store" / "review"; root.mkdir(parents=True)
    (root / "unknown.csv").write_text("other\n1\n", encoding="utf-8")
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root.parent))
    with _client() as client:
        binding_id, _, _ = _setup(client, "review")
        assert client.post(f"/api/semantic-mapping/bindings/{binding_id}/refresh").status_code == 200
        item = _review_item(client, binding_id)
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "semantic-review-permission-test-key-32")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        # Password mode intentionally stays unavailable until a global admin
        # has a password.  This fixture supplies that bootstrap prerequisite;
        # the caller under test remains a non-member viewer.
        conn.execute("INSERT INTO users (id, username, password_hash, display_name, legacy_role, is_active, created_at, updated_at, account_status, is_global_admin) VALUES ('review-auth-admin', 'review-auth-admin', ?, 'Admin', 'admin', true, ?, ?, 'ACTIVE', true)", [hash_password("review-auth-admin-password"), now, now])
        conn.execute("INSERT INTO users (id, username, password_hash, display_name, legacy_role, is_active, created_at, updated_at, account_status, is_global_admin) VALUES ('review-denied', 'review-denied', ?, 'Denied', 'viewer', true, ?, ?, 'ACTIVE', false)", [hash_password("review-denied-password"), now, now])
    def source_must_not_run(*_args, **_kwargs):
        pytest.fail("source read occurred before permission was denied")
    monkeypatch.setattr(semantic_review, "_source", source_must_not_run)
    with _client() as client:
        login = client.post("/api/auth/login", json={"username": "review-denied", "password": "review-denied-password"})
        assert login.status_code == 200, login.text
        denied = client.post(f"/api/semantic-mapping/review-items/{item['id']}/revalidate", headers={"Authorization": f"Bearer {login.json()['access_token']}"}, json={"expected_revision": item["revision"]})
        assert denied.status_code == 403


@pytest.mark.duckdb_integration
def test_reconnected_target_cannot_list_or_read_old_target_review_history(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    root = tmp_path / "store" / "review"; root.mkdir(parents=True)
    (root / "unknown.csv").write_text("other\n1\n", encoding="utf-8")
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root.parent))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with _client() as client:
        binding_id, recipe_id, template_id = _setup(client, "review")
        assert client.post(f"/api/semantic-mapping/bindings/{binding_id}/refresh").status_code == 200
        old_item = _review_item(client, binding_id)
        with connect() as conn:
            conn.execute("INSERT INTO projects(id, name, product_name, description, created_at) VALUES ('project-review-b', 'Review B', 'Review B', '', ?)", [now])
            conn.execute("INSERT INTO analysis_requests(id, project_id, title, status, owner, owner_user_id, requested_at, due_at, overall_note) VALUES ('request-review-b', 'project-review-b', 'Review B', 'READY', 'local-admin', 'local-admin', ?, NULL, '')", [now])
            conn.execute("INSERT INTO load_cases(id, request_id, name, analysis_type, status, parameters_json, created_at) VALUES ('loadcase-review-b', 'request-review-b', 'Review B', 'DROP', 'READY', '{}', ?)", [now])
            conn.execute("INSERT INTO users (id, username, password_hash, display_name, legacy_role, is_active, created_at, updated_at, account_status, is_global_admin) VALUES ('review-history-admin', 'review-history-admin', ?, 'Admin', 'admin', true, ?, ?, 'ACTIVE', true)", [hash_password('review-history-admin-password'), now, now])
            conn.execute("INSERT INTO users (id, username, password_hash, display_name, legacy_role, is_active, created_at, updated_at, account_status, is_global_admin) VALUES ('review-history-b', 'review-history-b', ?, 'B only', 'viewer', true, ?, ?, 'ACTIVE', false)", [hash_password('review-history-b-password'), now, now])
            conn.execute("INSERT INTO project_memberships(id, project_id, user_id, role, created_by, created_at, updated_by, updated_at) VALUES ('review-history-b-member', 'project-review-b', 'review-history-b', 'power', 'local-admin', ?, 'local-admin', ?)", [now, now])
        reconnect = client.put(f"/api/semantic-mapping/bindings/{binding_id}", json={"id": binding_id, "expected_revision": 1, "relative_path": "review", "project_id": "project-review-b", "request_id": "request-review-b", "load_case_id": "loadcase-review-b", "role": "RESULTS", "recipe_ids": [recipe_id], "template_id": template_id})
        assert reconnect.status_code == 200, reconnect.text
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "semantic-review-history-test-key-32")
    with _client() as client:
        login = client.post("/api/auth/login", json={"username": "review-history-b", "password": "review-history-b-password"})
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        listed = client.get(f"/api/semantic-mapping/bindings/{binding_id}/review-items", headers=headers)
        assert listed.status_code == 200 and listed.json()["items"] == []
        history = client.get(f"/api/semantic-mapping/review-items/{old_item['id']}/history", headers=headers)
        assert history.status_code == 403


@pytest.mark.unit
def test_postgres_startup_gate_requires_review_events_table(monkeypatch: pytest.MonkeyPatch) -> None:
    class Cursor:
        def __init__(self, row): self.row = row
        def fetchone(self): return (self.row,)

    class Connection:
        def execute(self, statement):
            return Cursor(None if "semantic_import_review_events" in statement else "public.present")

    class Context:
        def __enter__(self): return Connection()
        def __exit__(self, *_args): return None

    monkeypatch.setattr(database, "database_settings", lambda: SimpleNamespace(backend="postgresql"))
    monkeypatch.setattr(database, "connect", lambda: Context())
    with pytest.raises(RuntimeError, match="semantic_import_review_events"):
        database.initialize_database()
