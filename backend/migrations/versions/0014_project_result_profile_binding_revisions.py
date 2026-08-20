"""Version immutable project result-profile bindings."""

from __future__ import annotations

from alembic import op

revision = "0014_result_profile_revs"
down_revision = "0013_project_result_profile_menu"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("ALTER TABLE project_request_type_result_profiles ADD COLUMN IF NOT EXISTS binding_version INTEGER")
    op.execute("UPDATE project_request_type_result_profiles SET binding_version=1 WHERE binding_version IS NULL")
    op.execute("ALTER TABLE project_request_type_result_profiles ALTER COLUMN binding_version SET NOT NULL")
    op.execute("ALTER TABLE project_request_type_result_profiles DROP CONSTRAINT IF EXISTS project_request_type_result_profiles_pkey")
    op.execute("ALTER TABLE project_request_type_result_profiles ADD PRIMARY KEY (project_id, request_type_id, request_type_version, binding_version)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_project_result_profiles_latest ON project_request_type_result_profiles(project_id, request_type_id, request_type_version, binding_version DESC)")

def downgrade() -> None:
    # The pre-0014 primary key cannot represent revision history.  Retain the
    # latest immutable binding for each key before restoring that old shape.
    op.execute("""
        DELETE FROM project_request_type_result_profiles AS older
        USING project_request_type_result_profiles AS newer
        WHERE older.project_id = newer.project_id
          AND older.request_type_id = newer.request_type_id
          AND older.request_type_version = newer.request_type_version
          AND older.binding_version < newer.binding_version
    """)
    op.execute("DROP INDEX IF EXISTS ix_project_result_profiles_latest")
    op.execute("ALTER TABLE project_request_type_result_profiles DROP CONSTRAINT IF EXISTS project_request_type_result_profiles_pkey")
    op.execute("ALTER TABLE project_request_type_result_profiles ADD PRIMARY KEY (project_id, request_type_id, request_type_version)")
    op.execute("ALTER TABLE project_request_type_result_profiles DROP COLUMN IF EXISTS binding_version")
