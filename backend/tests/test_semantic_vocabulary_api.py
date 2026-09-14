from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.database_connection import connect
from app.main import app


def _client() -> TestClient:
    return TestClient(app)


def _folder(*, label: str, aliases: list[str], scope_project_id: str | None = None, enabled: bool = True) -> dict[str, object]:
    return {
        "key": "input_folder" if scope_project_id else "global_folder",
        "label": label,
        "description": "Reusable exact label",
        "target_kind": "FOLDER_ROLE",
        "target_id": "INPUT",
        "scope_project_id": scope_project_id,
        "aliases": aliases,
        "enabled": enabled,
    }


@pytest.mark.duckdb_integration
def test_crud_normalized_conflict_and_revision_cas() -> None:
    with _client() as client:
        created = client.post("/api/semantic-vocabulary", json=_folder(label="Input Data", aliases=["Source files"])).json()
        assert created["revision"] == 1 and created["target_label"] == "INPUT"
        duplicate = client.post("/api/semantic-vocabulary", json={**_folder(label="Other", aliases=["  source   files  "]), "key": "other_folder"})
        assert duplicate.status_code == 409
        updated = client.put(f"/api/semantic-vocabulary/{created['id']}", json={**_folder(label="Input data", aliases=["Source files"]), "expected_revision": 1})
        assert updated.status_code == 200 and updated.json()["revision"] == 2
        stale = client.put(f"/api/semantic-vocabulary/{created['id']}", json={**_folder(label="Input data", aliases=["Source files"]), "expected_revision": 1})
        assert stale.status_code == 409
        changed_target = client.put(f"/api/semantic-vocabulary/{created['id']}", json={**_folder(label="Input data", aliases=["Source files"]), "target_id": "RESULTS", "expected_revision": 2})
        assert changed_target.status_code == 422


@pytest.mark.duckdb_integration
def test_disable_allows_global_fallback_and_resolve_is_exact() -> None:
    with _client() as client:
        global_entry = client.post("/api/semantic-vocabulary", json=_folder(label="Input", aliases=["source files"])).json()
        local_payload = _folder(label="Local Input", aliases=["source files"], scope_project_id="project-tv-001")
        local_entry = client.post("/api/semantic-vocabulary", json=local_payload).json()
        local = client.post("/api/semantic-vocabulary/resolve", json={"terms": ["SOURCE   FILES"], "scope_project_id": "project-tv-001"}).json()["matches"][0]
        assert local["status"] == "MATCHED" and local["candidates"][0]["id"] == local_entry["id"]
        disabled = client.put(f"/api/semantic-vocabulary/{local_entry['id']}", json={**local_payload, "enabled": False, "expected_revision": 1})
        assert disabled.status_code == 200
        fallback = client.post("/api/semantic-vocabulary/resolve", json={"terms": ["source files"], "scope_project_id": "project-tv-001"}).json()["matches"][0]
        assert fallback["status"] == "MATCHED" and fallback["candidates"][0]["id"] == global_entry["id"]
        exact = client.post("/api/semantic-vocabulary/resolve", json={"terms": ["source-files"]}).json()["matches"][0]
        assert exact["status"] == "UNMAPPED"


@pytest.mark.duckdb_integration
def test_scope_ownership_ambiguous_targets_and_existing_mapping_unchanged() -> None:
    with _client() as client:
        mismatch = client.post("/api/semantic-vocabulary", json={
            "key": "request_drop", "label": "Drop request", "description": "", "target_kind": "REQUEST",
            "target_id": "request-drop-001", "scope_project_id": "project-feature-showcase", "aliases": [], "enabled": True,
        })
        assert mismatch.status_code == 422
        first = client.post("/api/semantic-vocabulary", json=_folder(label="Shared name", aliases=[]))
        assert first.status_code == 201
        project = client.post("/api/semantic-vocabulary", json={
            "key": "tv_project", "label": "Shared name", "description": "", "target_kind": "PROJECT",
            "target_id": "project-tv-001", "scope_project_id": None, "aliases": [], "enabled": True,
        })
        assert project.status_code == 201
        ambiguous = client.post("/api/semantic-vocabulary/resolve", json={"terms": ["shared name"]}).json()["matches"][0]
        assert ambiguous["status"] == "AMBIGUOUS" and {candidate["target_kind"] for candidate in ambiguous["candidates"]} == {"FOLDER_ROLE", "PROJECT"}
        before = client.get("/api/semantic-mapping/catalog").json()
        client.post("/api/semantic-vocabulary", json={**_folder(label="Separate catalog", aliases=["isolated"]), "key": "separate_catalog"}).raise_for_status()
        assert client.get("/api/semantic-mapping/catalog").json() == before


@pytest.mark.duckdb_integration
def test_permission_is_checked_before_catalog_access(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.routers import semantic_vocabulary

    monkeypatch.setattr(semantic_vocabulary, "require_permission", lambda *_args, **_kwargs: (_ for _ in ()).throw(HTTPException(403, "denied")))
    with _client() as client:
        response = client.get("/api/semantic-vocabulary")
    assert response.status_code == 403


@pytest.mark.duckdb_integration
def test_api_rejects_nul_and_control_characters_before_database_access() -> None:
    with _client() as client:
        assert client.post("/api/semantic-vocabulary/resolve", json={"terms": ["bad\u0000term"]}).status_code == 422
        assert client.post("/api/semantic-vocabulary", json={**_folder(label="Bad\u0000label", aliases=[])}).status_code == 422
        assert client.post("/api/semantic-vocabulary", json={**_folder(label="Nul target", aliases=[]), "target_id": "INPUT\u0000"}).status_code == 422
        assert client.post("/api/semantic-vocabulary", json={**_folder(label="Nul description", aliases=[]), "description": "text\u0000"}).status_code == 422
        assert client.post("/api/semantic-vocabulary/resolve", json={"terms": ["normal"], "scope_project_id": "project-tv-001\u0000"}).status_code == 422
        created = client.post("/api/semantic-vocabulary", json=_folder(label="Allowed label", aliases=[]))
        assert created.status_code == 201
        assert client.put(f"/api/semantic-vocabulary/{created.json()['id']}", json={**_folder(label="Bad\u0000label", aliases=[]), "expected_revision": 1}).status_code == 422


@pytest.mark.duckdb_integration
def test_limit_and_missing_canonical_target_are_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.routers import semantic_vocabulary

    monkeypatch.setattr(semantic_vocabulary, "_ENTRY_LIMIT", 1)
    with _client() as client:
        assert client.post("/api/semantic-vocabulary", json=_folder(label="Only entry", aliases=[])).status_code == 201
        assert client.post("/api/semantic-vocabulary", json={**_folder(label="Over limit", aliases=[]), "key": "over_limit"}).status_code == 409

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute(
            "INSERT INTO semantic_vocabulary_entries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ["missing-project-entry", "missing_project", "Ghost project", "", "PROJECT", "deleted-project", None,
             json.dumps(["ghost"]), 1, True, now, now, "local-admin", "local-admin"],
        )
        conn.execute("INSERT INTO semantic_vocabulary_terms VALUES (?, ?, ?, ?)", ["missing-project-entry", "", "PROJECT", "ghost"])
    with _client() as client:
        listed = next(entry for entry in client.get("/api/semantic-vocabulary").json()["entries"] if entry["id"] == "missing-project-entry")
        assert listed["target_available"] is False and listed["target_label"] == "deleted-project"
        assert client.post("/api/semantic-vocabulary/resolve", json={"terms": ["ghost"]}).json()["matches"][0]["status"] == "UNMAPPED"
        disabled = client.put("/api/semantic-vocabulary/missing-project-entry", json={
            "key": "missing_project", "label": "Ghost project", "description": "", "target_kind": "PROJECT",
            "target_id": "deleted-project", "scope_project_id": None, "aliases": ["ghost"], "enabled": False, "expected_revision": 1,
        })
        assert disabled.status_code == 200
