"""Add immutable request scenarios and sequential work items.

Revision ID: 0004_request_work_plans
Revises: 0003_workbench_demo
"""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "0004_request_work_plans"
down_revision = "0003_workbench_demo"
branch_labels = None
depends_on = None


SCENARIOS = (
    (
        "design-reliability-validation",
        "설계 신뢰성 검증",
        (
            ("cad-prepare", "CAD 작업"),
            ("analysis-modeling", "해석 모델링"),
            ("hpc-submit", "HPC 수행"),
            ("post-process", "결과 후처리"),
            ("analysis-db-publish", "해석 DB 저장"),
            ("reliability-evaluation", "오픈셀 파손 및 CHR 휨 평가 분석"),
        ),
    ),
    (
        "design-doe-exploration",
        "설계 DOE 탐색",
        (
            ("cad-prepare", "CAD 작업"),
            ("doe-generate", "DOE 파일 생성"),
            ("hpc-submit", "HPC 수행"),
            ("post-process", "결과 후처리"),
            ("optimization-analysis", "최적화 분석"),
            ("analysis-db-publish", "해석 DB 저장"),
            ("design-performance-ranking", "설계 성능 순위 평가"),
        ),
    ),
)


def _definition(tasks: tuple[tuple[str, str], ...]) -> dict:
    nodes = []
    for index, (task_id, display_name) in enumerate(tasks):
        nodes.append(
            {
                "node_key": task_id,
                "task_type_id": task_id,
                "task_type_version": 1,
                "depends_on": [tasks[index - 1][0]] if index else [],
                "display_name": display_name,
            }
        )
    return {"nodes": nodes}


def _seed_catalog() -> None:
    connection = op.get_bind()
    tasks = (
        ("reliability-evaluation", "RELIABILITY_EVALUATION", "오픈셀 파손 및 CHR 휨 평가 분석", ["POST_RESULT"], ["RELIABILITY_ASSESSMENT"]),
        ("optimization-analysis", "OPTIMIZATION_ANALYSIS", "최적화 분석", ["POST_RESULT"], ["OPTIMIZATION_RESULT"]),
        ("design-performance-ranking", "PERFORMANCE_RANKING", "설계 성능 순위 평가", ["ANALYSIS_RUN_REFERENCE"], ["PERFORMANCE_RANKING"]),
    )
    for task_id, kind, display_name, inputs, outputs in tasks:
        connection.execute(
            sa.text(
                """
                INSERT INTO task_type_versions
                    (id, version, kind, display_name, description, supports_standalone,
                     input_artifact_types_json, output_artifact_types_json, parameter_schema_json,
                     demo_artifact_url, is_active, created_at)
                VALUES (:id, 1, :kind, :display_name, :description, true,
                        CAST(:inputs AS JSONB), CAST(:outputs AS JSONB), CAST(:schema AS JSONB),
                        '/assets/demo-workbench.svg', true, CURRENT_TIMESTAMP)
                ON CONFLICT (id, version) DO NOTHING
                """
            ),
            {"id": task_id, "kind": kind, "display_name": display_name, "description": f"{display_name} 데모 Task", "inputs": json.dumps(inputs, ensure_ascii=False), "outputs": json.dumps(outputs, ensure_ascii=False), "schema": json.dumps({"type": "object", "additionalProperties": False})},
        )
    connection.execute(sa.text("UPDATE request_type_versions SET is_active=false WHERE id IN ('result-reprocessing', 'doe-analysis', 'physicsai-training', 'physicsai-prediction')"))
    for request_type_id, display_name, tasks in SCENARIOS:
        definition = _definition(tasks)
        refs = [{"id": task_id, "version": 1} for task_id, _ in tasks]
        connection.execute(
            sa.text(
                """
                INSERT INTO request_type_versions
                    (id, version, display_name, description, allowed_task_types_json,
                     default_workflow_json, match_rules_json, is_active, created_at)
                VALUES (:id, 1, :display_name, :description, CAST(:refs AS JSONB),
                        CAST(:definition AS JSONB), '{}'::jsonb, true, CURRENT_TIMESTAMP)
                ON CONFLICT (id, version) DO NOTHING
                """
            ),
            {"id": request_type_id, "display_name": display_name, "description": f"{display_name} 고정 순차 시나리오", "refs": json.dumps(refs), "definition": json.dumps(definition, ensure_ascii=False)},
        )


def _backfill_seed_request(request_id: str, request_type_id: str, completed: int) -> None:
    connection = op.get_bind()
    scenario = next(item for item in SCENARIOS if item[0] == request_type_id)
    _, scenario_name, tasks = scenario
    definition = _definition(tasks)
    connection.execute(
        sa.text(
            """
            INSERT INTO analysis_request_type_assignments
                (request_id, request_type_id, request_type_version, source, rule_snapshot_json, decided_by, decided_at)
            SELECT ar.id, CAST(:request_type_id AS VARCHAR), 1, 'ADMIN', '{}'::jsonb, 'system', CURRENT_TIMESTAMP
            FROM analysis_requests ar
            WHERE ar.id=CAST(:request_id AS VARCHAR)
            ON CONFLICT (request_id) DO UPDATE SET
                request_type_id=excluded.request_type_id, request_type_version=1,
                source='ADMIN', rule_snapshot_json='{}'::jsonb,
                decided_by='system', decided_at=CURRENT_TIMESTAMP
            """
        ),
        {"request_id": request_id, "request_type_id": request_type_id},
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO request_work_plans
                (request_id, request_type_id, request_type_version, scenario_name,
                 source_type, source_reference, requested_by,
                 definition_snapshot_json, assigned_by, assigned_at)
            SELECT ar.id, CAST(:request_type_id AS VARCHAR), 1, CAST(:scenario_name AS VARCHAR),
                   'DEPARTMENT_HEAD', '기존 데모 시드', 'system',
                   CAST(:definition AS JSONB), 'system', CURRENT_TIMESTAMP
            FROM analysis_requests ar
            WHERE ar.id=CAST(:request_id AS VARCHAR)
            ON CONFLICT (request_id) DO NOTHING
            """
        ),
        {"request_id": request_id, "request_type_id": request_type_id, "scenario_name": scenario_name, "definition": json.dumps(definition, ensure_ascii=False)},
    )
    for sequence_no, (task_id, display_name) in enumerate(tasks, start=1):
        status = "COMPLETED" if sequence_no <= completed else "IN_PROGRESS" if sequence_no == completed + 1 else "WAITING"
        connection.execute(
            sa.text(
                """
                INSERT INTO request_work_items
                    (id, request_id, node_key, task_type_id, task_type_version, sequence_no,
                     display_name, status, owner, started_by, started_at,
                     completed_by, completed_at, demo_run_id)
                SELECT CAST(:id AS VARCHAR), ar.id, CAST(:node_key AS VARCHAR),
                       CAST(:task_type_id AS VARCHAR), 1, CAST(:sequence_no AS INTEGER),
                       CAST(:display_name AS VARCHAR), CAST(:status AS VARCHAR),
                       COALESCE(ar.owner, '해석 담당자'),
                       CASE WHEN CAST(:status AS VARCHAR) IN ('IN_PROGRESS', 'COMPLETED') THEN 'system' ELSE NULL END,
                       CASE WHEN CAST(:status AS VARCHAR) IN ('IN_PROGRESS', 'COMPLETED') THEN CURRENT_TIMESTAMP ELSE NULL END,
                       CASE WHEN CAST(:status AS VARCHAR)='COMPLETED' THEN 'system' ELSE NULL END,
                       CASE WHEN CAST(:status AS VARCHAR)='COMPLETED' THEN CURRENT_TIMESTAMP ELSE NULL END, NULL
                FROM analysis_requests ar
                WHERE ar.id=CAST(:request_id AS VARCHAR)
                ON CONFLICT (request_id, node_key) DO NOTHING
                """
            ),
            {"id": f"seed-work-{request_id}-{sequence_no}", "request_id": request_id, "node_key": task_id, "task_type_id": task_id, "sequence_no": sequence_no, "display_name": display_name, "status": status},
        )
    connection.execute(sa.text("UPDATE analysis_requests SET status='IN_PROGRESS' WHERE id=:request_id"), {"request_id": request_id})


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS request_work_plans (
            request_id VARCHAR PRIMARY KEY,
            request_type_id VARCHAR NOT NULL,
            request_type_version INTEGER NOT NULL,
            scenario_name VARCHAR NOT NULL,
            source_type VARCHAR NOT NULL,
            source_reference VARCHAR NOT NULL,
            requested_by VARCHAR NOT NULL,
            definition_snapshot_json JSONB NOT NULL,
            assigned_by VARCHAR NOT NULL,
            assigned_at TIMESTAMP NOT NULL,
            CONSTRAINT ck_request_work_plans_source_type CHECK (source_type IN ('EXTERNAL_SYSTEM', 'DEPARTMENT_HEAD'))
        )
    """))
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS request_work_items (
            id VARCHAR PRIMARY KEY,
            request_id VARCHAR NOT NULL,
            node_key VARCHAR NOT NULL,
            task_type_id VARCHAR NOT NULL,
            task_type_version INTEGER NOT NULL,
            sequence_no INTEGER NOT NULL,
            display_name VARCHAR NOT NULL,
            status VARCHAR NOT NULL,
            owner VARCHAR NOT NULL,
            started_by VARCHAR,
            started_at TIMESTAMP,
            completed_by VARCHAR,
            completed_at TIMESTAMP,
            demo_run_id VARCHAR,
            UNIQUE (request_id, sequence_no),
            UNIQUE (request_id, node_key),
            CONSTRAINT ck_request_work_items_status CHECK (status IN ('READY', 'IN_PROGRESS', 'WAITING', 'COMPLETED'))
        )
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_request_work_plans_type ON request_work_plans(request_type_id, request_type_version)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_request_work_items_request_status ON request_work_items(request_id, status, sequence_no)"))
    for statement in (
        "ALTER TABLE request_work_plans ADD CONSTRAINT fk_request_work_plans_request FOREIGN KEY (request_id) REFERENCES analysis_requests(id)",
        "ALTER TABLE request_work_plans ADD CONSTRAINT fk_request_work_plans_type FOREIGN KEY (request_type_id, request_type_version) REFERENCES request_type_versions(id, version)",
        "ALTER TABLE request_work_items ADD CONSTRAINT fk_request_work_items_request FOREIGN KEY (request_id) REFERENCES request_work_plans(request_id)",
        "ALTER TABLE request_work_items ADD CONSTRAINT fk_request_work_items_type FOREIGN KEY (task_type_id, task_type_version) REFERENCES task_type_versions(id, version)",
        "ALTER TABLE request_work_items ADD CONSTRAINT fk_request_work_items_demo_run FOREIGN KEY (demo_run_id) REFERENCES workflow_runs(id)",
    ):
        op.execute(sa.text(f"DO $$ BEGIN {statement}; EXCEPTION WHEN duplicate_object THEN NULL; END $$"))
    _seed_catalog()
    _backfill_seed_request("request-drop-001", "design-reliability-validation", 2)
    _backfill_seed_request("request-clamp-001", "design-doe-exploration", 1)


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS request_work_items"))
    op.execute(sa.text("DROP TABLE IF EXISTS request_work_plans"))
