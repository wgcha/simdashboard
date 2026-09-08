from __future__ import annotations

import base64
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.database_connection import connect
from app.main import app
from app.security import hash_password
from app.services import modeling_templates

pytestmark = pytest.mark.duckdb_integration


def _encoded(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def test_csv_template_versions_preserve_bytes_paths_history_and_downloads() -> None:
    cp949 = "항목,값\n충격,42\n".encode("cp949")
    utf8 = b"metric,value\npeak,12\n"
    with TestClient(app) as client:
        created = client.post("/api/modeling-templates", json={"name": "Rear drop", "product_name": "TV_A", "load_case_name": "Drop_01", "description": "baseline"})
        assert created.status_code == 201, created.text
        card = created.json()
        assert card["latest_version"] == 1 and card["file_count"] == 0
        template_id = card["id"]
        v2 = client.post(f"/api/modeling-templates/{template_id}/versions", json={"expected_version": 1, "mode": "merge", "files": [{"relative_path": "inputs/korean.csv", "content_base64": _encoded(cp949)}, {"relative_path": "results/summary.csv", "content_base64": _encoded(utf8)}]})
        assert v2.status_code == 200, v2.text
        assert v2.json()["selected_version"]["version"] == 2
        merged = client.post(f"/api/modeling-templates/{template_id}/versions", json={"expected_version": 2, "mode": "merge", "files": [{"relative_path": "results/summary.csv", "content_base64": _encoded(b"metric,value\npeak,99\n")} ]})
        assert merged.status_code == 200
        assert {item["relative_path"] for item in merged.json()["files"]} == {"inputs/korean.csv", "results/summary.csv"}
        v3 = client.post(f"/api/modeling-templates/{template_id}/versions", json={"expected_version": 2, "mode": "replace", "files": [{"relative_path": "new/only.csv", "content_base64": _encoded(b"")} ]})
        assert v3.status_code == 409
        v4 = client.post(f"/api/modeling-templates/{template_id}/versions", json={"expected_version": 3, "mode": "replace", "files": [{"relative_path": "new/only.csv", "content_base64": _encoded(b"")} ]})
        assert v4.status_code == 200 and v4.json()["file_count"] == 1
        old = client.get(f"/api/modeling-templates/{template_id}/versions/2")
        assert {item["relative_path"] for item in old.json()["files"]} == {"inputs/korean.csv", "results/summary.csv"}
        old_file = next(item for item in old.json()["files"] if item["relative_path"] == "inputs/korean.csv")
        assert client.get(f"/api/modeling-templates/{template_id}/versions/2/files/{old_file['id']}/download").content == cp949
        archive = zipfile.ZipFile(io.BytesIO(client.get(f"/api/modeling-templates/{template_id}/versions/2/download").content))
        assert archive.read("inputs/korean.csv") == cp949 and archive.read("results/summary.csv") == utf8


def test_csv_template_search_validation_and_stale_version_conflict() -> None:
    with TestClient(app) as client:
        created = client.post("/api/modeling-templates", json={"name": "A_B%", "product_name": "Product_A", "load_case_name": "Case_A"})
        template_id = created.json()["id"]
        assert client.get("/api/modeling-templates", params={"q": "A_B%"}).json()["items"]
        invalid = client.post(f"/api/modeling-templates/{template_id}/versions", json={"expected_version": 1, "files": [{"relative_path": "../escape.csv", "content_base64": _encoded(b"x")} ]})
        assert invalid.status_code == 422
        collision = client.post(f"/api/modeling-templates/{template_id}/versions", json={"expected_version": 1, "files": [{"relative_path": "a.csv", "content_base64": _encoded(b"x")}, {"relative_path": "a.csv/nested.csv", "content_base64": _encoded(b"y")} ]})
        assert collision.status_code == 422
        duplicate = client.post(f"/api/modeling-templates/{template_id}/versions", json={"expected_version": 1, "files": [{"relative_path": "Folder/A.csv", "content_base64": _encoded(b"x")}, {"relative_path": "folder/a.CSV", "content_base64": _encoded(b"y")} ]})
        assert duplicate.status_code == 422
        assert client.get(f"/api/modeling-templates/{template_id}").json()["latest_version"] == 1
        accepted = client.post(f"/api/modeling-templates/{template_id}/versions", json={"expected_version": 1, "files": [{"relative_path": "good.csv", "content_base64": _encoded(b"x")} ]})
        assert accepted.status_code == 200
        stale = client.post(f"/api/modeling-templates/{template_id}/versions", json={"expected_version": 1, "files": []})
        assert stale.status_code == 409


def test_library_read_is_authorized_but_catalog_write_requires_global_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "modeling-template-test-secret-key-32chars")
    with connect() as conn:
        conn.execute("INSERT INTO users (id, username, password_hash, display_name, legacy_role, account_status, is_global_admin, is_active, created_at, updated_at) VALUES ('template-reader', 'template-reader', ?, 'Template Reader', 'viewer', 'ACTIVE', false, true, now(), now())", [hash_password("template-reader-password")])
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "template-reader", "password": "template-reader-password"})
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        assert client.get("/api/modeling-templates", headers=headers).status_code == 200
        denied = client.post("/api/modeling-templates", headers=headers, json={"name": "Denied", "product_name": "P", "load_case_name": "L"})
        assert denied.status_code == 403


def test_snapshot_limits_and_invalid_base64_are_rejected() -> None:
    with pytest.raises(modeling_templates.TemplateError):
        modeling_templates._validate_snapshot([modeling_templates.IncomingFile(f"f-{index}.csv", b"") for index in range(201)])
    with pytest.raises(modeling_templates.TemplateError):
        modeling_templates._validate_snapshot([modeling_templates.IncomingFile("large.csv", b"x" * (modeling_templates.MAX_TOTAL_BYTES + 1))])
    with TestClient(app) as client:
        created = client.post("/api/modeling-templates", json={"name": "Invalid data", "product_name": "P", "load_case_name": "L"}).json()
        rejected = client.post(f"/api/modeling-templates/{created['id']}/versions", json={"expected_version": 1, "files": [{"relative_path": "bad.csv", "content_base64": "not-base64!"}]})
        assert rejected.status_code == 422
