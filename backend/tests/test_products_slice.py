from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.adapters.persistence.products import SQLProductInformationRepository
from app.application.products.queries import list_product_information
from app.config import database_settings
from app.database_connection import connect
from app.domains.products.models import ProductInformation
from app.domains.products.ports import ProductInformationRepository
from app.main import app
from app.security import hash_password


class FakeProductInformationRepository:
    def __init__(self, items: list[ProductInformation], events: list[str]) -> None:
        self._items = items
        self._events = events

    def list_for_load_case(self, load_case_id: str) -> list[ProductInformation]:
        self._events.append(f"list:{load_case_id}")
        return self._items


@pytest.mark.unit
def test_product_query_authorizes_before_repository_provider() -> None:
    events: list[str] = []
    expected: list[ProductInformation] = [
        {
            "category": "MODEL",
            "name": "제품 모델명",
            "value_text": "Model X",
            "file_path": None,
            "metadata": {"source": "project_registration"},
        }
    ]

    def authorize() -> object:
        events.append("authorize")
        return object()

    @contextmanager
    def provider() -> Iterator[ProductInformationRepository]:
        events.append("open")
        yield FakeProductInformationRepository(expected, events)
        events.append("close")

    assert list_product_information("load-case-a", authorize, provider) == expected
    assert events == ["authorize", "open", "list:load-case-a", "close"]


@pytest.mark.unit
def test_product_query_denial_does_not_open_repository() -> None:
    provider_opened = False

    def deny() -> object:
        raise PermissionError("denied")

    @contextmanager
    def provider() -> Iterator[ProductInformationRepository]:
        nonlocal provider_opened
        provider_opened = True
        yield FakeProductInformationRepository([], [])

    with pytest.raises(PermissionError, match="denied"):
        list_product_information("load-case-a", deny, provider)

    assert not provider_opened


@pytest.mark.unit
def test_sql_repository_preserves_order_nulls_and_metadata_shapes() -> None:
    class Cursor:
        description = [
            ("category",),
            ("name",),
            ("value_text",),
            ("file_path",),
            ("metadata_json",),
        ]

        def fetchall(self) -> list[tuple[Any, ...]]:
            return [
                ("MODEL", "제품 모델명", "Model X", None, '{"source":"seed"}'),
                ("SPEC", "화면 크기", None, "spec.pdf", None),
                ("SPEC", "화면 정보", "65 inch", None, {"diagonal_inch": 65}),
                ("TEXT", "원문", "legacy", None, "not-json"),
            ]

    class Connection:
        def execute(self, statement: str, parameters: Any | None = None) -> Cursor:
            assert "JOIN analysis_requests" in statement
            assert "JOIN product_information" in statement
            assert "ORDER BY pi.category, pi.name" in statement
            assert parameters == ["load-case-a"]
            return Cursor()

    assert SQLProductInformationRepository(Connection()).list_for_load_case("load-case-a") == [  # type: ignore[arg-type]
        {
            "category": "MODEL",
            "name": "제품 모델명",
            "value_text": "Model X",
            "file_path": None,
            "metadata": {"source": "seed"},
        },
        {
            "category": "SPEC",
            "name": "화면 크기",
            "value_text": None,
            "file_path": "spec.pdf",
            "metadata": None,
        },
        {
            "category": "SPEC",
            "name": "화면 정보",
            "value_text": "65 inch",
            "file_path": None,
            "metadata": {"diagonal_inch": 65},
        },
        {
            "category": "TEXT",
            "name": "원문",
            "value_text": "legacy",
            "file_path": None,
            "metadata": "not-json",
        },
    ]


def _expected_product_information(load_case_id: str) -> list[dict[str, Any]]:
    with connect() as connection:
        stored = connection.execute(
            """
            SELECT pi.category, pi.name, pi.value_text, pi.file_path, pi.metadata_json
            FROM load_cases lc
            JOIN analysis_requests ar ON ar.id=lc.request_id
            JOIN product_information pi ON pi.project_id=ar.project_id
            WHERE lc.id=? ORDER BY pi.category, pi.name
            """,
            [load_case_id],
        ).fetchall()
    return [
        {
            "category": category,
            "name": name,
            "value_text": value_text,
            "file_path": file_path,
            "metadata": json.loads(metadata) if isinstance(metadata, str) else metadata,
        }
        for category, name, value_text, file_path, metadata in stored
    ]


@pytest.mark.contract
def test_overview_preserves_product_payload_with_run_and_no_data() -> None:
    with connect() as connection:
        with_run = connection.execute(
            """
            SELECT lc.id FROM load_cases lc
            JOIN analysis_runs run ON run.load_case_id=lc.id
            ORDER BY lc.id LIMIT 1
            """
        ).fetchone()
        without_run = connection.execute(
            """
            SELECT lc.id FROM load_cases lc
            LEFT JOIN analysis_runs run ON run.load_case_id=lc.id
            WHERE run.id IS NULL ORDER BY lc.id LIMIT 1
            """
        ).fetchone()
    assert with_run is not None
    assert without_run is not None

    with TestClient(app) as client:
        run_response = client.get(f"/api/load-cases/{with_run[0]}/overview")
        no_data_response = client.get(f"/api/load-cases/{without_run[0]}/overview")

    assert run_response.status_code == 200
    assert run_response.json()["product_information"] == _expected_product_information(with_run[0])
    assert no_data_response.status_code == 200
    assert no_data_response.json()["run"] is None
    assert no_data_response.json()["overall_verdict"] == "NO_DATA"
    assert no_data_response.json()["product_information"] == _expected_product_information(without_run[0])


@pytest.mark.contract
def test_overview_missing_load_case_preserves_exact_404() -> None:
    with TestClient(app) as client:
        response = client.get("/api/load-cases/load-case-does-not-exist/overview")

    assert response.status_code == 404
    assert response.json() == {"detail": "하중 경우를 찾을 수 없습니다."}


@pytest.mark.contract
def test_overview_authorized_product_contract() -> None:
    load_case_id = "loadcase-drop-bottom-001"
    with TestClient(app) as client:
        response = client.get(f"/api/load-cases/{load_case_id}/overview")

    assert response.status_code == 200
    assert response.json()["product_information"] == _expected_product_information(load_case_id)


@pytest.mark.contract
def test_active_nonmember_keeps_company_product_read_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suffix = uuid4().hex[:10]
    user_id = f"user-product-{suffix}"
    username = f"product-{suffix}"
    password = "product-read-password"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO users
                (id, username, password_hash, display_name, legacy_role, is_active,
                 created_at, updated_at, account_status, is_global_admin)
            VALUES (?, ?, ?, ?, 'viewer', true, ?, ?, 'ACTIVE', false)
            """,
            [user_id, username, hash_password(password), "Product Reader", now, now],
        )
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-secret-key-that-is-at-least-32-characters")

    try:
        with TestClient(app) as client:
            login = client.post("/api/auth/login", json={"username": username, "password": password})
            assert login.status_code == 200
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            response = client.get(
                "/api/load-cases/loadcase-drop-bottom-001/overview",
                headers=headers,
            )
        assert response.status_code == 200
        assert response.json()["product_information"]
    finally:
        with connect() as connection:
            if database_settings().backend == "duckdb":
                connection.execute("DELETE FROM audit_events WHERE user_id=?", [user_id])
            connection.execute("DELETE FROM project_memberships WHERE user_id=?", [user_id])
            connection.execute("DELETE FROM users WHERE id=?", [user_id])
