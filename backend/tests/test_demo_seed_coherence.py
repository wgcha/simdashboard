from __future__ import annotations

from datetime import datetime, timezone

from app import database as database_module
from app.database import connect, initialize_database


class _SeedGateConnection:
    def __init__(self, canonical_exists: bool) -> None:
        self.canonical_exists = canonical_exists
        self.queries: list[str] = []

    def execute(self, sql: str, parameters: object | None = None) -> "_SeedGateConnection":
        del parameters
        self.queries.append(sql)
        return self

    def fetchone(self) -> tuple[int]:
        return (1 if self.canonical_exists else 0,)


def test_canonical_seed_gate_uses_known_project_id_not_total_project_count(monkeypatch) -> None:
    connection = _SeedGateConnection(canonical_exists=False)
    seeded: list[bool] = []
    monkeypatch.setattr(database_module, "seed_database", lambda _conn: seeded.append(True))

    database_module._ensure_canonical_orion_seed(connection)

    assert seeded == [True]
    assert connection.queries[0] == "SELECT count(*) FROM projects WHERE id='project-tv-001'"


def _table_counts() -> dict[str, int]:
    with connect() as conn:
        return {
            table: int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in ("projects", "analysis_requests", "load_cases", "analysis_runs", "project_memberships")
        }


def test_canonical_projects_have_requests_and_load_cases_but_resultless_examples_are_explicit() -> None:
    """The reference catalog is navigable without pretending every request ran."""
    initialize_database()
    with connect() as conn:
        project_ids = {row[0] for row in conn.execute("SELECT id FROM projects").fetchall()}
        assert {"project-tv-001", "project-feature-showcase"} <= project_ids

        assert conn.execute(
            "SELECT count(*) FROM analysis_requests WHERE project_id='project-feature-showcase'"
        ).fetchone()[0] == 7
        assert conn.execute(
            """SELECT count(*) FROM load_cases lc
               JOIN analysis_requests r ON r.id=lc.request_id
               WHERE r.project_id='project-feature-showcase'"""
        ).fetchone()[0] == 7
        assert conn.execute(
            """SELECT count(*) FROM request_steps step
               JOIN analysis_requests r ON r.id=step.request_id
               WHERE r.project_id='project-feature-showcase'"""
        ).fetchone()[0] == 70
        assert conn.execute(
            """SELECT count(*) FROM analysis_runs run
               JOIN load_cases lc ON lc.id=run.load_case_id
               JOIN analysis_requests r ON r.id=lc.request_id
               WHERE r.project_id='project-feature-showcase'"""
        ).fetchone()[0] == 11

        for project_id in project_ids & {"project-tv-001", "project-feature-showcase"}:
            requests = conn.execute(
                "SELECT id FROM analysis_requests WHERE project_id=? ORDER BY id",
                [project_id],
            ).fetchall()
            assert requests, project_id
            for (request_id,) in requests:
                assert conn.execute("SELECT 1 FROM load_cases WHERE request_id=?", [request_id]).fetchone(), request_id

        assert conn.execute("SELECT count(*) FROM analysis_runs WHERE load_case_id='loadcase-showcase-waiting'").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM analysis_runs WHERE load_case_id='loadcase-showcase-workflow'").fetchone()[0] == 0
        assert conn.execute(
            "SELECT count(*) FROM project_memberships WHERE project_id='project-feature-showcase' AND user_id='local-admin'"
        ).fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM quality_thresholds WHERE project_id='project-feature-showcase'").fetchone()[0] >= 2
        assert conn.execute("SELECT count(*) FROM project_workspace_layouts WHERE project_id='project-feature-showcase'").fetchone()[0] >= 2


def test_demo_seed_is_idempotent_and_does_not_fill_arbitrary_projects() -> None:
    initialize_database()
    first = _table_counts()
    initialize_database()
    assert _table_counts() == first

    with connect() as conn:
        conn.execute(
            "INSERT INTO projects (id, name, product_name, description, created_at) VALUES (?, ?, ?, ?, ?)",
            ["project-arbitrary-empty", "사용자 빈 프로젝트", "사용자 제품", "아직 의뢰를 만들지 않은 실제 프로젝트", datetime.now(timezone.utc).replace(tzinfo=None)],
        )
    initialize_database()
    with connect() as conn:
        assert conn.execute(
            "SELECT count(*) FROM analysis_requests WHERE project_id='project-arbitrary-empty'"
        ).fetchone()[0] == 0


def test_feature_example_seed_repairs_a_missing_request_folder_step() -> None:
    initialize_database()
    with connect() as conn:
        conn.execute("DELETE FROM request_steps WHERE id='step-showcase-workflow-05'")
        assert conn.execute(
            "SELECT count(*) FROM request_steps WHERE id='step-showcase-workflow-05'"
        ).fetchone()[0] == 0

    initialize_database()
    with connect() as conn:
        repaired = conn.execute(
            "SELECT request_id, sequence_no, status FROM request_steps WHERE id='step-showcase-workflow-05'"
        ).fetchone()
        assert repaired == ("request-showcase-workflow", 5, "BLOCKED")


def test_orion_seed_repairs_missing_request_graph_without_duplicating_results() -> None:
    initialize_database()
    with connect() as conn:
        before_runs = conn.execute("SELECT count(*) FROM analysis_runs WHERE id='run-drop-001'").fetchone()[0]
        conn.execute("DELETE FROM load_cases WHERE id='loadcase-clamp-left-001'")
        conn.execute("DELETE FROM analysis_requests WHERE id='request-clamp-001'")
        conn.execute("DELETE FROM request_steps WHERE id='step-drop-01'")

    initialize_database()
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM analysis_requests WHERE id='request-clamp-001' AND project_id='project-tv-001'").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM load_cases WHERE id='loadcase-clamp-left-001' AND request_id='request-clamp-001'").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM request_steps WHERE id='step-drop-01' AND request_id='request-drop-001'").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM analysis_runs WHERE id='run-drop-001'").fetchone()[0] == before_runs == 1


def test_canonical_demo_work_plans_show_spdm_request_origin() -> None:
    initialize_database()
    with connect() as conn:
        assert conn.execute(
            "SELECT source_type, source_reference FROM request_work_plans WHERE request_id='request-drop-001'"
        ).fetchone() == ("EXTERNAL_SYSTEM", "SPDM-DEMO-2026-0001")
        assert conn.execute(
            "SELECT source_type, source_reference FROM request_work_plans WHERE request_id='request-clamp-001'"
        ).fetchone() == ("EXTERNAL_SYSTEM", "SPDM-DEMO-2026-0002")


def test_legacy_demo_origin_is_repaired_but_user_override_is_preserved() -> None:
    initialize_database()
    with connect() as conn:
        conn.execute(
            "UPDATE request_work_plans SET source_type='DEPARTMENT_HEAD', source_reference='기존 데모 시드' WHERE request_id='request-drop-001'"
        )
    initialize_database()
    with connect() as conn:
        assert conn.execute(
            "SELECT source_type, source_reference FROM request_work_plans WHERE request_id='request-drop-001'"
        ).fetchone() == ("EXTERNAL_SYSTEM", "SPDM-DEMO-2026-0001")
        conn.execute(
            "UPDATE request_work_plans SET source_type='DEPARTMENT_HEAD', source_reference='부서장 직접 접수' WHERE request_id='request-drop-001'"
        )
    initialize_database()
    with connect() as conn:
        assert conn.execute(
            "SELECT source_type, source_reference FROM request_work_plans WHERE request_id='request-drop-001'"
        ).fetchone() == ("DEPARTMENT_HEAD", "부서장 직접 접수")
        # Keep the disposable fixture's canonical state for any callers that reuse it.
        conn.execute(
            "UPDATE request_work_plans SET source_type='EXTERNAL_SYSTEM', source_reference='SPDM-DEMO-2026-0001' WHERE request_id='request-drop-001'"
        )
