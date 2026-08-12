"""Add project access control, OIDC identities, assignees, and menu policy.

Revision ID: 0007_access_control_menu_policy
Revises: 0006_batch_attempts
"""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "0007_access_control_menu_policy"
down_revision = "0006_batch_attempts"
branch_labels = None
depends_on = None


MENU_DEFINITIONS = (
    ("portfolio", "운영 대시보드", "company.dashboard.view", "company", 10, True),
    ("dashboard", "해석 의뢰 현황", "project.data.view", "project", 20, True),
    ("intake", "의뢰 접수", "request.create", "project", 30, True),
    ("workbench", "해석 작업 실행", "project.data.view", "project", 40, True),
    ("data", "해석 데이터 등록", "result.import", "project", 50, True),
    ("workbench_admin", "작업 유형 관리", "system.catalog.manage", "system", 60, True),
    ("variables", "변수 카탈로그", "project.variable.manage", "project", 70, True),
    ("templates", "자동화 템플릿", "system.catalog.manage", "system", 80, True),
    ("schemas", "폴더 스키마", "system.catalog.manage", "system", 90, True),
    ("examples", "예제 갤러리", "project.data.view", "company", 100, True),
    ("help", "도움말", "company.dashboard.view", "company", 110, True),
    ("access_admin", "사용자·프로젝트 권한", "project.member.manage", "project", 120, True),
    ("menu_policy_admin", "권한 및 메뉴 정책", "system.menu_policy.manage", "system", 130, False),
    ("audit_admin", "감사로그", "audit.view", "system", 140, False),
)

DEFAULT_VISIBILITY = {
    "general": {
        "portfolio": True,
        "dashboard": True,
        "intake": False,
        "workbench": True,
        "data": False,
        "workbench_admin": False,
        "variables": False,
        "templates": False,
        "schemas": False,
        "examples": True,
        "help": True,
        "access_admin": False,
        "menu_policy_admin": False,
        "audit_admin": False,
    },
    "power": {
        "portfolio": True,
        "dashboard": True,
        "intake": True,
        "workbench": True,
        "data": True,
        "workbench_admin": False,
        "variables": False,
        "templates": False,
        "schemas": False,
        "examples": True,
        "help": True,
        "access_admin": False,
        "menu_policy_admin": False,
        "audit_admin": False,
    },
    "admin": {
        "portfolio": True,
        "dashboard": True,
        "intake": True,
        "workbench": True,
        "data": True,
        "workbench_admin": False,
        "variables": True,
        "templates": False,
        "schemas": False,
        "examples": True,
        "help": True,
        "access_admin": True,
        "menu_policy_admin": False,
        "audit_admin": False,
    },
}


def _backfill_owner(table_name: str) -> None:
    # A row is linked only when one distinct ACTIVE user matches the preserved
    # display-name/username snapshot. Ambiguous names deliberately stay NULL.
    op.execute(
        sa.text(
            f"""
            WITH matches AS (
                SELECT target.id AS target_id, min(users.id) AS user_id,
                       count(DISTINCT users.id) AS match_count
                FROM {table_name} AS target
                JOIN users
                  ON lower(btrim(target.owner)) = lower(btrim(users.display_name))
                  OR lower(btrim(target.owner)) = lower(btrim(users.username))
                WHERE target.owner IS NOT NULL
                  AND users.account_status = 'ACTIVE'
                GROUP BY target.id
            )
            UPDATE {table_name} AS target
               SET owner_user_id = matches.user_id
              FROM matches
             WHERE target.id = matches.target_id
               AND matches.match_count = 1
               AND target.owner_user_id IS NULL
            """
        )
    )


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    legacy_conversion_pending = "account_status" not in user_columns or "is_global_admin" not in user_columns
    if "role" in user_columns and "legacy_role" not in user_columns:
        op.execute(sa.text("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_role"))
        op.execute(sa.text("ALTER TABLE users RENAME COLUMN role TO legacy_role"))
        user_columns.remove("role")
        user_columns.add("legacy_role")
    op.execute(sa.text("ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL"))
    op.execute(sa.text("ALTER TABLE users ALTER COLUMN legacy_role DROP NOT NULL"))
    user_additions = {
        "employee_id": "VARCHAR",
        "email": "VARCHAR",
        "department": "VARCHAR",
        "job_title": "VARCHAR",
        "oidc_issuer": "VARCHAR",
        "oidc_subject": "VARCHAR",
        "account_status": "VARCHAR NOT NULL DEFAULT 'ACTIVE'",
        "is_global_admin": "BOOLEAN NOT NULL DEFAULT false",
        "approved_by": "VARCHAR",
        "approved_at": "TIMESTAMP",
        "last_login_at": "TIMESTAMP",
    }
    for column_name, definition in user_additions.items():
        if column_name not in user_columns:
            op.execute(sa.text(f"ALTER TABLE users ADD COLUMN {column_name} {definition}"))
    check_names = {constraint.get("name") for constraint in inspector.get_check_constraints("users")}
    if "ck_users_account_status" not in check_names:
        op.execute(
            sa.text(
                "ALTER TABLE users ADD CONSTRAINT ck_users_account_status "
                "CHECK (account_status IN ('PENDING', 'ACTIVE', 'SUSPENDED'))"
            )
        )
    if legacy_conversion_pending:
        op.execute(
            sa.text(
                "UPDATE users SET account_status='ACTIVE', "
                "is_global_admin=CASE WHEN legacy_role='admin' THEN true ELSE false END"
            )
        )
    op.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS uq_users_employee_id ON users(employee_id) WHERE employee_id IS NOT NULL"))
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_oidc_identity ON users(oidc_issuer, oidc_subject) "
            "WHERE oidc_issuer IS NOT NULL AND oidc_subject IS NOT NULL"
        )
    )
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_users_account_status ON users(account_status)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_users_employee_id ON users(employee_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_users_oidc_identity ON users(oidc_issuer, oidc_subject)"))

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS project_memberships (
                id VARCHAR PRIMARY KEY,
                project_id VARCHAR NOT NULL,
                user_id VARCHAR NOT NULL,
                role VARCHAR NOT NULL,
                created_by VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                updated_by VARCHAR NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                CONSTRAINT uq_project_membership UNIQUE (project_id, user_id),
                CONSTRAINT ck_project_membership_role CHECK (role IN ('general', 'power', 'admin')),
                CONSTRAINT fk_project_memberships_project FOREIGN KEY (project_id) REFERENCES projects(id),
                CONSTRAINT fk_project_memberships_user FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )
    )
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_project_memberships_user_project ON project_memberships(user_id, project_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_project_memberships_project_role ON project_memberships(project_id, role, user_id)"))
    if legacy_conversion_pending:
        op.execute(
            sa.text(
                """
                INSERT INTO project_memberships
                    (id, project_id, user_id, role, created_by, created_at, updated_by, updated_at)
                SELECT 'membership-' || substr(md5(projects.id || ':' || users.id), 1, 24),
                       projects.id, users.id,
                       CASE users.legacy_role WHEN 'editor' THEN 'power' ELSE 'general' END,
                       'migration-0007', CURRENT_TIMESTAMP, 'migration-0007', CURRENT_TIMESTAMP
                FROM projects CROSS JOIN users
                WHERE users.legacy_role IN ('viewer', 'editor')
                ON CONFLICT (project_id, user_id) DO NOTHING
                """
            )
        )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS project_invitations (
                id VARCHAR PRIMARY KEY,
                project_id VARCHAR NOT NULL,
                employee_id VARCHAR NOT NULL,
                display_name_snapshot VARCHAR NOT NULL,
                department_snapshot VARCHAR,
                desired_role VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                resolved_user_id VARCHAR,
                invited_by VARCHAR NOT NULL,
                invited_at TIMESTAMP NOT NULL,
                resolved_by VARCHAR,
                resolved_at TIMESTAMP,
                cancelled_by VARCHAR,
                cancelled_at TIMESTAMP,
                CONSTRAINT ck_project_invitation_role CHECK (desired_role IN ('general', 'power', 'admin')),
                CONSTRAINT ck_project_invitation_status CHECK (
                    status IN ('PENDING_ACCOUNT', 'PENDING_APPROVAL', 'READY', 'COMPLETED', 'CANCELLED')
                ),
                CONSTRAINT fk_project_invitations_project FOREIGN KEY (project_id) REFERENCES projects(id),
                CONSTRAINT fk_project_invitations_user FOREIGN KEY (resolved_user_id) REFERENCES users(id)
            )
            """
        )
    )
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_project_invitations_project_status ON project_invitations(project_id, status, invited_at DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_project_invitations_employee ON project_invitations(employee_id, status)"))
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_project_invitations_open
            ON project_invitations(project_id, employee_id)
            WHERE status IN ('PENDING_ACCOUNT', 'PENDING_APPROVAL', 'READY')
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS menu_definitions (
                id VARCHAR PRIMARY KEY,
                label VARCHAR NOT NULL,
                required_permission VARCHAR NOT NULL,
                context_kind VARCHAR NOT NULL,
                sequence_no INTEGER NOT NULL,
                is_policy_editable BOOLEAN NOT NULL DEFAULT true,
                is_active BOOLEAN NOT NULL DEFAULT true,
                CONSTRAINT ck_menu_context_kind CHECK (context_kind IN ('company', 'project', 'system'))
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS menu_policy_state (
                id VARCHAR PRIMARY KEY,
                version INTEGER NOT NULL,
                updated_by VARCHAR NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                CONSTRAINT ck_menu_policy_singleton CHECK (id = 'global')
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS role_menu_policies (
                role VARCHAR NOT NULL,
                menu_id VARCHAR NOT NULL,
                is_visible BOOLEAN NOT NULL,
                policy_version INTEGER NOT NULL,
                updated_by VARCHAR NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                PRIMARY KEY (role, menu_id),
                CONSTRAINT ck_role_menu_policy_role CHECK (role IN ('general', 'power', 'admin')),
                CONSTRAINT fk_role_menu_policies_menu FOREIGN KEY (menu_id) REFERENCES menu_definitions(id)
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS menu_policy_versions (
                version INTEGER PRIMARY KEY,
                definition_json JSONB NOT NULL,
                created_by VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                source_version INTEGER,
                change_note VARCHAR
            )
            """
        )
    )

    for menu_id, label, permission, context_kind, sequence_no, editable in MENU_DEFINITIONS:
        connection.execute(
            sa.text(
                """
                INSERT INTO menu_definitions
                    (id, label, required_permission, context_kind, sequence_no, is_policy_editable, is_active)
                VALUES (:id, :label, :permission, :context_kind, :sequence_no, :editable, true)
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": menu_id,
                "label": label,
                "permission": permission,
                "context_kind": context_kind,
                "sequence_no": sequence_no,
                "editable": editable,
            },
        )
    connection.execute(
        sa.text(
            "INSERT INTO menu_policy_state (id, version, updated_by, updated_at) "
            "VALUES ('global', 1, 'migration-0007', CURRENT_TIMESTAMP) ON CONFLICT (id) DO NOTHING"
        )
    )
    for role, visibility in DEFAULT_VISIBILITY.items():
        for menu_id, is_visible in visibility.items():
            connection.execute(
                sa.text(
                    """
                    INSERT INTO role_menu_policies
                        (role, menu_id, is_visible, policy_version, updated_by, updated_at)
                    VALUES (:role, :menu_id, :is_visible, 1, 'migration-0007', CURRENT_TIMESTAMP)
                    ON CONFLICT (role, menu_id) DO NOTHING
                    """
                ),
                {"role": role, "menu_id": menu_id, "is_visible": is_visible},
            )
    connection.execute(
        sa.text(
            """
            INSERT INTO menu_policy_versions
                (version, definition_json, created_by, created_at, source_version, change_note)
            VALUES (1, CAST(:definition AS JSONB), 'migration-0007', CURRENT_TIMESTAMP, NULL, 'Initial seeded policy')
            ON CONFLICT (version) DO NOTHING
            """
        ),
        {"definition": json.dumps(DEFAULT_VISIBILITY, ensure_ascii=False, sort_keys=True)},
    )

    project_layouts_preexisting = inspector.has_table("project_workspace_layouts")
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS project_workspace_layouts (
                project_id VARCHAR NOT NULL REFERENCES projects(id),
                layout_kind VARCHAR NOT NULL,
                version INTEGER NOT NULL,
                definition_json JSONB NOT NULL,
                updated_by VARCHAR NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                PRIMARY KEY (project_id, layout_kind)
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS project_workspace_layout_versions (
                project_id VARCHAR NOT NULL REFERENCES projects(id),
                layout_kind VARCHAR NOT NULL,
                version INTEGER NOT NULL,
                definition_json JSONB NOT NULL,
                created_by VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                is_valid BOOLEAN NOT NULL DEFAULT true,
                PRIMARY KEY (project_id, layout_kind, version)
            )
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS idx_project_workspace_layouts_project "
            "ON project_workspace_layouts(project_id, layout_kind)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS idx_project_workspace_layout_versions_project "
            "ON project_workspace_layout_versions(project_id, layout_kind, version DESC)"
        )
    )
    if not project_layouts_preexisting:
        op.execute(
            sa.text(
                """
                INSERT INTO project_workspace_layouts
                    (project_id, layout_kind, version, definition_json, updated_by, updated_at)
                SELECT projects.id, layouts.layout_kind, layouts.version, layouts.definition_json,
                       layouts.updated_by, layouts.updated_at
                FROM projects CROSS JOIN workspace_layouts AS layouts
                ON CONFLICT (project_id, layout_kind) DO NOTHING
                """
            )
        )
        op.execute(
            sa.text(
                """
                INSERT INTO project_workspace_layout_versions
                    (project_id, layout_kind, version, definition_json, created_by, created_at, is_valid)
                SELECT projects.id, versions.layout_kind, versions.version, versions.definition_json,
                       versions.created_by, versions.created_at, versions.is_valid
                FROM projects CROSS JOIN workspace_layout_versions AS versions
                ON CONFLICT (project_id, layout_kind, version) DO NOTHING
                """
            )
        )

    inspector = sa.inspect(connection)
    for table_name in ("analysis_requests", "request_steps", "request_work_items"):
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        if "owner_user_id" not in columns:
            op.execute(sa.text(f"ALTER TABLE {table_name} ADD COLUMN owner_user_id VARCHAR"))
        op.execute(sa.text(f"CREATE INDEX IF NOT EXISTS idx_{table_name}_owner_user_id ON {table_name}(owner_user_id)"))
        if legacy_conversion_pending:
            _backfill_owner(table_name)


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS project_workspace_layout_versions"))
    op.execute(sa.text("DROP TABLE IF EXISTS project_workspace_layouts"))
    for table_name in ("request_work_items", "request_steps", "analysis_requests"):
        op.execute(sa.text(f"DROP INDEX IF EXISTS idx_{table_name}_owner_user_id"))
        op.execute(sa.text(f"ALTER TABLE {table_name} DROP COLUMN IF EXISTS owner_user_id"))

    op.execute(sa.text("DROP TABLE IF EXISTS menu_policy_versions"))
    op.execute(sa.text("DROP TABLE IF EXISTS role_menu_policies"))
    op.execute(sa.text("DROP TABLE IF EXISTS menu_policy_state"))
    op.execute(sa.text("DROP TABLE IF EXISTS menu_definitions"))
    op.execute(sa.text("DROP TABLE IF EXISTS project_invitations"))
    op.execute(sa.text("DROP TABLE IF EXISTS project_memberships"))

    op.execute(sa.text("DROP INDEX IF EXISTS uq_users_oidc_identity"))
    op.execute(sa.text("DROP INDEX IF EXISTS uq_users_employee_id"))
    op.execute(sa.text("DROP INDEX IF EXISTS idx_users_oidc_identity"))
    op.execute(sa.text("DROP INDEX IF EXISTS idx_users_employee_id"))
    op.execute(sa.text("DROP INDEX IF EXISTS idx_users_account_status"))
    op.execute(sa.text("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_account_status"))
    for column_name in (
        "last_login_at",
        "approved_at",
        "approved_by",
        "is_global_admin",
        "account_status",
        "oidc_subject",
        "oidc_issuer",
        "job_title",
        "department",
        "email",
        "employee_id",
    ):
        op.execute(sa.text(f"ALTER TABLE users DROP COLUMN IF EXISTS {column_name}"))
    op.execute(sa.text("UPDATE users SET legacy_role='viewer' WHERE legacy_role IS NULL"))
    op.execute(sa.text("ALTER TABLE users RENAME COLUMN legacy_role TO role"))
    op.execute(
        sa.text(
            "ALTER TABLE users ADD CONSTRAINT ck_users_role "
            "CHECK (role IN ('viewer', 'editor', 'admin'))"
        )
    )
