"""Add the project-scoped materials dashboard menu."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0032_materials_dashboard_menu"
down_revision = "0031_result_registration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Seed only the new menu rows. Existing role visibility is administrator-owned.
    op.execute(sa.text("""
        INSERT INTO menu_definitions
            (id, label, required_permission, context_kind, sequence_no, is_policy_editable, is_active)
        VALUES ('materials', '모델 소재·물성', 'project.data.view', 'project', 55, true, true)
        ON CONFLICT (id) DO NOTHING
    """))
    for role in ("general", "power", "admin"):
        op.execute(sa.text(f"""
            INSERT INTO role_menu_policies
                (role, menu_id, is_visible, policy_version, updated_by, updated_at)
            VALUES ('{role}', 'materials', true, 1, 'migration-0032', CURRENT_TIMESTAMP)
            ON CONFLICT (role, menu_id) DO NOTHING
        """))


def downgrade() -> None:
    raise RuntimeError("Materials menu visibility is administrator-owned; destructive downgrade is not supported.")
