"""Order navigation menus around the request execution workflow.

Revision ID: 0009_menu_workflow_order
Revises: 0008_media_blob_storage
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0009_menu_workflow_order"
down_revision = "0008_media_blob_storage"
branch_labels = None
depends_on = None


WORKFLOW_MENU_ORDER = (
    ("portfolio", 10),
    ("dashboard", 20),
    ("intake", 30),
    ("workbench", 40),
    ("data", 50),
    ("workbench_admin", 60),
    ("schemas", 70),
    ("variables", 80),
    ("templates", 90),
    ("access_admin", 100),
    ("menu_policy_admin", 110),
    ("audit_admin", 120),
    ("examples", 130),
    ("help", 140),
)

PREVIOUS_MENU_ORDER = (
    ("portfolio", 10),
    ("dashboard", 20),
    ("intake", 30),
    ("workbench", 40),
    ("data", 50),
    ("workbench_admin", 60),
    ("variables", 70),
    ("templates", 80),
    ("schemas", 90),
    ("examples", 100),
    ("help", 110),
    ("access_admin", 120),
    ("menu_policy_admin", 130),
    ("audit_admin", 140),
)


def _apply(order: tuple[tuple[str, int], ...]) -> None:
    connection = op.get_bind()
    for menu_id, sequence_no in order:
        connection.execute(
            sa.text("UPDATE menu_definitions SET sequence_no=:sequence_no WHERE id=:menu_id"),
            {"menu_id": menu_id, "sequence_no": sequence_no},
        )


def upgrade() -> None:
    _apply(WORKFLOW_MENU_ORDER)


def downgrade() -> None:
    _apply(PREVIOUS_MENU_ORDER)
