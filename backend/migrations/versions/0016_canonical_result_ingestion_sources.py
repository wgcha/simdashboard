"""Add PostgreSQL source-identity reservations for canonical result ingestion.

Revision ID: 0016_result_ingestion_sources
Revises: 0015_legacy_drop_layout
"""
from __future__ import annotations

from alembic import op


revision = "0016_result_ingestion_sources"
down_revision = "0015_legacy_drop_layout"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # This is intentionally separate from analysis_run_metadata: pre-existing
    # metadata can contain legitimate historical duplicates, while rows here
    # are created only by the canonical UoW and commit only with a success.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS canonical_result_ingestion_sources (
            source_type VARCHAR NOT NULL,
            source_name VARCHAR NOT NULL,
            source_checksum VARCHAR NOT NULL,
            claimed_at TIMESTAMP NOT NULL,
            PRIMARY KEY (source_type, source_name, source_checksum)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS canonical_result_ingestion_sources")
