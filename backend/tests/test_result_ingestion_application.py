from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime

import pytest

from app.application.results import ingestion

pytestmark = pytest.mark.unit


def _input(*, validate_only: bool) -> ingestion.ManualResultImportInput:
    return ingestion.ManualResultImportInput(
        target=ingestion.ResultIngestionTarget("project-1", "request-1", "load-case-1"),
        filename="result.json",
        content="{}",
        author=None,
        principal_user_id="user-1",
        actor_name="User 1",
        source_run_id="producer-1",
        conflict_policy="REJECT",
        chassis_threshold=5.0,
        open_cell_threshold=75.0,
        catalog={},
        validate_only=validate_only,
    )


def test_rejected_manual_import_audits_inside_transaction_and_returns_outcome(monkeypatch):
    events: list[str] = []
    rejected = {
        "status": "REJECTED",
        "operation": "REJECTED",
        "analysis_run_id": "run-existing",
        "reason_code": "SOURCE_RUN_CONFLICT",
        "existing_analysis_run_id": "run-existing",
        "replaced_analysis_run_id": None,
        "source_revision": 3,
    }

    monkeypatch.setattr(
        ingestion,
        "_parse_manual_result",
        lambda _input: {"summary": {"source_format": "SUMMARY_RESULT"}, "_canonical": {}, "_source_checksum": "checksum"},
    )
    monkeypatch.setattr(
        ingestion,
        "ingest_result_bundle",
        lambda *_args: events.append("ingest") or rejected,
    )

    @contextmanager
    def transaction():
        events.append("enter")
        yield lambda: None
        events.append("exit")

    execution = ingestion.run_manual_import(
        _input(validate_only=False),
        transaction=transaction,
        audit=lambda record: events.append(f"audit:{record.action}:{record.status_code}"),
        now=lambda: datetime(2026, 1, 1),
    )

    assert execution.outcome == rejected
    assert events == ["enter", "ingest", "audit:RESULT_IMPORT_REJECTED:409", "exit"]


def test_validate_only_manual_import_does_not_open_transaction_or_audit(monkeypatch):
    parsed = {"summary": {"source_format": "SUMMARY_RESULT"}}
    monkeypatch.setattr(ingestion, "_parse_manual_result", lambda _input: parsed)

    execution = ingestion.run_manual_import(
        _input(validate_only=True),
        transaction=lambda: (_ for _ in ()).throw(AssertionError("transaction should not open")),
        audit=lambda _record: (_ for _ in ()).throw(AssertionError("audit should not run")),
        now=lambda: datetime(2026, 1, 1),
    )

    assert execution.parsed == parsed
    assert execution.outcome is None
