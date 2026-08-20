from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Literal, Protocol, cast

from fastapi import HTTPException, Request

from .config import database_settings, security_settings
from .database_connection import ConnectionLike, connect


AccountStatus = Literal["PENDING", "ACTIVE", "SUSPENDED"]
ProjectRole = Literal["general", "power", "admin"]
Permission = Literal[
    "company.dashboard.view",
    "project.data.view",
    "report.export",
    "work.execute_assigned",
    "work.execute_any",
    "request.create",
    "request.edit",
    "workflow.edit",
    "result.import",
    "result.review",
    "dashboard.edit",
    "project.layout.edit",
    "project.threshold.manage",
    "project.variable.manage",
    "project.member.manage",
    "project.invitation.create",
    "system.catalog.manage",
    "system.user.approve",
    "system.menu_policy.manage",
    "audit.view",
]

COMPANY_DASHBOARD_VIEW: Final[Permission] = "company.dashboard.view"
PROJECT_DATA_VIEW: Final[Permission] = "project.data.view"
REPORT_EXPORT: Final[Permission] = "report.export"
WORK_EXECUTE_ASSIGNED: Final[Permission] = "work.execute_assigned"
WORK_EXECUTE_ANY: Final[Permission] = "work.execute_any"
REQUEST_CREATE: Final[Permission] = "request.create"
REQUEST_EDIT: Final[Permission] = "request.edit"
WORKFLOW_EDIT: Final[Permission] = "workflow.edit"
RESULT_IMPORT: Final[Permission] = "result.import"
RESULT_REVIEW: Final[Permission] = "result.review"
DASHBOARD_EDIT: Final[Permission] = "dashboard.edit"
PROJECT_LAYOUT_EDIT: Final[Permission] = "project.layout.edit"
PROJECT_THRESHOLD_MANAGE: Final[Permission] = "project.threshold.manage"
PROJECT_VARIABLE_MANAGE: Final[Permission] = "project.variable.manage"
PROJECT_MEMBER_MANAGE: Final[Permission] = "project.member.manage"
PROJECT_INVITATION_CREATE: Final[Permission] = "project.invitation.create"
SYSTEM_CATALOG_MANAGE: Final[Permission] = "system.catalog.manage"
SYSTEM_USER_APPROVE: Final[Permission] = "system.user.approve"
SYSTEM_MENU_POLICY_MANAGE: Final[Permission] = "system.menu_policy.manage"
AUDIT_VIEW: Final[Permission] = "audit.view"

GENERAL_PERMISSIONS: Final[frozenset[Permission]] = frozenset(
    {
        COMPANY_DASHBOARD_VIEW,
        PROJECT_DATA_VIEW,
        REPORT_EXPORT,
        WORK_EXECUTE_ASSIGNED,
    }
)
POWER_PERMISSIONS: Final[frozenset[Permission]] = GENERAL_PERMISSIONS | frozenset(
    {
        REQUEST_CREATE,
        REQUEST_EDIT,
        WORKFLOW_EDIT,
        RESULT_IMPORT,
        RESULT_REVIEW,
    }
)
PROJECT_ADMIN_PERMISSIONS: Final[frozenset[Permission]] = POWER_PERMISSIONS | frozenset(
    {
        DASHBOARD_EDIT,
        PROJECT_LAYOUT_EDIT,
        PROJECT_THRESHOLD_MANAGE,
        PROJECT_VARIABLE_MANAGE,
        PROJECT_MEMBER_MANAGE,
        PROJECT_INVITATION_CREATE,
    }
)
GLOBAL_ADMIN_PERMISSIONS: Final[frozenset[Permission]] = PROJECT_ADMIN_PERMISSIONS | frozenset(
    {
        WORK_EXECUTE_ANY,
        SYSTEM_CATALOG_MANAGE,
        SYSTEM_USER_APPROVE,
        SYSTEM_MENU_POLICY_MANAGE,
        AUDIT_VIEW,
    }
)
ALL_PERMISSIONS: Final[frozenset[Permission]] = GLOBAL_ADMIN_PERMISSIONS
ROLE_PERMISSIONS: Final[dict[ProjectRole, frozenset[Permission]]] = {
    "general": GENERAL_PERMISSIONS,
    "power": POWER_PERMISSIONS,
    "admin": PROJECT_ADMIN_PERMISSIONS,
}
COMPANY_PERMISSIONS: Final[frozenset[Permission]] = frozenset(
    {COMPANY_DASHBOARD_VIEW, PROJECT_DATA_VIEW, REPORT_EXPORT}
)


@dataclass(frozen=True)
class MenuDefinition:
    id: str
    label: str
    required_permission: Permission
    context_kind: Literal["company", "project", "system"]
    sequence_no: int
    is_policy_editable: bool = True


MENU_DEFINITIONS: Final[tuple[MenuDefinition, ...]] = (
    MenuDefinition("portfolio", "운영 대시보드", COMPANY_DASHBOARD_VIEW, "company", 10),
    MenuDefinition("dashboard", "해석 의뢰 현황", PROJECT_DATA_VIEW, "project", 20),
    MenuDefinition("intake", "의뢰 접수", REQUEST_CREATE, "project", 30),
    MenuDefinition("workbench", "해석 작업 실행", PROJECT_DATA_VIEW, "project", 40),
    MenuDefinition("data", "해석 데이터 등록", RESULT_IMPORT, "project", 50),
    MenuDefinition("workbench_admin", "작업 유형 관리", SYSTEM_CATALOG_MANAGE, "system", 60),
    MenuDefinition("project_result_profiles", "프로젝트 결과 구성", DASHBOARD_EDIT, "project", 65),
    MenuDefinition("schemas", "폴더 스키마", SYSTEM_CATALOG_MANAGE, "system", 70),
    MenuDefinition("variables", "변수 카탈로그", PROJECT_VARIABLE_MANAGE, "project", 80),
    MenuDefinition("templates", "자동화 템플릿", SYSTEM_CATALOG_MANAGE, "system", 90),
    MenuDefinition("access_admin", "사용자·프로젝트 권한", PROJECT_MEMBER_MANAGE, "project", 100),
    MenuDefinition("menu_policy_admin", "권한 및 메뉴 정책", SYSTEM_MENU_POLICY_MANAGE, "system", 110, False),
    MenuDefinition("audit_admin", "감사로그", AUDIT_VIEW, "system", 120, False),
    MenuDefinition("examples", "예제 갤러리", PROJECT_DATA_VIEW, "company", 130),
    MenuDefinition("help", "도움말", COMPANY_DASHBOARD_VIEW, "company", 140),
)

DEFAULT_MENU_VISIBILITY: Final[dict[ProjectRole, dict[str, bool]]] = {
    "general": {
        "portfolio": True,
        "dashboard": True,
        "intake": False,
        "workbench": True,
        "data": False,
        "workbench_admin": False,
        "project_result_profiles": False,
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
        "project_result_profiles": False,
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
        "project_result_profiles": True,
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


class PrincipalLike(Protocol):
    user_id: str
    account_status: AccountStatus
    is_global_admin: bool


@dataclass(frozen=True)
class AccessContext:
    principal: PrincipalLike
    permission: Permission
    project_id: str | None
    project_role: ProjectRole | None


@dataclass(frozen=True)
class ProjectAssignee:
    user_id: str
    display_name: str


@dataclass(frozen=True)
class _DatabasePrincipal:
    user_id: str
    account_status: AccountStatus
    is_global_admin: bool


def _locking_suffix() -> str:
    return " FOR SHARE" if database_settings().backend == "postgresql" else ""


def _fresh_principal(conn: ConnectionLike, principal: PrincipalLike) -> PrincipalLike:
    if principal.user_id == "local-admin" and security_settings().auth_mode == "disabled":
        return principal
    row = conn.execute(
        "SELECT account_status, is_global_admin FROM users WHERE id=?" + _locking_suffix(),
        [principal.user_id],
    ).fetchone()
    if not row or row[0] not in {"PENDING", "ACTIVE", "SUSPENDED"}:
        return _DatabasePrincipal(principal.user_id, "SUSPENDED", False)
    return _DatabasePrincipal(principal.user_id, cast(AccountStatus, row[0]), bool(row[1]))


def resolve_project_assignee(
    conn: ConnectionLike,
    project_id: str,
    owner_user_id: str,
) -> ProjectAssignee:
    """Resolve the canonical ACTIVE project member used for ownership.

    Display names are snapshots only. Authorization and execution always use
    the immutable internal user id returned here.
    """
    if owner_user_id == "local-admin" and security_settings().auth_mode != "disabled":
        raise HTTPException(
            422,
            {"code": "ASSIGNEE_PROJECT_MEMBERSHIP_REQUIRED", "project_id": project_id, "owner_user_id": owner_user_id},
        )
    row = conn.execute(
        """
        SELECT users.id, users.display_name, users.account_status
        FROM users
        JOIN project_memberships memberships ON memberships.user_id=users.id
        WHERE users.id=? AND memberships.project_id=?
        """ + _locking_suffix(),
        [owner_user_id, project_id],
    ).fetchone()
    if not row:
        raise HTTPException(
            422,
            {
                "code": "ASSIGNEE_PROJECT_MEMBERSHIP_REQUIRED",
                "project_id": project_id,
                "owner_user_id": owner_user_id,
            },
        )
    if row[2] != "ACTIVE":
        raise HTTPException(
            422,
            {
                "code": "ASSIGNEE_ACCOUNT_NOT_ACTIVE",
                "project_id": project_id,
                "owner_user_id": owner_user_id,
            },
        )
    return ProjectAssignee(user_id=str(row[0]), display_name=str(row[1]))


def permissions_for(principal: PrincipalLike, role: ProjectRole | None) -> frozenset[Permission]:
    if principal.account_status != "ACTIVE":
        return frozenset()
    if principal.is_global_admin:
        return GLOBAL_ADMIN_PERMISSIONS
    if role is None:
        return COMPANY_PERMISSIONS
    return ROLE_PERMISSIONS[role]


def has_permission(
    request: Request,
    permission: Permission,
    project_id: str | None = None,
    *,
    conn: ConnectionLike,
) -> bool:
    principal = cast(PrincipalLike, request.state.principal)
    effective_principal = _fresh_principal(conn, principal)
    role = project_role(conn, effective_principal, project_id) if project_id is not None else None
    return permission in permissions_for(effective_principal, role)


def project_role(conn: ConnectionLike, principal: PrincipalLike, project_id: str) -> ProjectRole | None:
    if principal.is_global_admin:
        return "admin"
    row = conn.execute(
        "SELECT role FROM project_memberships WHERE project_id=? AND user_id=?" + _locking_suffix(),
        [project_id, principal.user_id],
    ).fetchone()
    if not row or row[0] not in ROLE_PERMISSIONS:
        return None
    return cast(ProjectRole, row[0])


def _permission_detail(code: str, permission: Permission, project_id: str | None) -> dict[str, Any]:
    return {
        "code": code,
        "message": "이 작업을 수행할 권한이 없습니다.",
        "required_permission": permission,
        "project_id": project_id,
    }


def require_permission(
    request: Request,
    permission: Permission,
    project_id: str | None = None,
    *,
    conn: ConnectionLike | None = None,
) -> AccessContext:
    principal = cast(PrincipalLike, request.state.principal)

    def check(connection: ConnectionLike) -> AccessContext:
        effective_principal = _fresh_principal(connection, principal)
        role = project_role(connection, effective_principal, project_id) if project_id is not None else None
        allowed = permissions_for(effective_principal, role)
        if permission not in allowed:
            code = "PROJECT_MEMBERSHIP_REQUIRED" if project_id is not None and role is None else "PERMISSION_DENIED"
            detail = _permission_detail(code, permission, project_id)
            request.state.authorization_detail = detail
            raise HTTPException(403, detail)
        return AccessContext(effective_principal, permission, project_id, role)

    if conn is not None:
        return check(conn)
    with connect() as connection:
        return check(connection)


RESOURCE_PROJECT_QUERIES: Final[dict[str, str]] = {
    "request": "SELECT project_id FROM analysis_requests WHERE id=?",
    "load_case": (
        "SELECT requests.project_id FROM load_cases "
        "JOIN analysis_requests requests ON requests.id=load_cases.request_id WHERE load_cases.id=?"
    ),
    "run": (
        "SELECT requests.project_id FROM analysis_runs "
        "JOIN load_cases ON load_cases.id=analysis_runs.load_case_id "
        "JOIN analysis_requests requests ON requests.id=load_cases.request_id WHERE analysis_runs.id=?"
    ),
    "review_item": (
        "SELECT requests.project_id FROM review_annotations "
        "JOIN analysis_runs ON analysis_runs.id=review_annotations.analysis_run_id "
        "JOIN load_cases ON load_cases.id=analysis_runs.load_case_id "
        "JOIN analysis_requests requests ON requests.id=load_cases.request_id WHERE review_annotations.id=?"
    ),
    "workflow_step": (
        "SELECT requests.project_id FROM request_steps "
        "JOIN analysis_requests requests ON requests.id=request_steps.request_id WHERE request_steps.id=?"
    ),
    "work_item": (
        "SELECT requests.project_id FROM request_work_items "
        "JOIN analysis_requests requests ON requests.id=request_work_items.request_id WHERE request_work_items.id=?"
    ),
    "dashboard": "SELECT project_id FROM dashboards WHERE id=?",
    "media_asset": (
        "SELECT requests.project_id FROM media_assets "
        "JOIN analysis_runs ON analysis_runs.id=media_assets.analysis_run_id "
        "JOIN load_cases ON load_cases.id=analysis_runs.load_case_id "
        "JOIN analysis_requests requests ON requests.id=load_cases.request_id "
        "WHERE media_assets.id=?"
    ),
    "drop_video": (
        "SELECT requests.project_id FROM drop_video_assets "
        "JOIN load_cases ON load_cases.id=drop_video_assets.load_case_id "
        "JOIN analysis_requests requests ON requests.id=load_cases.request_id "
        "WHERE drop_video_assets.video_id=?"
    ),
}


def resolve_project_id(conn: ConnectionLike, resource_kind: str, resource_id: str) -> str:
    query = RESOURCE_PROJECT_QUERIES.get(resource_kind)
    if query is None:
        raise ValueError(f"Unknown project-scoped resource kind: {resource_kind}")
    row = conn.execute(query, [resource_id]).fetchone()
    if not row:
        raise HTTPException(404, "요청한 리소스를 찾을 수 없습니다.")
    return str(row[0])


def require_resource_permission(
    request: Request,
    permission: Permission,
    resource_kind: str,
    resource_id: str,
    *,
    conn: ConnectionLike | None = None,
) -> AccessContext:
    if conn is None:
        with connect() as connection:
            return require_resource_permission(
                request,
                permission,
                resource_kind,
                resource_id,
                conn=connection,
            )
    return require_permission(
        request,
        permission,
        resolve_project_id(conn, resource_kind, resource_id),
        conn=conn,
    )


def require_any_project_permission(
    request: Request,
    permission: Permission,
    *,
    conn: ConnectionLike | None = None,
) -> AccessContext:
    principal = cast(PrincipalLike, request.state.principal)

    def check(connection: ConnectionLike) -> AccessContext:
        effective_principal = _fresh_principal(connection, principal)
        if effective_principal.is_global_admin:
            return AccessContext(effective_principal, permission, None, "admin")
        memberships = connection.execute(
            "SELECT project_id, role FROM project_memberships WHERE user_id=?" + _locking_suffix(),
            [principal.user_id],
        ).fetchall()
        for project_id, role in memberships:
            if role in ROLE_PERMISSIONS and permission in permissions_for(effective_principal, cast(ProjectRole, role)):
                return AccessContext(effective_principal, permission, str(project_id), cast(ProjectRole, role))
        detail = _permission_detail("PROJECT_MEMBERSHIP_REQUIRED", permission, None)
        request.state.authorization_detail = detail
        raise HTTPException(403, detail)

    if conn is not None:
        return check(conn)
    with connect() as connection:
        return check(connection)


def require_assigned_work_item(
    request: Request,
    work_item_id: str,
    *,
    conn: ConnectionLike | None = None,
) -> AccessContext:
    principal = cast(PrincipalLike, request.state.principal)

    def check(connection: ConnectionLike) -> AccessContext:
        row = connection.execute(
            """
            SELECT requests.project_id, items.owner_user_id
            FROM request_work_items items
            JOIN analysis_requests requests ON requests.id=items.request_id
            WHERE items.id=?
            """ + _locking_suffix(),
            [work_item_id],
        ).fetchone()
        if not row:
            raise HTTPException(404, "작업 항목을 찾을 수 없습니다.")
        project_id, owner_user_id = str(row[0]), row[1]
        context = require_permission(request, WORK_EXECUTE_ASSIGNED, project_id, conn=connection)
        if owner_user_id is None:
            raise HTTPException(
                409,
                {
                    "code": "OWNER_REASSIGNMENT_REQUIRED",
                    "message": "담당자를 사용자 계정으로 다시 지정해야 실행할 수 있습니다.",
                    "project_id": project_id,
                },
            )
        if owner_user_id != principal.user_id and WORK_EXECUTE_ANY not in permissions_for(context.principal, context.project_role):
            request.state.authorization_detail = {
                "required_permission": WORK_EXECUTE_ASSIGNED,
                "project_id": project_id,
            }
            raise HTTPException(
                403,
                {
                    "code": "WORK_ITEM_NOT_ASSIGNED",
                    "message": "본인에게 배정된 작업만 실행할 수 있습니다.",
                    "required_permission": WORK_EXECUTE_ASSIGNED,
                    "project_id": project_id,
                },
            )
        if owner_user_id != principal.user_id:
            request.state.work_execution_override = {
                "work_item_id": work_item_id,
                "project_id": project_id,
                "owner_user_id": owner_user_id,
                "permission": WORK_EXECUTE_ANY,
            }
            return AccessContext(principal, WORK_EXECUTE_ANY, project_id, context.project_role)
        return context

    if conn is not None:
        return check(conn)
    with connect() as connection:
        return check(connection)
