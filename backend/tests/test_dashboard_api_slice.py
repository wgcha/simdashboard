from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database import initialize_database
from app.database_connection import connect
from app.main import app
from app.security import hash_password


pytestmark = pytest.mark.duckdb_integration
PASSWORD = "dashboard-api-test-password"


def _request_context() -> tuple[str, str]:
    with connect() as connection:
        row = connection.execute(
            "SELECT project_id,id FROM analysis_requests ORDER BY id LIMIT 1"
        ).fetchone()
    assert row is not None
    return str(row[0]), str(row[1])


def _create_user(project_id: str, *, role: str, global_admin: bool = False) -> str:
    suffix = uuid4().hex[:10]
    user_id = f"dashboard-api-{role}-{suffix}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO users
                (id,username,password_hash,display_name,legacy_role,account_status,
                 is_global_admin,is_active,created_at,updated_at)
            VALUES (?,?,?,?,?,'ACTIVE',?,true,?,?)
            """,
            [
                user_id,
                user_id,
                hash_password(PASSWORD),
                user_id,
                "admin" if global_admin else ("editor" if role == "power" else "viewer"),
                global_admin,
                now,
                now,
            ],
        )
        if not global_admin:
            connection.execute(
                """
                INSERT INTO project_memberships
                    (id,project_id,user_id,role,created_by,created_at,updated_by,updated_at)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                [f"membership-{suffix}", project_id, user_id, role, user_id, now, user_id, now],
            )
    return user_id


def _login(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _capture_body(project_id: str, request_id: str, relative: str, environment: str):
    return {
        "project_id": project_id,
        "request_id": request_id,
        "root_relative_path": relative,
        "environment": environment,
    }


def test_usage_case_capture_and_five_evaluations_need_no_legacy_load_case(tmp_path, monkeypatch):
    root = tmp_path / "request-only-shared"
    files = {
        "Settle/model_settle_result.json": {"Set Tilt Angle @ Settle (deg)": 1.18},
        "Wobble/model_wobble_center_front_result.json": {"Wobble Disp. (mm)": -2.5},
        "Horizontal_Force_Angle/model_horizontal_force_angle_front_result.json": {"Set Tilt Angle Difference (deg)": 3.0},
        "Slope_Angle/model_slope_angle_front_result.json": {"Slope Angle (deg)": 8.0, "OK/NG": "OK"},
        "Slope_Angle_360/model_slope_angle_front_360_result.json": {"OK/NG": "NG"},
    }
    for relative, values in files.items():
        path = root / "usage-case" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(values), encoding="utf-8")
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root))
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "request-only-dashboard-test-secret-at-least-32-characters")
    initialize_database()
    project_id, _ = _request_context()
    request_id = "usage-request-without-load-case"
    with connect() as conn:
        conn.execute("""INSERT INTO analysis_requests
            (id,project_id,title,status,owner,requested_at,due_at,overall_note)
            VALUES (?,?,'Usage request','READY','test',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'')""",
            [request_id, project_id])
        assert conn.execute("SELECT count(*) FROM load_cases WHERE request_id=?", [request_id]).fetchone()[0] == 0
    admin = _create_user(project_id, role="admin", global_admin=True)
    with TestClient(app) as client:
        headers = _login(client, admin)
        result = client.post("/api/dashboard/captures", headers=headers,
            json=_capture_body(project_id, request_id, "usage-case", "USAGE"))
        assert result.status_code == 200, result.text
        capture = result.json()
        catalog = client.get("/api/dashboard/catalog", headers=headers,
            params={"request_id": request_id, "environment": "USAGE"})
        assert catalog.status_code == 200, catalog.text
        assert [item["id"] for item in catalog.json()["cases"]] == [capture["case_id"]]
        assert catalog.json()["load_cases"] == []
        response = client.get(f"/api/dashboard/usage/cases/{capture['case_id']}", headers=headers,
            params={"capture_id": capture["id"]})
        assert response.status_code == 200, response.text
        rows = {item["id"]: item for item in response.json()["evaluations"]}
        assert len(rows) == 5
        assert rows["Settle"]["common"]["value"] == 1.18
        assert rows["Wobble"]["front"]["value"] == -2.5
        assert rows["Slope_Angle_360"]["front"]["verdict"] == "NG"
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM load_cases WHERE request_id=?", [request_id]).fetchone()[0] == 0


def test_dashboard_scan_publish_read_permissions_and_asset_ranges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "shared"
    usage = root / "usage-case" / "Settle"
    usage.mkdir(parents=True)
    (usage / "model_settle_result.json").write_text(
        '{"Set Tilt Angle @ Settle (deg)":1.18}', encoding="utf-8"
    )
    image_bytes = b"synthetic-dashboard-image"
    (usage / "model_settle.jpg").write_bytes(image_bytes)
    reference_usage = root / "usage-reference" / "Settle"
    reference_usage.mkdir(parents=True)
    (reference_usage / "model_settle_result.json").write_text(
        '{"Set Tilt Angle @ Settle (deg)":2.25}', encoding="utf-8"
    )
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root))
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv(
        "AUTH_SECRET_KEY", "dashboard-api-test-secret-key-at-least-32-characters"
    )
    initialize_database()
    project_id, request_id = _request_context()
    admin = _create_user(project_id, role="admin", global_admin=True)
    power = _create_user(project_id, role="power")
    viewer = _create_user(project_id, role="general")

    with TestClient(app) as client:
        admin_headers = _login(client, admin)
        power_headers = _login(client, power)
        viewer_headers = _login(client, viewer)

        scan = client.post(
            "/api/dashboard/scans",
            headers=admin_headers,
            json={
                "project_id": project_id,
                "request_id": request_id,
                "root_relative_path": "usage-case",
                "environment": "USAGE",
            },
        )
        assert scan.status_code == 200, scan.text
        assert [item["root_relative_path"] for item in scan.json()["cases"]] == [
            "usage-case"
        ]
        assert scan.json()["storage_root_id"].startswith("dashboard-root-")
        assert str(root).casefold() not in scan.json()["storage_root_id"].casefold()
        automatic = client.post(
            "/api/dashboard/scans",
            headers=admin_headers,
            json={
                "project_id": project_id,
                "request_id": request_id,
                "root_relative_path": "",
                "environment": "USAGE",
            },
        )
        assert automatic.status_code == 200
        assert automatic.json()["cases"] == []
        assert client.post(
            "/api/dashboard/scans",
            headers=power_headers,
            json={
                "project_id": project_id,
                "request_id": request_id,
                "root_relative_path": "usage-case",
                "environment": "USAGE",
            },
        ).status_code == 403
        assert client.post(
            "/api/dashboard/scans",
            headers=admin_headers,
            json={
                "project_id": project_id,
                "request_id": request_id,
                "root_relative_path": "../outside",
                "environment": "USAGE",
            },
        ).status_code == 422
        assert client.post(
            "/api/dashboard/scans",
            headers=admin_headers,
            json={
                "project_id": project_id,
                "request_id": request_id,
                "root_relative_path": "missing-case",
                "environment": "USAGE",
            },
        ).status_code == 422

        published = client.post(
            "/api/dashboard/captures",
            headers=admin_headers,
            json=_capture_body(project_id, request_id, "usage-case", "USAGE"),
        )
        assert published.status_code == 200, published.text
        capture = published.json()
        assert client.post(
            "/api/dashboard/captures",
            headers=power_headers,
            json=_capture_body(project_id, request_id, "usage-case", "USAGE"),
        ).status_code == 200

        usage_response = client.get(
            f"/api/dashboard/usage/cases/{capture['case_id']}",
            params={"capture_id": capture["id"]},
            headers=viewer_headers,
        )
        assert usage_response.status_code == 200, usage_response.text
        settle = next(
            item for item in usage_response.json()["evaluations"] if item["id"] == "Settle"
        )
        assert settle["common"]["value"] == 1.18
        reference_capture = client.post(
            "/api/dashboard/captures",
            headers=admin_headers,
            json=_capture_body(project_id, request_id, "usage-reference", "USAGE"),
        ).json()
        referenced = client.get(
            f"/api/dashboard/usage/cases/{capture['case_id']}",
            params={
                "capture_id": capture["id"],
                "reference_case_id": reference_capture["case_id"],
                "reference_capture_id": reference_capture["id"],
            },
            headers=viewer_headers,
        )
        assert referenced.status_code == 200, referenced.text
        referenced_settle = next(
            item for item in referenced.json()["evaluations"] if item["id"] == "Settle"
        )
        assert referenced_settle["reference"]["status"] == "READY"
        assert referenced_settle["reference"]["common"]["value"] == 2.25
        asset_id = settle["media"][0]["asset_id"]

        full = client.get(f"/api/dashboard/assets/{asset_id}", headers=viewer_headers)
        assert full.status_code == 200
        assert full.content == image_bytes
        assert full.headers["x-content-type-options"] == "nosniff"
        ranged = client.get(
            f"/api/dashboard/assets/{asset_id}",
            headers={**viewer_headers, "Range": "bytes=2-6"},
        )
        assert ranged.status_code == 206
        assert ranged.content == image_bytes[2:7]
        assert ranged.headers["content-range"] == f"bytes 2-6/{len(image_bytes)}"
        invalid_range = client.get(
            f"/api/dashboard/assets/{asset_id}",
            headers={**viewer_headers, "Range": "bytes=999-1000"},
        )
        assert invalid_range.status_code == 416
        client.cookies.clear()
        assert client.get(f"/api/dashboard/assets/{asset_id}").status_code == 401


def test_distribution_basis_context_and_historical_capture_are_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "shared"
    scene = (
        root
        / "distribution-case"
        / "Drop"
        / "run-a"
        / "INDIVIDUAL"
        / "1_Face_Drop_Scene01_Face1_1st"
    )
    scene.mkdir(parents=True)
    summary = scene / "MAX_RESULT_Max_Stress_P1 (major)_Mid_C23_scene.h3d.csv"
    summary.write_text(
        "Position,Layer_1,Layer_2,Layer_3,Layer_4\nTOP,10,20,30,40\n",
        encoding="utf-8",
    )
    (scene / "CONTOUR_COMP23_Max_Stress_P1 (major)_Mid.jpg").write_bytes(
        b"synthetic-contour"
    )
    scene_b = (
        root
        / "distribution-case-b"
        / "Drop"
        / "run-b"
        / "INDIVIDUAL"
        / "1_Face_Drop_Scene01_Face1_1st"
    )
    scene_b.mkdir(parents=True)
    (scene_b / "MAX_RESULT_Max_Stress_P1 (major)_Mid_C23_scene.h3d.csv").write_text(
        "Position,Layer_1,Layer_2,Layer_3,Layer_4\nTOP,110,120,130,140\n",
        encoding="utf-8",
    )
    (scene_b / "CONTOUR_COMP23_Max_Stress_P1 (major)_Mid.jpg").write_bytes(
        b"synthetic-contour-b"
    )
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root))
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv(
        "AUTH_SECRET_KEY", "dashboard-api-test-secret-key-at-least-32-characters"
    )
    initialize_database()
    project_id, request_id = _request_context()
    admin = _create_user(project_id, role="admin", global_admin=True)

    with TestClient(app) as client:
        headers = _login(client, admin)
        body = _capture_body(
            project_id, request_id, "distribution-case", "DISTRIBUTION"
        )
        first_response = client.post(
            "/api/dashboard/captures", headers=headers, json=body
        )
        assert first_response.status_code == 200, first_response.text
        first = first_response.json()
        first_run = first["payload"]["runs"][0]
        first_scene = first_run["scenes"][0]

        params = {
            "capture_id": first["id"],
            "mode": "INDIVIDUAL",
            "component_id": "C23",
            "basis": "REPORTED_SUMMARY",
            "edge_keys": "TOP",
            "line_indices": "1,2,3,4",
        }
        projected = client.get(
            f"/api/dashboard/distribution/runs/{first_run['id']}",
            params=params,
            headers=headers,
        )
        assert projected.status_code == 200, projected.text
        projection = projected.json()
        assert projection["context"]["capture_id"] == first["id"]
        assert projection["context"]["mode"] == "INDIVIDUAL"
        assert projection["context"]["component_id"] == "C23"
        assert projection["context"]["basis"] == "REPORTED_SUMMARY"
        assert projection["series"][0]["selected_edge_envelope"] == 40
        assert projection["contours"][0]["asset"]["frame_role"] == "UNKNOWN"

        wrong_mode = client.get(
            f"/api/dashboard/distribution/runs/{first_run['id']}",
            params={**params, "mode": "CUMULATIVE"},
            headers=headers,
        )
        assert wrong_mode.status_code == 422
        detail_without_detail_source = client.get(
            f"/api/dashboard/distribution/runs/{first_run['id']}",
            params={**params, "basis": "DETAIL"},
            headers=headers,
        )
        assert detail_without_detail_source.status_code == 200
        assert detail_without_detail_source.json()["series"][0]["selected_edge_envelope"] is None

        scene_detail = client.get(
            f"/api/dashboard/distribution/scenes/{first_scene['id']}",
            params={
                **params,
                "run_id": first_run["id"],
                "position": "TOP",
            },
            headers=headers,
        )
        assert scene_detail.status_code == 200, scene_detail.text
        assert scene_detail.json()["context"]["scene_id"] == first_scene["id"]

        summary.write_text(
            "Position,Layer_1,Layer_2,Layer_3,Layer_4\nTOP,12,22,32,42\n",
            encoding="utf-8",
        )
        second_response = client.post(
            "/api/dashboard/captures", headers=headers, json=body
        )
        assert second_response.status_code == 200, second_response.text
        second = second_response.json()
        assert second["id"] != first["id"]

        old = client.get(
            f"/api/dashboard/distribution/runs/{first_run['id']}",
            params=params,
            headers=headers,
        )
        new = client.get(
            f"/api/dashboard/distribution/runs/{second['payload']['runs'][0]['id']}",
            params={**params, "capture_id": second["id"]},
            headers=headers,
        )
        assert old.json()["series"][0]["selected_edge_envelope"] == 40
        assert new.json()["series"][0]["selected_edge_envelope"] == 42

        case_b_response = client.post(
            "/api/dashboard/captures",
            headers=headers,
            json=_capture_body(
                project_id, request_id, "distribution-case-b", "DISTRIBUTION"
            ),
        )
        assert case_b_response.status_code == 200, case_b_response.text
        case_b = case_b_response.json()
        run_b = case_b["payload"]["runs"][0]
        members = [
            {
                "simulation_case_id": first["case_id"],
                "load_case_id": first_run["load_case_id"],
                "execution_run_id": first_run["id"],
                "capture_id": first["id"],
                "mode": "INDIVIDUAL",
                "component_id": "C23",
                "basis": "REPORTED_SUMMARY",
            },
            {
                "simulation_case_id": case_b["case_id"],
                "load_case_id": run_b["load_case_id"],
                "execution_run_id": run_b["id"],
                "capture_id": case_b["id"],
                "mode": "INDIVIDUAL",
                "component_id": "C23",
                "basis": "REPORTED_SUMMARY",
            },
        ]
        compared = client.post(
            "/api/dashboard/distribution/comparison",
            headers=headers,
            json={"members": members, "edge_keys": "TOP", "line_indices": "1,2,3,4"},
        )
        assert compared.status_code == 200, compared.text
        comparison = compared.json()
        assert len(comparison["members"]) == 2
        assert len(comparison["scenes"]) == 2
        assert "CASE_SCENE_ALIGNMENT_UNCONFIRMED" in comparison["quality_issues"]
        assert {item["selected_edge_envelope"] for item in comparison["series"]} == {
            40,
            140,
        }
        assert client.post(
            "/api/dashboard/distribution/comparison",
            headers=headers,
            json={"members": [members[0], members[0]]},
        ).status_code == 422
