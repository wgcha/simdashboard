"""Stable access-control facade used by business domains.

Business modules import authorization contracts from here.  Provider-specific
OIDC and employee-directory implementations remain behind the authentication
and access-control routers, so an intranet adapter can be replaced without
changing request, workflow, result, or dashboard domain code.
"""

from ...access_policy import (
    AUDIT_VIEW,
    COMPANY_PERMISSIONS,
    DASHBOARD_EDIT,
    PROJECT_DATA_VIEW,
    PROJECT_INVITATION_CREATE,
    PROJECT_LAYOUT_EDIT,
    PROJECT_MEMBER_MANAGE,
    PROJECT_THRESHOLD_MANAGE,
    PROJECT_VARIABLE_MANAGE,
    ROLE_PERMISSIONS,
    REPORT_EXPORT,
    REQUEST_CREATE,
    REQUEST_EDIT,
    RESULT_IMPORT,
    RESULT_REVIEW,
    SYSTEM_CATALOG_MANAGE,
    SYSTEM_MENU_POLICY_MANAGE,
    SYSTEM_USER_APPROVE,
    WORKFLOW_EDIT,
    AccountStatus,
    has_permission,
    require_any_project_permission,
    require_assigned_work_item,
    require_permission,
    require_resource_permission,
    resolve_project_assignee,
)

__all__ = [
    "AUDIT_VIEW",
    "COMPANY_PERMISSIONS",
    "DASHBOARD_EDIT",
    "PROJECT_DATA_VIEW",
    "PROJECT_INVITATION_CREATE",
    "PROJECT_LAYOUT_EDIT",
    "PROJECT_MEMBER_MANAGE",
    "PROJECT_THRESHOLD_MANAGE",
    "PROJECT_VARIABLE_MANAGE",
    "ROLE_PERMISSIONS",
    "REPORT_EXPORT",
    "REQUEST_CREATE",
    "REQUEST_EDIT",
    "RESULT_IMPORT",
    "RESULT_REVIEW",
    "SYSTEM_CATALOG_MANAGE",
    "SYSTEM_MENU_POLICY_MANAGE",
    "SYSTEM_USER_APPROVE",
    "WORKFLOW_EDIT",
    "AccountStatus",
    "has_permission",
    "require_any_project_permission",
    "require_assigned_work_item",
    "require_permission",
    "require_resource_permission",
    "resolve_project_assignee",
]
