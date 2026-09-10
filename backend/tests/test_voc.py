from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.database import initialize_database
from app.database_connection import connect
from app.security import Principal, create_access_token, hash_password
from app.services.voc_service import _excel_safe


def test_voc_posts_are_server_attributed_paginated_and_exportable(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "disabled")
    first_content = '=SUM(1,1)\n<script>"unsafe"</script>'
    second_content = "A normal request"

    with TestClient(app) as client:
        rejected = client.post("/api/voc/posts", json={"content": "valid content", "author_username": "forged"})
        assert rejected.status_code == 422

        blank = client.post("/api/voc/posts", json={"content": "  \n\t "})
        assert blank.status_code == 422
        nul = client.post("/api/voc/posts", json={"content": "not\u0000allowed"})
        assert nul.status_code == 422

        first = client.post("/api/voc/posts", json={"content": f"  {first_content}  "})
        assert first.status_code == 201
        first_post = first.json()
        assert first_post["content"] == first_content
        assert first_post["author_user_id"] == "local-admin"
        assert first_post["author_username"] == "local"
        assert first_post["author_display_name"] == "로컬 관리자"
        assert first_post["created_at"].endswith(("Z", "+00:00"))

        second = client.post("/api/voc/posts", json={"content": second_content})
        assert second.status_code == 201
        second_post = second.json()

        page = client.get("/api/voc/posts?limit=1&offset=1")
        assert page.status_code == 200
        assert page.json()["total"] == 2
        assert page.json()["limit"] == 1
        assert page.json()["offset"] == 1
        assert [item["id"] for item in page.json()["items"]] == [first_post["id"]]
        assert client.get("/api/voc/posts?limit=101").status_code == 422

        json_export = client.get("/api/voc/export?format=json")
        assert json_export.status_code == 200
        assert json_export.headers["content-disposition"] == 'attachment; filename="voc-posts.json"'
        exported_json = json_export.json()
        assert {post["id"] for post in exported_json} == {first_post["id"], second_post["id"]}
        assert next(post for post in exported_json if post["id"] == first_post["id"])["content"] == first_content

        csv_export = client.get("/api/voc/export?format=csv")
        assert csv_export.status_code == 200
        assert csv_export.headers["content-type"].startswith("text/csv; charset=utf-8")
        assert csv_export.content.startswith(b"\xef\xbb\xbf")
        exported_csv = list(csv.DictReader(io.StringIO(csv_export.content.decode("utf-8-sig"))))
        assert exported_csv[0].keys() == {
            "id", "author_user_id", "author_username", "author_display_name", "content", "created_at"
        }
        assert next(row for row in exported_csv if row["id"] == first_post["id"])["content"] == "'" + first_content


def test_voc_requires_an_active_account_and_reads_current_author_identity(monkeypatch) -> None:
    initialize_database()
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "voc-test-secret-key-that-is-at-least-32-chars")
    suffix = uuid4().hex[:10]
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    def insert_user(
        user_id: str,
        username: str,
        display_name: str,
        status: str,
        password: str | None,
        legacy_role: str = "viewer",
    ) -> None:
        with connect() as connection:
            connection.execute(
                """
                INSERT INTO users
                    (id, username, password_hash, display_name, legacy_role, is_active,
                     created_at, updated_at, account_status, is_global_admin)
                VALUES (?, ?, ?, ?, ?, true, ?, ?, ?, false)
                """,
                [user_id, username, hash_password(password) if password else None, display_name, legacy_role, now, now, status],
            )

    admin_id = f"voc-admin-{suffix}"
    member_id = f"voc-member-{suffix}"
    pending_id = f"voc-pending-{suffix}"
    suspended_id = f"voc-suspended-{suffix}"
    power_id = f"voc-power-{suffix}"
    project_admin_id = f"voc-project-admin-{suffix}"
    bootstrap_admin_id = f"voc-bootstrap-admin-{suffix}"
    admin_password = "voc-admin-password"
    member_password = "voc-member-password"
    bootstrap_admin_password = "voc-bootstrap-admin-password"
    insert_user(admin_id, f"voc-admin-{suffix}", "VOC Admin", "ACTIVE", admin_password)
    with connect() as connection:
        connection.execute("UPDATE users SET is_global_admin=true, legacy_role='admin' WHERE id=?", [admin_id])
    insert_user(
        bootstrap_admin_id,
        f"voc-bootstrap-admin-{suffix}",
        "VOC Bootstrap Admin",
        "ACTIVE",
        bootstrap_admin_password,
        "admin",
    )
    with connect() as connection:
        connection.execute("UPDATE users SET is_global_admin=true WHERE id=?", [bootstrap_admin_id])
    insert_user(member_id, f"voc-member-{suffix}", "Current Member", "ACTIVE", member_password)
    insert_user(pending_id, f"voc-pending-{suffix}", "Pending Member", "PENDING", None)
    insert_user(suspended_id, f"voc-suspended-{suffix}", "Suspended Member", "SUSPENDED", None)
    insert_user(power_id, f"voc-power-{suffix}", "Power Member", "ACTIVE", None, "editor")
    insert_user(project_admin_id, f"voc-project-admin-{suffix}", "Project Admin", "ACTIVE", None, "admin")
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO project_memberships
                (id, project_id, user_id, role, created_by, created_at, updated_by, updated_at)
            VALUES (?, 'project-tv-001', ?, ?, 'test', ?, 'test', ?)
            """,
            [f"voc-membership-power-{suffix}", power_id, "power", now, now],
        )
        connection.execute(
            """
            INSERT INTO project_memberships
                (id, project_id, user_id, role, created_by, created_at, updated_by, updated_at)
            VALUES (?, 'project-tv-001', ?, ?, 'test', ?, 'test', ?)
            """,
            [f"voc-membership-admin-{suffix}", project_admin_id, "admin", now, now],
        )

    with TestClient(app) as client:
        assert client.get("/api/voc/posts").status_code == 401
        assert client.post("/api/voc/posts", json={"content": "anonymous request"}).status_code == 401
        assert client.get("/api/voc/export?format=json").status_code == 401
        assert client.get("/api/voc/export?format=csv").status_code == 401
        login = client.post("/api/auth/login", json={"username": f"voc-member-{suffix}", "password": member_password})
        assert login.status_code == 200
        active_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        created = client.post("/api/voc/posts", headers=active_headers, json={"content": "active request"})
        assert created.status_code == 201
        assert created.json()["author_user_id"] == member_id
        assert created.json()["author_username"] == f"voc-member-{suffix}"
        assert created.json()["author_display_name"] == "Current Member"
        assert client.get("/api/voc/posts", headers=active_headers).status_code == 200
        for format in ("csv", "json"):
            assert client.get(f"/api/voc/export?format={format}", headers=active_headers).status_code == 403

        for user_id, username, display_name in (
            (power_id, f"voc-power-{suffix}", "Power Member"),
            (project_admin_id, f"voc-project-admin-{suffix}", "Project Admin"),
        ):
            token, _ = create_access_token(Principal(user_id, username, display_name, "ACTIVE", False, None))
            headers = {"Authorization": f"Bearer {token}"}
            for format in ("csv", "json"):
                assert client.get(f"/api/voc/export?format={format}", headers=headers).status_code == 403

        admin_login = client.post("/api/auth/login", json={"username": f"voc-admin-{suffix}", "password": admin_password})
        assert admin_login.status_code == 200
        global_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
        for format in ("csv", "json"):
            assert client.get(f"/api/voc/export?format={format}", headers=global_headers).status_code == 200
        with connect() as connection:
            connection.execute("UPDATE users SET is_global_admin=false WHERE id=?", [admin_id])
        for format in ("csv", "json"):
            assert client.get(f"/api/voc/export?format={format}", headers=global_headers).status_code == 403

        for user_id, username, display_name, status in (
            (pending_id, f"voc-pending-{suffix}", "Pending Member", "PENDING"),
            (suspended_id, f"voc-suspended-{suffix}", "Suspended Member", "SUSPENDED"),
        ):
            token, _ = create_access_token(Principal(user_id, username, display_name, status, False, None))
            headers = {"Authorization": f"Bearer {token}"}
            assert client.get("/api/voc/posts", headers=headers).status_code == 403
            assert client.post("/api/voc/posts", headers=headers, json={"content": "blocked request"}).status_code == 403
            for format in ("csv", "json"):
                assert client.get(f"/api/voc/export?format={format}", headers=headers).status_code == 403


def test_voc_csv_formula_protection_handles_leading_whitespace() -> None:
    assert _excel_safe("=SUM(1,1)") == "'=SUM(1,1)"
    assert _excel_safe("\t@SUM(1,1)") == "'\t@SUM(1,1)"
    assert _excel_safe("\r\n+1+1") == "'\r\n+1+1"
    assert _excel_safe(" ordinary text") == " ordinary text"
