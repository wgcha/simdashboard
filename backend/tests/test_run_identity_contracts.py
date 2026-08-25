from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.database import connect, initialize_database
from app.main import app
from app.services.master_result_refresh import MasterResultRefreshService


pytestmark = pytest.mark.duckdb_integration

LOAD_CASE_ID = "loadcase-drop-bottom-001"
PROJECT_ID = "project-tv-001"
REQUEST_ID = "request-drop-001"


def _manual_payload(
    filename: str,
    value: float,
    *,
    source_run_id: str | None = "producer-run-contract-1",
    conflict_policy: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "filename": filename,
        "content": json.dumps(
            {
                "solver": "Run identity contract test",
                "scalar_results": [
                    {
                        "variable_key": "top_edge_max_stress",
                        "value": value,
                        "unit": "MPa",
                        "threshold": 75.0,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        "source_run_id": source_run_id,
    }
    if conflict_policy is not None:
        payload["conflict_policy"] = conflict_policy
    return payload


def test_manual_source_run_conflicts_are_committed_and_audited_once():
    initialize_database()
    filename = "run-identity-contract.json"

    with TestClient(app) as client:
        first = client.post(
            f"/api/load-cases/{LOAD_CASE_ID}/results/import",
            json=_manual_payload(filename, 42.0),
        )
        assert first.status_code == 200, first.text
        first_body = first.json()
        assert (first_body["status"], first_body["operation"], first_body["source_revision"]) == (
            "IMPORTED",
            "CREATED",
            1,
        )
        first_run_id = first_body["run_id"]
        first_run_no = first_body["run_no"]

        changed_skip = client.post(
            f"/api/load-cases/{LOAD_CASE_ID}/results/import",
            json=_manual_payload(filename, 43.0, conflict_policy="SKIP"),
        )
        assert changed_skip.status_code == 200, changed_skip.text
        assert changed_skip.json()["status"] == "SKIPPED"
        assert changed_skip.json()["operation"] == "NOOP"
        assert changed_skip.json()["run_id"] == first_run_id
        assert changed_skip.json()["run_no"] == first_run_no

        changed_reject = client.post(
            f"/api/load-cases/{LOAD_CASE_ID}/results/import",
            json=_manual_payload(filename, 44.0, conflict_policy="REJECT"),
        )
        assert changed_reject.status_code == 409, changed_reject.text
        assert changed_reject.json()["detail"]["code"] == "SOURCE_RUN_CONFLICT"

        changed_replace = client.post(
            f"/api/load-cases/{LOAD_CASE_ID}/results/import",
            json=_manual_payload(filename, 45.0, conflict_policy="REPLACE"),
        )
        assert changed_replace.status_code == 200, changed_replace.text
        replaced_body = changed_replace.json()
        assert (replaced_body["status"], replaced_body["operation"], replaced_body["source_revision"]) == (
            "IMPORTED",
            "REPLACED",
            2,
        )
        assert replaced_body["run_id"] != first_run_id
        assert replaced_body["run_no"] != first_run_no
        assert replaced_body["replaced_run_id"] == first_run_id

        # The exact checksum is idempotent regardless of the requested policy.
        exact_retry = client.post(
            f"/api/load-cases/{LOAD_CASE_ID}/results/import",
            json=_manual_payload(filename, 45.0, conflict_policy="REJECT"),
        )
        assert exact_retry.status_code == 200, exact_retry.text
        assert exact_retry.json()["operation"] == "NOOP"
        assert exact_retry.json()["run_id"] == replaced_body["run_id"]
        assert exact_retry.json()["source_revision"] == 2

    with connect() as conn:
        metadata = conn.execute(
            """
            SELECT analysis_run_id, source_checksum
            FROM analysis_run_metadata
            WHERE source_type='FILE_UPLOAD' AND source_name=?
            ORDER BY created_at
            """,
            [f"{LOAD_CASE_ID}/{filename}"],
        ).fetchall()
        assert len(metadata) == 2
        assert {row[0] for row in metadata} == {first_run_id, replaced_body["run_id"]}

        ledger = conn.execute(
            """
            SELECT source_run_id, source_revision, analysis_run_id, supersedes_analysis_run_id
            FROM canonical_result_ingestion_source_versions
            WHERE load_case_id=? AND source_type='FILE_UPLOAD' AND source_key=?
            ORDER BY source_revision
            """,
            [LOAD_CASE_ID, "run:producer-run-contract-1"],
        ).fetchall()
        assert ledger == [
            ("producer-run-contract-1", 1, first_run_id, None),
            ("producer-run-contract-1", 2, replaced_body["run_id"], first_run_id),
        ]

        jobs = conn.execute(
            """
            SELECT status, analysis_run_id, replaced_analysis_run_id
            FROM folder_import_jobs
            WHERE source_folder=?
            ORDER BY created_at, id
            """,
            [f"{LOAD_CASE_ID}/{filename}"],
        ).fetchall()
        assert [row[0] for row in jobs] == ["COMPLETED", "SKIPPED", "REJECTED", "COMPLETED", "SKIPPED"]
        assert jobs[2][1] == first_run_id
        assert jobs[3][2] == first_run_id

        audit_counts = dict(
            conn.execute(
                """
                SELECT action, count(*)
                FROM audit_events
                WHERE action IN (
                    'RESULT_IMPORTED', 'RESULT_IMPORT_SKIPPED',
                    'RESULT_IMPORT_REJECTED', 'RESULT_IMPORT_REPLACED'
                ) AND path LIKE '%results/import'
                GROUP BY action
                """
            ).fetchall()
        )
        assert audit_counts == {
            "RESULT_IMPORTED": 1,
            "RESULT_IMPORT_SKIPPED": 2,
            "RESULT_IMPORT_REJECTED": 1,
            "RESULT_IMPORT_REPLACED": 1,
        }


@pytest.mark.parametrize(
    "source_run_id",
    ["", "   ", "producer\x00run", "x" * 121],
)
def test_manual_source_run_id_validation_rejects_unsafe_values(source_run_id: str):
    initialize_database()
    with TestClient(app) as client:
        response = client.post(
            f"/api/load-cases/{LOAD_CASE_ID}/results/import",
            json=_manual_payload("run-id-validation.json", 42.0, source_run_id=source_run_id),
        )
    assert response.status_code == 422, response.text
    with connect() as conn:
        assert conn.execute(
            "SELECT count(*) FROM analysis_run_metadata WHERE source_name=?",
            [f"{LOAD_CASE_ID}/run-id-validation.json"],
        ).fetchone()[0] == 0


def test_manual_source_run_id_is_trimmed_and_policy_is_validated():
    initialize_database()
    with TestClient(app) as client:
        trimmed = client.post(
            f"/api/load-cases/{LOAD_CASE_ID}/results/import",
            json=_manual_payload(
                "run-id-trimmed.json",
                42.0,
                source_run_id="  producer-trimmed  ",
            ),
        )
        assert trimmed.status_code == 200, trimmed.text
        invalid_policy = client.post(
            f"/api/load-cases/{LOAD_CASE_ID}/results/import",
            json=_manual_payload(
                "run-id-invalid-policy.json",
                42.0,
                conflict_policy="MERGE",
            ),
        )
        assert invalid_policy.status_code == 422, invalid_policy.text

    with connect() as conn:
        assert conn.execute(
            "SELECT source_run_id FROM canonical_result_ingestion_source_versions WHERE analysis_run_id=?",
            [trimmed.json()["run_id"]],
        ).fetchone()[0] == "producer-trimmed"


def _write_master_bundle(
    root: Path,
    name: str,
    *,
    value: float = 42.5,
    source_run_id: str | None = "master-run-contract-1",
    conflict_policy: object = None,
) -> Path:
    bundle = root / name
    bundle.mkdir(parents=True)
    (bundle / "scalar-results.json").write_text(
        json.dumps(
            [
                {
                    "variable_key": "top_edge_max_stress",
                    "display_name": "상단 최대 응력",
                    "data_type": "FLOAT",
                    "value": value,
                    "unit": "MPa",
                    "threshold": 75.0,
                    "result_group": "OPEN_CELL",
                }
            ]
        ),
        encoding="utf-8",
    )
    manifest: dict[str, object] = {
        "schema_id": "master-refresh-contract",
        "version": 1,
        "solver": "pytest",
        "context": {
            "project_id": PROJECT_ID,
            "request_id": REQUEST_ID,
            "load_case_id": LOAD_CASE_ID,
        },
        "mappings": [{"kind": "typed_scalars", "path": "scalar-results.json"}],
    }
    if source_run_id is not None:
        manifest["source_run_id"] = source_run_id
    if conflict_policy is not None:
        manifest["conflict_policy"] = conflict_policy
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return bundle


def test_master_source_identity_defaults_to_skip_and_reject_is_a_terminal_job(tmp_path: Path):
    initialize_database()
    bundle = _write_master_bundle(tmp_path, "bundle-source-contract")
    first = MasterResultRefreshService(tmp_path).refresh()
    assert [(item.status, item.operation, item.source_revision) for item in first] == [
        ("IMPORTED", "CREATED", 1)
    ]
    first_run_id = first[0].analysis_run_id

    (bundle / "scalar-results.json").write_text(
        json.dumps(
            [
                {
                    "variable_key": "top_edge_max_stress",
                    "display_name": "상단 최대 응력",
                    "data_type": "FLOAT",
                    "value": 43.5,
                    "unit": "MPa",
                    "threshold": 75.0,
                    "result_group": "OPEN_CELL",
                }
            ]
        ),
        encoding="utf-8",
    )
    changed_skip = MasterResultRefreshService(tmp_path).refresh()
    assert changed_skip[0].status == "SKIPPED"
    assert changed_skip[0].analysis_run_id == first_run_id
    assert changed_skip[0].reason_code == "SOURCE_RUN_CHANGED_SKIPPED"

    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["conflict_policy"] = "REJECT"
    (bundle / "scalar-results.json").write_text(
        json.dumps(
            [
                {
                    "variable_key": "top_edge_max_stress",
                    "display_name": "상단 최대 응력",
                    "data_type": "FLOAT",
                    "value": 44.5,
                    "unit": "MPa",
                    "threshold": 75.0,
                    "result_group": "OPEN_CELL",
                }
            ]
        ),
        encoding="utf-8",
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    rejected = MasterResultRefreshService(tmp_path).refresh()
    assert rejected[0].status == "FAILED"
    assert rejected[0].operation == "REJECTED"
    assert rejected[0].reason_code == "SOURCE_RUN_CONFLICT"

    with connect() as conn:
        assert conn.execute(
            "SELECT count(*) FROM analysis_run_metadata WHERE source_type='MASTER_FOLDER_REFRESH'"
        ).fetchone()[0] == 1
        statuses = conn.execute(
            "SELECT status FROM folder_import_jobs WHERE source_folder=? ORDER BY created_at, id",
            ["bundle-source-contract/manifest.json"],
        ).fetchall()
        assert [row[0] for row in statuses] == ["COMPLETED", "SKIPPED", "REJECTED"]
        assert conn.execute(
            "SELECT count(*) FROM folder_import_jobs WHERE source_folder=? AND status='FAILED'",
            ["bundle-source-contract/manifest.json"],
        ).fetchone()[0] == 0


@pytest.mark.parametrize("conflict_policy", ["REPLACE", 7])
def test_master_source_policy_fails_closed_without_creating_a_run(tmp_path: Path, conflict_policy: object):
    initialize_database()
    _write_master_bundle(
        tmp_path,
        "bundle-invalid-source-policy",
        conflict_policy=conflict_policy,
    )

    items = MasterResultRefreshService(tmp_path).refresh()
    assert len(items) == 1
    assert items[0].status == "FAILED"
    assert items[0].reason_code == "SOURCE_CONFLICT_POLICY_UNSUPPORTED"

    with connect() as conn:
        assert conn.execute(
            "SELECT count(*) FROM analysis_run_metadata WHERE source_type='MASTER_FOLDER_REFRESH'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT count(*) FROM folder_import_jobs WHERE source_folder=? AND status='FAILED'",
            ["bundle-invalid-source-policy/manifest.json"],
        ).fetchone()[0] == 1
