"""Expose project-scoped result-profile binding to project admins."""

from __future__ import annotations

from alembic import op


revision = "0013_project_result_profile_menu"
down_revision = "0012_project_result_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO menu_definitions (id, label, required_permission, context_kind, sequence_no, is_policy_editable)
        VALUES ('project_result_profiles', '프로젝트 결과 구성', 'dashboard.edit', 'project', 65, true)
        ON CONFLICT (id) DO UPDATE SET label=excluded.label, required_permission=excluded.required_permission,
          context_kind=excluded.context_kind, sequence_no=excluded.sequence_no, is_policy_editable=excluded.is_policy_editable
    """)
    for role, visible in (("general", False), ("power", False), ("admin", True)):
        op.execute(f"""
            INSERT INTO role_menu_policies (role, menu_id, is_visible, policy_version, updated_by, updated_at)
            VALUES ('{role}', 'project_result_profiles', {str(visible).lower()}, 1, 'migration', CURRENT_TIMESTAMP)
            ON CONFLICT (role, menu_id) DO UPDATE SET is_visible=excluded.is_visible, updated_by=excluded.updated_by, updated_at=excluded.updated_at
        """)


def downgrade() -> None:
    op.execute("DELETE FROM role_menu_policies WHERE menu_id='project_result_profiles'")
    op.execute("DELETE FROM menu_definitions WHERE id='project_result_profiles'")
