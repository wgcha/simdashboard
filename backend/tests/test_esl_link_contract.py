"""ESL(등가정적하중) 플랫폼 링크 계약(docs/plans/physicsai-integration.md §ESL).

ESL 플랫폼은 PhysicsAI와 같은 방식으로 같은 주소의 ``/esl/``(API ``/esl/api``)에서
대시보드 세션 쿠키를 공유한다. 세션·``/api/auth/me``·``/api/projects`` 모양은
``test_physicsai_link_contract.py``가 고정하므로, 이 시험은 사이드바 "등가정적하중"
링크 표시 플래그(``SIMDASH_ESL_ENABLED``, 기본 off)와 응답 모양만 고정한다.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import esl_link_enabled, physicsai_link_enabled
from app.database import initialize_database
from app.main import app

from tests.test_physicsai_link_contract import PASSWORD, _cleanup, _insert_user


def test_esl_link_flag_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    initialize_database()
    suffix = uuid4().hex[:8]
    admin_id = _insert_user(f"esl-admin-{suffix}", admin=True, membership_role=None)
    outsider_id = _insert_user(f"esl-outsider-{suffix}", admin=False, membership_role=None)
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-secret-key-that-is-at-least-32-characters")
    monkeypatch.delenv("SIMDASH_ESL_ENABLED", raising=False)
    monkeypatch.delenv("SIMDASH_PHYSICSAI_ENABLED", raising=False)
    try:
        with TestClient(app) as client:
            login = client.post("/api/auth/login", json={"username": f"esl-outsider-{suffix}", "password": PASSWORD})
            assert login.status_code == 200
            client.cookies.clear()
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

            # Default off hides the sidebar link; any ACTIVE user (also a non-member) may read it.
            link = client.get("/api/integrations/esl", headers=headers)
            assert link.status_code == 200
            assert link.json() == {"enabled": False, "base_path": "/esl/"}
            monkeypatch.setenv("SIMDASH_ESL_ENABLED", "true")
            assert client.get("/api/integrations/esl", headers=headers).json() == {"enabled": True, "base_path": "/esl/"}
            # The two flags are independent: ESL on does not turn PhysicsAI on.
            assert client.get("/api/integrations/physicsai", headers=headers).json() == {"enabled": False, "base_path": "/physicsai/"}
            assert client.get("/api/integrations/esl").status_code == 401
    finally:
        _cleanup([admin_id, outsider_id], suffix)


@pytest.mark.unit
def test_esl_link_flag_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SIMDASH_ESL_ENABLED", raising=False)
    monkeypatch.delenv("SIMDASH_PHYSICSAI_ENABLED", raising=False)
    assert esl_link_enabled() is False
    for value in ("false", "0", "off", "", "no", "maybe"):
        monkeypatch.setenv("SIMDASH_ESL_ENABLED", value)
        assert esl_link_enabled() is False, value
    for value in ("true", "1", "on", "yes", " TRUE "):
        monkeypatch.setenv("SIMDASH_ESL_ENABLED", value)
        assert esl_link_enabled() is True, value
        assert physicsai_link_enabled() is False


@pytest.mark.unit
def test_esl_route_in_openapi() -> None:
    schema = app.openapi()
    operation = schema["paths"]["/api/integrations/esl"]["get"]
    ref = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("/EslLinkResponse")
    assert set(schema["components"]["schemas"]["EslLinkResponse"]["properties"]) == {"enabled", "base_path"}
