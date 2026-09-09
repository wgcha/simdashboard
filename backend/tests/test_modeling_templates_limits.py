from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.modeling_templates import _LimitedBodyRoute
from app.services import modeling_templates

pytestmark = pytest.mark.duckdb_integration


def test_receive_limit_rejects_before_json_parse_and_does_not_write(monkeypatch: pytest.MonkeyPatch) -> None:
    with TestClient(app) as client:
        before = client.get('/api/modeling-templates').json()['items']
        monkeypatch.setattr(_LimitedBodyRoute, 'max_body_bytes', 64)
        rejected = client.post('/api/modeling-templates', content=b' ' * 65, headers={'Content-Type': 'application/json'})
        assert rejected.status_code == 413, rejected.text
        assert client.get('/api/modeling-templates').json()['items'] == before


def test_aggregate_limits_and_hidden_path_collision_leave_snapshot_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    with TestClient(app) as client:
        card = client.post('/api/modeling-templates', json={'name': 'Limits', 'product_name': 'TV', 'load_case_name': 'DROP'}).json()
        endpoint = f"/api/modeling-templates/{card['id']}/versions"
        def file(path: str, content: bytes = b'a') -> dict:
            return {'relative_path': path, 'content_base64': base64.b64encode(content).decode()}
        monkeypatch.setattr(modeling_templates, 'MAX_TOTAL_BYTES', 3)
        assert client.post(endpoint, json={'expected_version': 1, 'files': [file('a.csv', b'ab'), file('b.csv', b'cd')]}).status_code == 422
        assert client.post(endpoint, json={'expected_version': 1, 'files': [file('a.csv'), file('a.csv-extra.csv'), file('a.csv/b.csv')]}).status_code == 422
        assert client.get(f"/api/modeling-templates/{card['id']}").json()['latest_version'] == 1
        assert client.post(endpoint, json={'expected_version': 1, 'files': [file('ok.csv', b'abc')]}).status_code == 200


def test_search_treats_sql_wildcard_characters_as_literal() -> None:
    with TestClient(app) as client:
        wanted = client.post('/api/modeling-templates', json={'name': 'A_B%', 'product_name': 'TV', 'load_case_name': 'DROP'}).json()
        client.post('/api/modeling-templates', json={'name': 'AxBy', 'product_name': 'TV', 'load_case_name': 'DROP'})
        matches = client.get('/api/modeling-templates', params={'q': 'A_B%'}).json()['items']
        assert [item['id'] for item in matches] == [wanted['id']]
