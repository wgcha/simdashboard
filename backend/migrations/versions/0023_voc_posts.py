"""Add append-only voice-of-customer posts."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0023_voc_posts"
down_revision = "0022_managed_local_execution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Author fields intentionally contain snapshots. Do not add a user foreign
    # key: historical VOC remains readable across account lifecycle changes.
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS voc_posts (
            id VARCHAR PRIMARY KEY,
            author_user_id VARCHAR NOT NULL,
            author_username VARCHAR NOT NULL,
            author_display_name VARCHAR NOT NULL,
            content TEXT NOT NULL CHECK (char_length(content) BETWEEN 1 AND 10000),
            created_at TIMESTAMPTZ NOT NULL
        )
    """))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_voc_posts_created_at_id ON voc_posts(created_at, id)"
    ))
    # Seed only the newly introduced menu rows. Existing visibility decisions
    # and policy-version history remain administrator-owned.
    op.execute(sa.text("""
        INSERT INTO menu_definitions
            (id, label, required_permission, context_kind, sequence_no, is_policy_editable, is_active)
        VALUES ('voc', 'VOC 게시판', 'company.dashboard.view', 'company', 150, true, true)
        ON CONFLICT (id) DO NOTHING
    """))
    for role in ("general", "power", "admin"):
        op.execute(sa.text(f"""
            INSERT INTO role_menu_policies
                (role, menu_id, is_visible, policy_version, updated_by, updated_at)
            VALUES ('{role}', 'voc', true, 1, 'migration-0023', CURRENT_TIMESTAMP)
            ON CONFLICT (role, menu_id) DO NOTHING
        """))


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM role_menu_policies WHERE menu_id='voc'"))
    op.execute(sa.text("DELETE FROM menu_definitions WHERE id='voc'"))
    op.execute(sa.text("DROP TABLE voc_posts"))
