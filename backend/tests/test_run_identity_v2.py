from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime

import pytest

from app.application.results.commands import ingest_result_bundle
from app.domains.results.models import decide_source_conflict, source_key_for


pytestmark = pytest.mark.unit


def _command(**overrides: object) -> dict[str, object]:
    command: dict[str, object] = {
        "project_id": "project-1",
        "request_id": "request-1",
        "load_case_id": "load-case-1",
        "source_type": "PRODUCER",
        "source_name": "result.json",
        "source_checksum": "sha-a",
        "parser_version": "v1",
        "parsed": {
            "schema_id": "schema",
            "schema_version": 1,
            "solver": "test",
            "scalars": [],
            "curves": [],
            "media": [],
            "note": None,
        },
        "actor": "pytest",
        "metadata": {},
    }
    command.update(overrides)
    return command


def _run(run_id: str, run_no: int, revision: int) -> dict[str, object]:
    return {"analysis_run_id": run_id, "run_no": run_no, "source_revision": revision}


@pytest.mark.parametrize(
    ("policy", "status", "operation", "reason"),
    [
        ("SKIP", "SKIPPED", "NOOP", "SOURCE_RUN_CHANGED_SKIPPED"),
        ("REJECT", "REJECTED", "REJECTED", "SOURCE_RUN_CHANGED_REJECTED"),
        ("REPLACE", "IMPORTED", "REPLACED", "SOURCE_RUN_REPLACED"),
    ],
)
def test_source_run_conflict_matrix_is_pure_and_policy_complete(policy, status, operation, reason):
    decision = decide_source_conflict(
        _command(source_run_id="producer-run-7", conflict_policy=policy),  # type: ignore[arg-type]
        None,
        _run("run-old", 4, 2),  # type: ignore[arg-type]
    )

    assert (decision["status"], decision["operation"], decision["reason_code"]) == (status, operation, reason)
    assert decision["existing_analysis_run_id"] == "run-old"
    assert decision["source_revision"] == (3 if policy == "REPLACE" else 2)


def test_source_key_namespaces_legacy_names_away_from_producer_run_ids():
    assert source_key_for(_command(source_name="run:producer-7")) == "name:run:producer-7"  # type: ignore[arg-type]
    assert source_key_for(_command(source_run_id="producer-7")) == "run:producer-7"  # type: ignore[arg-type]


class _UnitOfWork:
    def __init__(self, exact=None, latest=None):
        self.exact = exact
        self.latest = latest
        self.calls: list[tuple[str, object]] = []

    def validate_target(self, command): self.calls.append(("validate", command))
    def authorize(self, command): self.calls.append(("authorize", command))
    def lock_load_case_ingestion(self, load_case_id): self.calls.append(("lock", load_case_id))
    def find_exact_source_run(self, command): self.calls.append(("exact", command)); return self.exact
    def find_latest_source_run(self, command): self.calls.append(("latest", command)); return self.latest
    def add_skipped_job(self, *args): self.calls.append(("skipped", args))
    def add_rejected_job(self, *args): self.calls.append(("rejected", args))
    def next_run_no(self, load_case_id): self.calls.append(("next", load_case_id)); return 9
    def add_running_job(self, *args): self.calls.append(("running", args))
    def ensure_catalog(self, *args): self.calls.append(("catalog", args))
    def add_run(self, *args): self.calls.append(("run", args))
    def add_metadata(self, *args): self.calls.append(("metadata", args))
    def record_source_version(self, *args): self.calls.append(("version", args))
    def add_results(self, *args): self.calls.append(("results", args))
    def complete_job(self, *args): self.calls.append(("complete", args))
    def sync_status(self, *args): self.calls.append(("sync", args))


def _ingest(unit_of_work: _UnitOfWork, **overrides: object):
    @contextmanager
    def provider():
        yield unit_of_work

    return ingest_result_bundle(
        _command(**overrides),  # type: ignore[arg-type]
        provider,
        lambda: datetime(2026, 8, 25, 12),
        lambda prefix: f"{prefix}-id",
    )


def test_exact_skip_returns_existing_server_assigned_identity():
    unit_of_work = _UnitOfWork(exact=_run("run-existing", 6, 2))

    outcome = _ingest(unit_of_work, source_run_id="producer-run", conflict_policy="REPLACE")

    assert outcome["status"] == "SKIPPED"
    assert outcome["analysis_run_id"] == "run-existing"
    assert outcome["run_no"] == 6
    assert outcome["operation"] == "NOOP"
    assert [call[0] for call in unit_of_work.calls] == ["validate", "authorize", "lock", "exact", "latest", "skipped"]


def test_reject_records_terminal_job_without_creating_or_rolling_back_run():
    unit_of_work = _UnitOfWork(latest=_run("run-existing", 6, 2))

    outcome = _ingest(unit_of_work, source_run_id="producer-run", conflict_policy="REJECT", source_checksum="sha-b")

    assert outcome["status"] == "REJECTED"
    assert outcome["analysis_run_id"] == "run-existing"
    assert [call[0] for call in unit_of_work.calls] == ["validate", "authorize", "lock", "exact", "latest", "rejected"]


def test_replace_appends_immutable_run_and_version_after_latest_run():
    unit_of_work = _UnitOfWork(latest=_run("run-old", 6, 2))

    outcome = _ingest(unit_of_work, source_run_id="producer-run", conflict_policy="REPLACE", source_checksum="sha-b")

    assert outcome["status"] == "IMPORTED"
    assert outcome["analysis_run_id"] == "run-id"
    assert outcome["run_no"] == 9
    assert outcome["replaced_analysis_run_id"] == "run-old"
    assert outcome["source_revision"] == 3
    assert [call[0] for call in unit_of_work.calls] == [
        "validate", "authorize", "lock", "exact", "latest", "next", "running",
        "catalog", "run", "metadata", "version", "results", "complete", "sync",
    ]


def test_legacy_changed_checksum_appends_a_scoped_ledger_revision():
    unit_of_work = _UnitOfWork(latest=_run("run-old", 6, 2))

    outcome = _ingest(unit_of_work, source_checksum="sha-b")

    assert outcome["operation"] == "CREATED"
    assert outcome["reason_code"] == "LEGACY_APPEND"
    assert outcome["source_revision"] == 3
    assert "version" in [call[0] for call in unit_of_work.calls]


def test_checksum_none_never_writes_a_source_ledger_version():
    unit_of_work = _UnitOfWork(latest=_run("run-old", 6, 2))

    outcome = _ingest(unit_of_work, source_run_id="producer-run", source_checksum=None)

    assert outcome["source_revision"] is None
    assert "version" not in [call[0] for call in unit_of_work.calls]
