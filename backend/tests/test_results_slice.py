from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient

from app.adapters.persistence.results import SQLAnalysisRunSummaryRepository, _json_value
from app.application.results.queries import list_analysis_runs
from app.config import database_settings
from app.database_connection import connect, rows
from app.domains.results.models import AnalysisRun, RunEvidence, RunSummaryReadData
from app.domains.results.policies import summarize_run
from app.domains.results.ports import AnalysisRunSummaryRepository
from app.main import app
from app.security import hash_password


RUN: AnalysisRun = {
    "id": "run-a",
    "load_case_id": "load-a",
    "template_execution_id": None,
    "run_no": 1,
    "solver": None,
    "status": "COMPLETED",
    "started_at": datetime(2025, 1, 1),
    "completed_at": datetime(2025, 1, 2),
}


def _evidence(**overrides: Any) -> RunEvidence:
    evidence: RunEvidence = {
        "scalar_count": 1,
        "series_count": 2,
        "failed_scalar_verdicts": 0,
        "scalar_verdicts": 1,
        "source_exists": True,
        "result_keys": {"stress"},
        "result_units": [("stress", "MPa")],
        "validation_verdicts": ["PASS"],
    }
    evidence.update(overrides)
    return evidence


@pytest.mark.unit
@pytest.mark.parametrize(
    ("run_status", "evidence", "overall", "trust"),
    [
        ("COMPLETED", _evidence(), "PASS", "TRUSTED"),
        ("COMPLETED", _evidence(failed_scalar_verdicts=1), "FAIL", "TRUSTED"),
        ("COMPLETED", _evidence(scalar_verdicts=0), "NO_DATA", "TRUSTED"),
        ("RUNNING", _evidence(), "PASS", "FAIL"),
        ("COMPLETED", _evidence(source_exists=False), "PASS", "WARN"),
        ("COMPLETED", _evidence(result_keys={"stress", "extra"}), "PASS", "WARN"),
        ("COMPLETED", _evidence(result_keys=set()), "PASS", "WARN"),
        ("COMPLETED", _evidence(result_units=[("stress", "Pa")]), "PASS", "WARN"),
        ("COMPLETED", _evidence(validation_verdicts=[]), "PASS", "WARN"),
        ("COMPLETED", _evidence(validation_verdicts=["FAIL"]), "PASS", "FAIL"),
    ],
)
def test_run_summary_policy_preserves_verdict_and_trust_rules(
    run_status: str,
    evidence: RunEvidence,
    overall: str,
    trust: str,
) -> None:
    summary = summarize_run({**RUN, "status": run_status}, evidence, {"stress": "MPa"}, is_latest=True)
    assert summary["overall_verdict"] == overall
    assert summary["trust_status"] == trust


@pytest.mark.unit
def test_run_query_denial_does_not_open_provider() -> None:
    opened = False

    def deny() -> object:
        raise PermissionError("denied")

    @contextmanager
    def provider() -> Iterator[AnalysisRunSummaryRepository]:
        nonlocal opened
        opened = True
        raise AssertionError("must not open")
        yield  # pragma: no cover

    with pytest.raises(PermissionError, match="denied"):
        list_analysis_runs("load-a", deny, provider)
    assert not opened


class FakeCursor:
    def __init__(self, records: list[tuple[Any, ...]], columns: list[str] | None = None) -> None:
        self._records = records
        self.description = [(column,) for column in (columns or [])]

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._records


class QueryCountingConnection:
    def __init__(self, runs: int) -> None:
        self.runs = runs
        self.execute_count = 0
        self.statements: list[str] = []

    def execute(self, statement: str, parameters: Any | None = None) -> FakeCursor:
        self.execute_count += 1
        self.statements.append(statement)
        if statement.strip().startswith("SELECT * FROM analysis_runs"):
            records = [
                (f"run-{index}", "load-a", None, index, None, "COMPLETED", None, None)
                for index in range(self.runs, 0, -1)
            ]
            return FakeCursor(records, list(RUN.keys()))
        if "result_type" in statement:
            return FakeCursor([])
        if "source_kind" in statement:
            return FakeCursor([])
        if "AS has_unit" in statement:
            return FakeCursor([])
        if "variable_definitions" in statement:
            return FakeCursor([])
        if "FROM validations" in statement:
            return FakeCursor([])
        raise AssertionError(statement)


@pytest.mark.unit
def test_repository_query_count_is_constant_for_one_and_many_runs() -> None:
    one = QueryCountingConnection(1)
    many = QueryCountingConnection(20)
    SQLAnalysisRunSummaryRepository(one).read_for_load_case("load-a")  # type: ignore[arg-type]
    SQLAnalysisRunSummaryRepository(many).read_for_load_case("load-a")  # type: ignore[arg-type]
    assert one.execute_count == many.execute_count == 6
    result_query = next(statement for statement in one.statements if "AS has_unit" in statement)
    assert "CAST(NULL AS VARCHAR) AS metadata_json" in result_query
    assert "CAST(media.metadata_json AS VARCHAR)" in result_query
    assert result_query.count("CAST(NULL AS VARCHAR)") == 7


@pytest.mark.unit
def test_result_metadata_malformed_json_preserves_legacy_string_fallback() -> None:
    assert _json_value("not-json") == "not-json"


@pytest.mark.contract
def test_runs_endpoint_preserves_exact_seeded_contract_and_order() -> None:
    load_case_id = "loadcase-drop-bottom-001"
    with TestClient(app) as client:
        response = client.get(f"/api/load-cases/{load_case_id}/runs")
    assert response.status_code == 200
    payload = response.json()
    assert payload
    assert [item["run_no"] for item in payload] == sorted(
        [item["run_no"] for item in payload], reverse=True
    )
    assert set(payload[0]) == set(RUN) | {
        "overall_verdict",
        "scalar_count",
        "series_count",
        "trust_status",
        "is_latest",
    }
    with connect() as connection:
        stored_runs = rows(
            connection.execute(
                "SELECT * FROM analysis_runs WHERE load_case_id=? ORDER BY run_no DESC",
                [load_case_id],
            )
        )
        verdicts_by_run = {
            run["id"]: [
                row[0]
                for row in connection.execute(
                    "SELECT verdict FROM scalar_results WHERE analysis_run_id=? AND verdict IS NOT NULL",
                    [run["id"]],
                ).fetchall()
            ]
            for run in stored_runs
        }

    expected = []
    with TestClient(app) as trust_client:
        for index, run in enumerate(stored_runs):
            scalar_verdicts = verdicts_by_run[run["id"]]
            trust = trust_client.get(f"/api/analysis-runs/{run['id']}/trust").json()
            expected.append(
                {
                    **run,
                    "overall_verdict": "FAIL" if "FAIL" in scalar_verdicts else "PASS" if scalar_verdicts else "NO_DATA",
                    "scalar_count": trust["counts"]["scalar"],
                    "series_count": trust["counts"]["time_series"],
                    "trust_status": trust["trust_status"],
                    "is_latest": index == 0,
                }
            )
    assert payload == jsonable_encoder(expected)


@pytest.mark.contract
def test_unknown_load_case_preserves_empty_list_contract() -> None:
    with TestClient(app) as client:
        response = client.get("/api/load-cases/load-case-does-not-exist/runs")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.contract
def test_active_nonmember_keeps_company_runs_read_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suffix = uuid4().hex[:10]
    user_id = f"user-runs-{suffix}"
    username = f"runs-{suffix}"
    password = "runs-read-password"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO users
                (id, username, password_hash, display_name, legacy_role, is_active,
                 created_at, updated_at, account_status, is_global_admin)
            VALUES (?, ?, ?, ?, 'viewer', true, ?, ?, 'ACTIVE', false)
            """,
            [user_id, username, hash_password(password), "Runs Reader", now, now],
        )
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-secret-key-that-is-at-least-32-characters")

    try:
        with TestClient(app) as client:
            login = client.post("/api/auth/login", json={"username": username, "password": password})
            assert login.status_code == 200
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            response = client.get(
                "/api/load-cases/loadcase-drop-bottom-001/runs",
                headers=headers,
            )
        assert response.status_code == 200
        assert response.json()
    finally:
        with connect() as connection:
            if database_settings().backend == "duckdb":
                connection.execute("DELETE FROM audit_events WHERE user_id=?", [user_id])
            connection.execute("DELETE FROM project_memberships WHERE user_id=?", [user_id])
            connection.execute("DELETE FROM users WHERE id=?", [user_id])
