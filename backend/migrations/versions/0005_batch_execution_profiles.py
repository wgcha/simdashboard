"""Add safe batch dispatch profiles and persistent work item progress.

Revision ID: 0005_batch_execution_profiles
Revises: 0004_request_work_plans
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_batch_execution_profiles"
down_revision = "0004_request_work_plans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("ALTER TABLE request_work_items ADD COLUMN IF NOT EXISTS progress INTEGER NOT NULL DEFAULT 0"))
    op.execute(sa.text("ALTER TABLE request_work_items ADD COLUMN IF NOT EXISTS progress_updated_by VARCHAR"))
    op.execute(sa.text("ALTER TABLE request_work_items ADD COLUMN IF NOT EXISTS progress_updated_at TIMESTAMP"))
    op.execute(sa.text("UPDATE request_work_items SET progress=CASE WHEN status='COMPLETED' THEN 100 ELSE progress END"))
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS batch_path_profiles (
            id VARCHAR PRIMARY KEY,
            name VARCHAR NOT NULL,
            solver_path VARCHAR NOT NULL,
            working_directory VARCHAR NOT NULL,
            arguments_template VARCHAR NOT NULL,
            environment_json JSONB NOT NULL,
            task_type_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            is_active BOOLEAN NOT NULL DEFAULT true,
            updated_by VARCHAR NOT NULL,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
    """))
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS batch_dispatches (
            id VARCHAR PRIMARY KEY,
            work_item_id VARCHAR NOT NULL REFERENCES request_work_items(id),
            workflow_run_id VARCHAR NOT NULL UNIQUE REFERENCES workflow_runs(id),
            batch_profile_id VARCHAR NOT NULL REFERENCES batch_path_profiles(id),
            profile_snapshot_json JSONB NOT NULL,
            command_preview VARCHAR NOT NULL,
            status VARCHAR NOT NULL CHECK (status = 'RECORDED_DEMO'),
            created_by VARCHAR NOT NULL,
            created_at TIMESTAMP NOT NULL
        )
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_batch_dispatches_work_item ON batch_dispatches(work_item_id, created_at DESC)"))
    op.execute(sa.text("""
        INSERT INTO batch_path_profiles
            (id, name, solver_path, working_directory, arguments_template,
             environment_json, task_type_ids_json, is_active, updated_by, created_at, updated_at)
        VALUES ('radioss-demo', 'Radioss 배치 예시', 'C:\\Altair\\hwsolvers\\radioss.exe',
                'C:\\Simulation\\runs\\{request_id}', '-i {input} -nt {cores}',
                '{"OMP_NUM_THREADS":"{cores}"}'::jsonb, '["hpc-submit"]'::jsonb, true, 'system', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT (id) DO NOTHING
    """))
    op.execute(sa.text("""
        UPDATE batch_path_profiles
        SET task_type_ids_json='["hpc-submit"]'::jsonb
        WHERE id='radioss-demo' AND task_type_ids_json='[]'::jsonb
    """))
    op.execute(sa.text("""
        INSERT INTO quality_thresholds
            (criterion_key, project_id, analysis_key, label, threshold_double, unit, updated_by, updated_at)
        SELECT 'open_cell_stress_mpa', project_id, 'OPEN_CELL_STRESS',
               'Open Cell 응력 허용값', 75.0, 'MPa', 'system', CURRENT_TIMESTAMP
        FROM quality_thresholds
        WHERE criterion_key = 'chassis_rear_permanent_deformation_mm'
        ON CONFLICT (criterion_key) DO NOTHING
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS batch_dispatches"))
    op.execute(sa.text("DROP TABLE IF EXISTS batch_path_profiles"))
    op.execute(sa.text("ALTER TABLE request_work_items DROP COLUMN IF EXISTS progress_updated_at"))
    op.execute(sa.text("ALTER TABLE request_work_items DROP COLUMN IF EXISTS progress_updated_by"))
    op.execute(sa.text("ALTER TABLE request_work_items DROP COLUMN IF EXISTS progress"))
