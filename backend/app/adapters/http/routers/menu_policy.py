from __future__ import annotations

from typing import NoReturn
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request

from ....adapters.persistence.menu_policy import (
    SQLMenuPolicyReaderProvider,
    SQLMenuPolicyUnitOfWorkProvider,
)
from ....application.menu_policy.policies import (
    get_menu_policy as get_menu_policy_query,
    get_menu_policy_version as get_menu_policy_version_query,
    list_menu_policy_versions,
    restore_menu_policy as restore_menu_policy_command,
    update_menu_policy as update_menu_policy_command,
)
from ....database_connection import ConnectionLike
from ....domains.menu_policy.models import (
    MenuPermissionMismatchError,
    MenuPolicyAuditContext,
    MenuPolicyError,
    MenuPolicyIncompleteError,
    MenuPolicyLockedError,
    MenuPolicyUnavailableError,
    MenuPolicyVersionNotFoundError,
    StaleMenuPolicyVersionError,
    UnknownMenuIdError,
    UnknownMenuPolicyRoleError,
)
from ....modules.access_control import ROLE_PERMISSIONS, SYSTEM_MENU_POLICY_MANAGE, require_permission
from ....schemas.access_control import (
    MenuPolicyResponse,
    MenuPolicyUpdate,
    MenuPolicyVersionResponse,
    MenuPolicyVersionSummary,
)


router = APIRouter()


def _authorize(request: Request):
    def authorize(connection: ConnectionLike) -> None:
        require_permission(request, SYSTEM_MENU_POLICY_MANAGE, conn=connection)

    return authorize


def _audit(request: Request) -> MenuPolicyAuditContext:
    principal = request.state.principal
    return {
        "user_id": principal.user_id,
        "username": principal.username,
        "role": principal.role,
        "method": request.method,
        "path": request.url.path,
        "request_id": getattr(request.state, "request_id", str(uuid4())),
        "client_ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent", "")[:500],
    }


def _parse_historical_visibility(
    expected_version: int,
    source_version: int,
    visibility: object,
) -> dict[str, dict[str, bool]]:
    return MenuPolicyUpdate(
        expected_version=expected_version,
        change_note=f"Restore version {source_version}",
        visibility=visibility,
    ).visibility


def _raise_mapped(error: MenuPolicyError) -> NoReturn:
    if isinstance(error, MenuPolicyUnavailableError):
        raise HTTPException(503, {"code": error.code}) from error
    if isinstance(error, MenuPolicyIncompleteError):
        raise HTTPException(503, {"code": error.code}) from error
    if isinstance(error, UnknownMenuPolicyRoleError):
        raise HTTPException(422, {"code": error.code, "roles": error.roles}) from error
    if isinstance(error, UnknownMenuIdError):
        raise HTTPException(422, {"code": error.code, "menu_ids": error.menu_ids}) from error
    if isinstance(error, MenuPolicyLockedError):
        raise HTTPException(409, {"code": error.code, "menu_id": error.menu_id}) from error
    if isinstance(error, MenuPermissionMismatchError):
        raise HTTPException(
            422,
            {
                "code": error.code,
                "role": error.role,
                "menu_id": error.menu_id,
                "required_permission": error.required_permission,
            },
        ) from error
    if isinstance(error, StaleMenuPolicyVersionError):
        raise HTTPException(
            409,
            {"code": error.code, "current_version": error.current_version},
        ) from error
    if isinstance(error, MenuPolicyVersionNotFoundError):
        raise HTTPException(404, error.message) from error
    raise error


@router.get(
    "/api/navigation/menu-policy",
    response_model=MenuPolicyResponse,
    operation_id="get_menu_policy_api_navigation_menu_policy_get",
)
def get_menu_policy(request: Request) -> dict[str, object]:
    # SecurityMiddleware already enforces ACTIVE for this endpoint.
    del request
    try:
        return get_menu_policy_query(SQLMenuPolicyReaderProvider(lambda _connection: None))
    except MenuPolicyError as error:
        _raise_mapped(error)


@router.put(
    "/api/admin/menu-policy",
    response_model=MenuPolicyResponse,
    operation_id="update_menu_policy_api_admin_menu_policy_put",
)
def update_menu_policy(payload: MenuPolicyUpdate, request: Request) -> dict[str, object]:
    try:
        return update_menu_policy_command(
            expected_version=payload.expected_version,
            change_note=payload.change_note,
            visibility=payload.visibility,
            role_permissions=ROLE_PERMISSIONS,
            actor_id=request.state.principal.user_id,
            audit=_audit(request),
            unit_of_work_provider=SQLMenuPolicyUnitOfWorkProvider(_authorize(request)),
        )
    except MenuPolicyError as error:
        _raise_mapped(error)


@router.get(
    "/api/admin/menu-policy/versions",
    response_model=list[MenuPolicyVersionSummary],
    operation_id="menu_policy_versions_api_admin_menu_policy_versions_get",
)
def menu_policy_versions(request: Request) -> list[dict[str, object]]:
    require_permission(request, SYSTEM_MENU_POLICY_MANAGE)
    return list_menu_policy_versions(SQLMenuPolicyReaderProvider(lambda _connection: None))


@router.get(
    "/api/admin/menu-policy/versions/{version}",
    response_model=MenuPolicyVersionResponse,
    operation_id="get_menu_policy_version_api_admin_menu_policy_versions__version__get",
)
def get_menu_policy_version(version: int, request: Request) -> dict[str, object]:
    require_permission(request, SYSTEM_MENU_POLICY_MANAGE)
    try:
        return get_menu_policy_version_query(
            version,
            SQLMenuPolicyReaderProvider(lambda _connection: None),
        )
    except MenuPolicyError as error:
        _raise_mapped(error)


@router.post(
    "/api/admin/menu-policy/versions/{version}/restore",
    response_model=MenuPolicyResponse,
    operation_id="restore_menu_policy_api_admin_menu_policy_versions__version__restore_post",
)
def restore_menu_policy(version: int, request: Request) -> dict[str, object]:
    try:
        return restore_menu_policy_command(
            version=version,
            role_permissions=ROLE_PERMISSIONS,
            actor_id=request.state.principal.user_id,
            audit=_audit(request),
            unit_of_work_provider=SQLMenuPolicyUnitOfWorkProvider(_authorize(request)),
            historical_visibility_parser=_parse_historical_visibility,
        )
    except MenuPolicyError as error:
        _raise_mapped(error)
