"""Add Run Identity V2 provenance and import-outcome history.

Revision ID: 0017_run_identity_v2
Revises: 0016_result_ingestion_sources
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0017_run_identity_v2"
down_revision = "0016_result_ingestion_sources"
branch_labels = None
depends_on = None


_HISTORY_COLUMNS = (
    ("source_type", "VARCHAR"),
    ("source_checksum", "VARCHAR"),
    ("source_run_id", "VARCHAR"),
    ("conflict_policy", "VARCHAR"),
    ("outcome_reason", "VARCHAR"),
    ("replaced_analysis_run_id", "VARCHAR"),
    ("completed_at", "TIMESTAMP"),
)


def upgrade() -> None:
    # Do not silently choose a historical keeper. A load case's run number is
    # now an identity slot and legacy ambiguity must be remediated explicitly.
    op.execute(
        sa.text(
            """DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM analysis_runs
                    GROUP BY load_case_id, run_no HAVING count(*) > 1
                ) THEN
                    RAISE EXCEPTION 'duplicate analysis_runs run_no for load case; remediate before Run Identity V2';
                END IF;
                IF EXISTS (
                    SELECT 1
                    FROM canonical_result_ingestion_sources AS legacy
                    LEFT JOIN analysis_run_metadata AS metadata
                      ON metadata.source_type = legacy.source_type
                     AND metadata.source_name = legacy.source_name
                     AND metadata.source_checksum = legacy.source_checksum
                    LEFT JOIN analysis_runs AS run
                      ON run.id = metadata.analysis_run_id
                    GROUP BY legacy.source_type, legacy.source_name, legacy.source_checksum
                    HAVING count(run.id) <> 1
                ) THEN
                    RAISE EXCEPTION 'legacy canonical source claim must map to exactly one analysis run';
                END IF;
            END $$"""
        )
    )
    for column, data_type in _HISTORY_COLUMNS:
        op.execute(sa.text(f"ALTER TABLE folder_import_jobs ADD COLUMN IF NOT EXISTS {column} {data_type}"))

    # ``0001_initial`` bootstraps from the current schema snapshot, so a blank
    # database can already contain this future constraint before Alembic walks
    # through 0017.  PostgreSQL has no ``ADD CONSTRAINT IF NOT EXISTS`` syntax;
    # guard by relation + constraint name to keep both blank installs and
    # upgrades from a historical 0016 database valid.
    op.execute(
        sa.text(
            """DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = 'fk_folder_import_jobs_replaced_run'
                      AND conrelid = 'folder_import_jobs'::regclass
                ) THEN
                    ALTER TABLE folder_import_jobs
                    ADD CONSTRAINT fk_folder_import_jobs_replaced_run
                    FOREIGN KEY (replaced_analysis_run_id) REFERENCES analysis_runs(id);
                END IF;
            END $$"""
        )
    )
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_folder_import_jobs_source_identity ON folder_import_jobs(source_type, source_checksum)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_folder_import_jobs_source_run ON folder_import_jobs(source_run_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_folder_import_jobs_replaced_run ON folder_import_jobs(replaced_analysis_run_id)"))
    op.execute(
        sa.text(
            """CREATE TABLE IF NOT EXISTS canonical_result_ingestion_source_versions (
                load_case_id VARCHAR NOT NULL,
                source_type VARCHAR NOT NULL,
                source_key VARCHAR NOT NULL,
                source_run_id VARCHAR,
                source_name VARCHAR NOT NULL,
                source_checksum VARCHAR NOT NULL,
                source_revision INTEGER NOT NULL CHECK (source_revision > 0),
                analysis_run_id VARCHAR NOT NULL,
                conflict_policy VARCHAR NOT NULL,
                supersedes_analysis_run_id VARCHAR,
                claimed_at TIMESTAMP NOT NULL,
                PRIMARY KEY (load_case_id, source_type, source_key, source_revision),
                UNIQUE (load_case_id, source_type, source_key, source_checksum),
                UNIQUE (analysis_run_id),
                FOREIGN KEY (load_case_id) REFERENCES load_cases(id),
                FOREIGN KEY (analysis_run_id) REFERENCES analysis_runs(id),
                FOREIGN KEY (supersedes_analysis_run_id) REFERENCES analysis_runs(id)
            )"""
        )
    )
    # Each 0016 reservation was committed with a canonical result. The
    # preflight above proves every claim has one, and only one, matching run.
    # Revision numbers are deterministic per source slot; no historical row
    # is dropped or selected as a preferred duplicate.
    op.execute(
        sa.text(
            """WITH legacy_matches AS (
                SELECT
                    run.load_case_id,
                    legacy.source_type,
                    legacy.source_name,
                    legacy.source_checksum,
                    run.id AS analysis_run_id,
                    legacy.claimed_at,
                    row_number() OVER (
                        PARTITION BY run.load_case_id, legacy.source_type, legacy.source_name
                        ORDER BY legacy.claimed_at, legacy.source_checksum, run.id
                    ) AS source_revision
                FROM canonical_result_ingestion_sources AS legacy
                JOIN analysis_run_metadata AS metadata
                  ON metadata.source_type = legacy.source_type
                 AND metadata.source_name = legacy.source_name
                 AND metadata.source_checksum = legacy.source_checksum
                JOIN analysis_runs AS run ON run.id = metadata.analysis_run_id
            )
            INSERT INTO canonical_result_ingestion_source_versions (
                load_case_id, source_type, source_key, source_run_id, source_name,
                source_checksum, source_revision, analysis_run_id, conflict_policy,
                supersedes_analysis_run_id, claimed_at
            )
            SELECT
                load_case_id, source_type, 'name:' || source_name, NULL, source_name,
                source_checksum, source_revision, analysis_run_id, 'LEGACY_APPEND',
                NULL, claimed_at
            FROM legacy_matches"""
        )
    )
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_canonical_result_ingestion_source_versions_lookup ON canonical_result_ingestion_source_versions(load_case_id, source_type, source_key, source_run_id, source_revision DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_canonical_result_ingestion_source_versions_supersedes_run ON canonical_result_ingestion_source_versions(supersedes_analysis_run_id)"))
    op.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS ux_analysis_runs_load_case_run_no ON analysis_runs(load_case_id, run_no)"))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ux_analysis_runs_load_case_run_no"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_canonical_result_ingestion_source_versions_supersedes_run"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_canonical_result_ingestion_source_versions_lookup"))
    op.execute(sa.text("DROP TABLE IF EXISTS canonical_result_ingestion_source_versions"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_folder_import_jobs_replaced_run"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_folder_import_jobs_source_run"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_folder_import_jobs_source_identity"))
    op.execute(sa.text("ALTER TABLE folder_import_jobs DROP CONSTRAINT IF EXISTS fk_folder_import_jobs_replaced_run"))
    for column, _data_type in reversed(_HISTORY_COLUMNS):
        op.execute(sa.text(f"ALTER TABLE folder_import_jobs DROP COLUMN IF EXISTS {column}"))
