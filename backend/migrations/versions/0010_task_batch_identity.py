"""Enforce one batch execution definition per task type version.

Revision ID: 0010_task_batch_identity
Revises: 0009_menu_workflow_order
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0010_task_batch_identity"
down_revision = "0009_menu_workflow_order"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("ALTER TABLE batch_path_profiles ADD COLUMN IF NOT EXISTS task_type_id VARCHAR"))
    op.execute(sa.text("ALTER TABLE batch_path_profiles ADD COLUMN IF NOT EXISTS task_type_version INTEGER NOT NULL DEFAULT 1"))
    op.execute(sa.text("ALTER TABLE batch_path_profile_versions ADD COLUMN IF NOT EXISTS task_type_id VARCHAR"))
    op.execute(sa.text("ALTER TABLE batch_path_profile_versions ADD COLUMN IF NOT EXISTS task_type_version INTEGER NOT NULL DEFAULT 1"))
    # Preserve legacy profiles where the old many-to-many array identified one
    # task. Ambiguous rows remain readable and must be remediated by an admin.
    op.execute(sa.text("""
        UPDATE batch_path_profiles
        SET task_type_id = task_type_ids_json->>0
        WHERE task_type_id IS NULL
          AND jsonb_typeof(task_type_ids_json) = 'array'
          AND jsonb_array_length(task_type_ids_json) = 1
    """))
    # If legacy data had several profiles pointing at the same task/version,
    # retain the lexicographically first profile as the canonical definition
    # and clear the normalized identity on the others. Their legacy JSON is
    # preserved for an administrator to remediate, so the unique index can be
    # created without silently deleting data.
    op.execute(sa.text("""
        UPDATE batch_path_profiles AS duplicate
        SET task_type_id = NULL
        WHERE duplicate.task_type_id IS NOT NULL
          AND EXISTS (
              SELECT 1 FROM batch_path_profiles AS keeper
              WHERE keeper.task_type_id = duplicate.task_type_id
                AND keeper.task_type_version = duplicate.task_type_version
                AND keeper.id < duplicate.id
          )
    """))
    op.execute(sa.text("""
        UPDATE batch_path_profile_versions v
        SET task_type_id = p.task_type_id, task_type_version = p.task_type_version
        FROM batch_path_profiles p
        WHERE v.id = p.id
    """))
    op.execute(sa.text("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_batch_profile_task_version
        ON batch_path_profiles(task_type_id, task_type_version)
        WHERE task_type_id IS NOT NULL
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_batch_profile_task_version ON batch_path_profiles(task_type_id, task_type_version)"))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_batch_profile_task_version"))
    op.execute(sa.text("DROP INDEX IF EXISTS ux_batch_profile_task_version"))
    op.execute(sa.text("ALTER TABLE batch_path_profile_versions DROP COLUMN IF EXISTS task_type_version"))
    op.execute(sa.text("ALTER TABLE batch_path_profile_versions DROP COLUMN IF EXISTS task_type_id"))
    op.execute(sa.text("ALTER TABLE batch_path_profiles DROP COLUMN IF EXISTS task_type_version"))
    op.execute(sa.text("ALTER TABLE batch_path_profiles DROP COLUMN IF EXISTS task_type_id"))
