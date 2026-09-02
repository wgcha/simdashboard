from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import check_openapi_contract


pytestmark = pytest.mark.unit


def test_canonical_openapi_bytes_round_trip_without_diff(tmp_path: Path) -> None:
    schema: dict[object, object] = {"openapi": "3.1.0", "paths": {"/api/health": {}}}
    snapshot = tmp_path / "openapi.json"
    snapshot.write_bytes(check_openapi_contract.canonical_openapi_bytes(schema))

    assert check_openapi_contract.check_contract(snapshot, schema) == []


def test_contract_check_reports_stale_snapshot(tmp_path: Path) -> None:
    snapshot = tmp_path / "openapi.json"
    snapshot.write_text(json.dumps({"openapi": "3.0.0"}) + "\n", encoding="utf-8")

    violations = check_openapi_contract.check_contract(snapshot, {"openapi": "3.1.0"})

    assert violations[0].startswith("OpenAPI snapshot is stale")
    assert any(line.startswith("-") and "3.0.0" in line for line in violations)
