from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import check_architecture


pytestmark = pytest.mark.unit


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _baseline(path: Path, ceilings: dict[str, int]) -> Path:
    baseline = path / "baseline.json"
    baseline.write_text(json.dumps({"version": 1, "execute_call_ceilings": ceilings}), encoding="utf-8")
    return baseline


def test_checked_in_architecture_baseline_passes() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    baseline = backend_root / "scripts" / "architecture_baseline.json"
    assert check_architecture.check_project(backend_root, baseline) == []


def test_checked_in_baseline_cannot_be_raised_by_editing_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path / "app" / "main.py", "def query(conn):\n    return conn.execute('SELECT 1')\n")
    baseline = _baseline(tmp_path, {"app/main.py": 1})
    monkeypatch.setattr(check_architecture, "CHECKED_IN_BASELINE_PATH", baseline.resolve())

    violations = check_architecture.check_project(tmp_path, baseline)

    assert any("differs from DEFAULT_EXECUTE_CALL_CEILINGS" in violation for violation in violations)


def test_checker_rejects_new_router_database_access_and_domain_dependency(tmp_path: Path) -> None:
    _write(tmp_path / "app" / "main.py", "def ready():\n    return True\n")
    _write(tmp_path / "app" / "routers" / "new_router.py", "def route(conn):\n    return conn.execute('SELECT 1')\n")
    _write(tmp_path / "app" / "adapters" / "http" / "routers" / "projects.py", "def route(conn):\n    return conn.execute('SELECT 1')\n")
    _write(
        tmp_path / "app" / "domains" / "projects" / "policy.py",
        "from fastapi import HTTPException\nfrom app.database import connect\nfrom ...database_connection import connect as alternate_connect\nfrom ... import database\nfrom ...adapters import persistence\nfrom ...repositories import portfolio\n\ndef evaluate(conn):\n    return conn.execute('SELECT 1')\n",
    )
    violations = check_architecture.check_project(tmp_path, _baseline(tmp_path, {"app/main.py": 0}))

    assert any("app/routers/new_router.py: .execute() calls increased" in violation for violation in violations)
    assert any("app/adapters/http/routers/projects.py: .execute() calls increased" in violation for violation in violations)
    assert any("domain code must not import fastapi" in violation for violation in violations)
    assert any("domain code must not import app.database" in violation for violation in violations)
    assert any("domain code must not import app.database_connection" in violation for violation in violations)
    assert any("domain code must not import app.adapters.persistence" in violation for violation in violations)
    assert any("domain code must not import app.repositories" in violation for violation in violations)
    assert any("domain code must not call .execute()" in violation for violation in violations)


def test_checker_rejects_http_framework_dependency_in_persistence(tmp_path: Path) -> None:
    _write(tmp_path / "app" / "main.py", "def ready():\n    return True\n")
    _write(
        tmp_path / "app" / "adapters" / "persistence" / "requests.py",
        "from fastapi import Request\nfrom starlette.requests import Request as StarletteRequest\n",
    )

    violations = check_architecture.check_project(tmp_path, _baseline(tmp_path, {"app/main.py": 0}))

    assert any("persistence code must not import fastapi" in violation for violation in violations)
    assert any("persistence code must not import starlette.requests" in violation for violation in violations)


def test_checker_rejects_application_dependencies_with_exact_offending_module(tmp_path: Path) -> None:
    _write(tmp_path / "app" / "main.py", "def ready():\n    return True\n")
    _write(
        tmp_path / "app" / "application" / "projects" / "service.py",
        "import app\nimport fastapi\nfrom ... import database, domains\nfrom ...repositories import projects\n",
    )

    violations = check_architecture.check_project(tmp_path, _baseline(tmp_path, {"app/main.py": 0}))

    assert "app/application/projects/service.py:2: application code must not import fastapi" in violations
    assert "app/application/projects/service.py:3: application code must not import app.database" in violations
    assert "app/application/projects/service.py:4: application code must not import app.repositories" in violations
    assert not any("application code must not import app" == violation.rsplit(": ", 1)[-1] for violation in violations)
