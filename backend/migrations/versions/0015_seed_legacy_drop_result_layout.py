"""Persist the explicit legacy-domain assignment for the seeded drop demo."""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "0015_legacy_drop_layout"
down_revision = "0014_result_profile_revs"
branch_labels = None
depends_on = None
LEGACY_DROP_SNAPSHOT = {
    "template_id": "legacy-domain-dashboard",
    "template_version": 1,
    "template_name": "기존 낙하 상세 분석",
    "request_type_id": "design-reliability-validation",
    "request_type_version": 1,
    "pages": [],
    "required_data_contracts": [],
    "legacy_renderer": "LEGACY_DOMAIN",
}


def upgrade() -> None:
    snapshot_json = json.dumps(LEGACY_DROP_SNAPSHOT, ensure_ascii=False, separators=(",", ":")).replace(":", r"\:")
    op.execute(sa.text(f"""
        INSERT INTO request_result_layout_snapshots
            (request_id, source_request_type_id, source_request_type_version, source_template_id,
             source_template_version, snapshot_json, snapshot_reason, created_by, created_at)
        SELECT
            'request-drop-001', 'design-reliability-validation', 1, 'legacy-domain-dashboard', 1,
            CAST('{snapshot_json}' AS JSONB),
            'LEGACY_ASSIGNED', 'migration-0015-legacy-domain', CURRENT_TIMESTAMP
        WHERE EXISTS (SELECT 1 FROM analysis_requests WHERE id='request-drop-001')
        ON CONFLICT (request_id) DO NOTHING
    """))


def downgrade() -> None:
    op.execute("""
        DELETE FROM request_result_layout_snapshots
        WHERE request_id='request-drop-001'
          AND snapshot_reason='LEGACY_ASSIGNED'
          AND created_by='migration-0015-legacy-domain'
    """)
