from __future__ import annotations

from fastapi.testclient import TestClient

from app.database_connection import connect
from app.main import app


def _registry_row(row_id: str, role_kind: str, target_id: str, code: str, path: str) -> list[str]:
    return [row_id, "selection-test-root", path, role_kind, role_kind, "", code, "stale registry name", "", "", target_id]


def test_selection_lists_enrich_only_matching_entity_registry_rows() -> None:
    """One scoped list query exposes current labels and its own safe registry row."""

    project_id = "project-tv-001"
    request_id = "request-drop-001"
    load_case_id = "loadcase-drop-bottom-001"
    with connect() as connection:
        for values in (
            _registry_row("selection-project", "PROJECT", project_id, "0007", "Project/0007"),
            _registry_row("selection-request", "REQUEST", request_id, "REQ-001", "Project/0007/REQ-001"),
            _registry_row("selection-loadcase", "LOAD_CASE", load_case_id, "LC-004", "Project/0007/REQ-001/LC-004"),
        ):
            connection.execute(
                """
                INSERT INTO folder_discovery_registry
                    (id,root_key,relative_path,role,role_kind,scope_key,code,name,analysis_type,
                     parent_target_id,target_id,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                """,
                values,
            )

    with TestClient(app) as client:
        project = next(item for item in client.get("/api/projects").json() if item["id"] == project_id)
        request = next(
            item for item in client.get(f"/api/projects/{project_id}/requests").json() if item["id"] == request_id
        )
        load_case = next(
            item for item in client.get(f"/api/requests/{request_id}/load-cases").json() if item["id"] == load_case_id
        )

    assert project["selection_metadata"] == {
        "code": "0007",
        "name": project["name"],
        "project": None,
        "request": None,
        "analysis_type": None,
        "relative_path": "Project/0007",
    }
    assert request["selection_metadata"]["code"] == "REQ-001"
    assert request["selection_metadata"]["name"] == request["title"]
    assert request["selection_metadata"]["project"] == {"id": project_id, "name": project["name"]}
    assert load_case["selection_metadata"] == {
        "code": "LC-004",
        "name": load_case["name"],
        "project": {"id": project_id, "name": project["name"]},
        "request": {"id": request_id, "name": request["title"]},
        "analysis_type": load_case["analysis_type"],
        "relative_path": "Project/0007/REQ-001/LC-004",
    }
