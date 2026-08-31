from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request

from ..access_policy import (
    ROLE_PERMISSIONS,
    SYSTEM_MENU_POLICY_MANAGE,
    SYSTEM_USER_APPROVE,
    ProjectRole,
    require_permission,
)
from ..config import database_settings
from ..database import json_value
from ..database_connection import ConnectionLike, connect, rows
from ..schemas.access_control import (
    AccountStatusUpdate,
    GlobalAdminUpdate,
    MenuPolicyResponse,
    MenuPolicyUpdate,
    MenuPolicyVersionResponse,
    MenuPolicyVersionSummary,
)
from ..security import Principal, write_audit_event
from ..adapters.http.routers.project_assignees import router as project_assignees_router
from ..adapters.http.routers.project_memberships import router as project_memberships_router
from ..adapters.http.routers.project_invitations import router as project_invitations_router


router = APIRouter(tags=["access-control"])


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _principal(request: Request) -> Principal:
    return cast(Principal, request.state.principal)


def _begin(conn: ConnectionLike, *tables: str) -> None:
    conn.execute("BEGIN TRANSACTION")
    if database_settings().backend == "postgresql" and tables:
        safe_tables = {"users", "project_memberships", "project_invitations", "menu_policy_state", "role_menu_policies"}
        if any(table not in safe_tables for table in tables):
            raise RuntimeError("Unexpected table lock requested")
        conn.execute(f"LOCK TABLE {', '.join(tables)} IN SHARE ROW EXCLUSIVE MODE")


def _rollback(conn: ConnectionLike) -> None:
    conn.execute("ROLLBACK")


def _commit(conn: ConnectionLike) -> None:
    conn.execute("COMMIT")


def _normalize_timestamp(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _assert_expected_timestamp(actual: datetime, expected: datetime) -> None:
    if _normalize_timestamp(actual) != _normalize_timestamp(expected):
        raise HTTPException(
            409,
            {"code": "STALE_USER_VERSION", "message": "사용자 정보가 다른 관리자에 의해 변경되었습니다."},
        )


def _user_item(conn: ConnectionLike, user_id: str) -> dict[str, Any]:
    result = rows(
        conn.execute(
            """
            SELECT id, username, display_name, employee_id, email, department, job_title,
                   account_status, is_global_admin, approved_by, approved_at, last_login_at,
                   created_at, updated_at
            FROM users WHERE id=?
            """,
            [user_id],
        )
    )
    if not result:
        raise HTTPException(404, "사용자를 찾을 수 없습니다.")
    return result[0]


@router.patch("/api/admin/users/{user_id}/status")
def update_account_status(user_id: str, payload: AccountStatusUpdate, request: Request) -> dict[str, Any]:
    principal = _principal(request)
    now = _now()
    with connect() as conn:
        _begin(conn, "users", "project_invitations")
        try:
            require_permission(request, SYSTEM_USER_APPROVE, conn=conn)
            current = conn.execute(
                "SELECT account_status, is_global_admin, employee_id, updated_at FROM users WHERE id=?",
                [user_id],
            ).fetchone()
            if not current:
                raise HTTPException(404, "사용자를 찾을 수 없습니다.")
            _assert_expected_timestamp(current[3], payload.expected_updated_at)
            if current[1] and current[0] == "ACTIVE" and payload.account_status != "ACTIVE":
                active_admin_count = conn.execute(
                    "SELECT count(*) FROM users WHERE is_global_admin=true AND account_status='ACTIVE'"
                ).fetchone()[0]
                if active_admin_count <= 1:
                    raise HTTPException(
                        409,
                        {"code": "LAST_GLOBAL_ADMIN_PROTECTED", "message": "마지막 전역 관리자는 중지할 수 없습니다."},
                    )
            approved_by = principal.user_id if payload.account_status == "ACTIVE" else None
            approved_at = now if payload.account_status == "ACTIVE" else None
            conn.execute(
                """
                UPDATE users
                SET account_status=?, is_active=?, approved_by=COALESCE(?, approved_by),
                    approved_at=COALESCE(?, approved_at), updated_at=?
                WHERE id=?
                """,
                [payload.account_status, payload.account_status != "SUSPENDED", approved_by, approved_at, now, user_id],
            )
            if payload.account_status == "ACTIVE" and current[2]:
                conn.execute(
                    """
                    UPDATE project_invitations
                    SET status='READY', resolved_user_id=?, resolved_by=?, resolved_at=?
                    WHERE employee_id=? AND status IN ('PENDING_ACCOUNT', 'PENDING_APPROVAL')
                    """,
                    [user_id, principal.user_id, now, current[2]],
                )
            write_audit_event(
                request=request,
                principal=principal,
                status_code=200,
                action="ACCOUNT_STATUS_CHANGED",
                detail={"target_user_id": user_id, "old_status": current[0], "new_status": payload.account_status, "reason": payload.reason},
                connection=conn,
            )
            item = _user_item(conn, user_id)
            _commit(conn)
            return item
        except Exception:
            _rollback(conn)
            raise


@router.patch("/api/admin/users/{user_id}/global-admin")
def update_global_admin(user_id: str, payload: GlobalAdminUpdate, request: Request) -> dict[str, Any]:
    principal = _principal(request)
    now = _now()
    with connect() as conn:
        _begin(conn, "users")
        try:
            require_permission(request, SYSTEM_USER_APPROVE, conn=conn)
            current = conn.execute(
                "SELECT is_global_admin, account_status, updated_at FROM users WHERE id=?",
                [user_id],
            ).fetchone()
            if not current:
                raise HTTPException(404, "사용자를 찾을 수 없습니다.")
            _assert_expected_timestamp(current[2], payload.expected_updated_at)
            if payload.is_global_admin and current[1] != "ACTIVE":
                raise HTTPException(422, {"code": "GLOBAL_ADMIN_MUST_BE_ACTIVE"})
            if current[0] and not payload.is_global_admin:
                active_admin_count = conn.execute(
                    "SELECT count(*) FROM users WHERE is_global_admin=true AND account_status='ACTIVE'"
                ).fetchone()[0]
                if current[1] == "ACTIVE" and active_admin_count <= 1:
                    raise HTTPException(
                        409,
                        {"code": "LAST_GLOBAL_ADMIN_PROTECTED", "message": "마지막 전역 관리자 권한은 해제할 수 없습니다."},
                    )
            conn.execute(
                "UPDATE users SET is_global_admin=?, updated_at=? WHERE id=?",
                [payload.is_global_admin, now, user_id],
            )
            write_audit_event(
                request=request,
                principal=principal,
                status_code=200,
                action="GLOBAL_ADMIN_CHANGED",
                detail={"target_user_id": user_id, "old_value": bool(current[0]), "new_value": payload.is_global_admin, "reason": payload.reason},
                connection=conn,
            )
            item = _user_item(conn, user_id)
            _commit(conn)
            return item
        except Exception:
            _rollback(conn)
            raise


def _project_exists(conn: ConnectionLike, project_id: str) -> None:
    if not conn.execute("SELECT 1 FROM projects WHERE id=?", [project_id]).fetchone():
        raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")

router.include_router(project_memberships_router)
router.include_router(project_invitations_router)
router.include_router(project_assignees_router)


def _menu_policy(conn: ConnectionLike) -> dict[str, Any]:
    state = conn.execute(
        "SELECT version, updated_by, updated_at FROM menu_policy_state WHERE id='global'"
    ).fetchone()
    if not state:
        raise HTTPException(503, {"code": "MENU_POLICY_UNAVAILABLE"})
    stored = rows(
        conn.execute(
            """
            SELECT definitions.id, definitions.label, definitions.required_permission,
                   definitions.context_kind, definitions.sequence_no, definitions.is_policy_editable,
                   policies.role, policies.is_visible
            FROM menu_definitions definitions
            JOIN role_menu_policies policies ON policies.menu_id=definitions.id
            WHERE definitions.is_active=true
            ORDER BY definitions.sequence_no, definitions.id, policies.role
            """
        )
    )
    items: dict[str, dict[str, Any]] = {}
    for row in stored:
        item = items.setdefault(
            row["id"],
            {
                "id": row["id"],
                "label": row["label"],
                "required_permission": row["required_permission"],
                "context_kind": row["context_kind"],
                "sequence_no": row["sequence_no"],
                "is_policy_editable": bool(row["is_policy_editable"]),
                "visibility": {},
            },
        )
        item["visibility"][row["role"]] = bool(row["is_visible"])
    if any(set(item["visibility"]) != {"general", "power", "admin"} for item in items.values()):
        raise HTTPException(503, {"code": "MENU_POLICY_INCOMPLETE"})
    return {
        "version": int(state[0]),
        "updated_by": state[1],
        "updated_at": state[2],
        "menus": list(items.values()),
    }


@router.get("/api/navigation/menu-policy", response_model=MenuPolicyResponse)
def get_menu_policy(request: Request) -> dict[str, Any]:
    # SecurityMiddleware already enforces ACTIVE for this endpoint.
    del request
    with connect() as conn:
        return _menu_policy(conn)


def _current_visibility(policy: dict[str, Any]) -> dict[ProjectRole, dict[str, bool]]:
    result: dict[ProjectRole, dict[str, bool]] = {"general": {}, "power": {}, "admin": {}}
    for menu in policy["menus"]:
        for role in result:
            result[role][menu["id"]] = bool(menu["visibility"][role])
    return result


def _normalized_visibility(policy: dict[str, Any], update: MenuPolicyUpdate) -> tuple[dict[ProjectRole, dict[str, bool]], list[str]]:
    current = _current_visibility(policy)
    menus = {menu["id"]: menu for menu in policy["menus"]}
    changed: set[str] = set()
    unknown_roles = set(update.visibility) - {"general", "power", "admin"}
    if unknown_roles:
        raise HTTPException(422, {"code": "UNKNOWN_MENU_POLICY_ROLE", "roles": sorted(unknown_roles)})
    for role, menu_updates in update.visibility.items():
        unknown_menus = set(menu_updates) - set(menus)
        if unknown_menus:
            raise HTTPException(422, {"code": "UNKNOWN_MENU_ID", "menu_ids": sorted(unknown_menus)})
        for menu_id, is_visible in menu_updates.items():
            menu = menus[menu_id]
            if not menu["is_policy_editable"] and is_visible != current[role][menu_id]:
                raise HTTPException(409, {"code": "MENU_POLICY_LOCKED", "menu_id": menu_id})
            if is_visible and menu["required_permission"] not in ROLE_PERMISSIONS[role]:
                raise HTTPException(
                    422,
                    {
                        "code": "MENU_PERMISSION_MISMATCH",
                        "role": role,
                        "menu_id": menu_id,
                        "required_permission": menu["required_permission"],
                    },
                )
            if current[role][menu_id] != is_visible:
                changed.add(menu_id)
            current[role][menu_id] = is_visible
    return current, sorted(changed)


def _write_menu_snapshot(
    conn: ConnectionLike,
    *,
    version: int,
    visibility: dict[ProjectRole, dict[str, bool]],
    actor_id: str,
    now: datetime,
    source_version: int | None,
    change_note: str,
) -> None:
    for role, role_visibility in visibility.items():
        for menu_id, is_visible in role_visibility.items():
            conn.execute(
                """
                UPDATE role_menu_policies
                SET is_visible=?, policy_version=?, updated_by=?, updated_at=?
                WHERE role=? AND menu_id=?
                """,
                [is_visible, version, actor_id, now, role, menu_id],
            )
    conn.execute(
        "UPDATE menu_policy_state SET version=?, updated_by=?, updated_at=? WHERE id='global'",
        [version, actor_id, now],
    )
    conn.execute(
        """
        INSERT INTO menu_policy_versions
            (version, definition_json, created_by, created_at, source_version, change_note)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [version, json.dumps(visibility, ensure_ascii=False, sort_keys=True), actor_id, now, source_version, change_note],
    )


@router.put("/api/admin/menu-policy", response_model=MenuPolicyResponse)
def update_menu_policy(payload: MenuPolicyUpdate, request: Request) -> dict[str, Any]:
    principal = _principal(request)
    now = _now()
    with connect() as conn:
        _begin(conn, "menu_policy_state", "role_menu_policies")
        try:
            require_permission(request, SYSTEM_MENU_POLICY_MANAGE, conn=conn)
            policy = _menu_policy(conn)
            if policy["version"] != payload.expected_version:
                raise HTTPException(
                    409,
                    {"code": "STALE_POLICY_VERSION", "current_version": policy["version"]},
                )
            visibility, changed = _normalized_visibility(policy, payload)
            next_version = policy["version"] + 1
            _write_menu_snapshot(
                conn,
                version=next_version,
                visibility=visibility,
                actor_id=principal.user_id,
                now=now,
                source_version=None,
                change_note=payload.change_note.strip(),
            )
            write_audit_event(
                request=request,
                principal=principal,
                status_code=200,
                action="MENU_POLICY_UPDATED",
                detail={"policy_version": next_version, "changed_menu_ids": changed},
                connection=conn,
            )
            result = _menu_policy(conn)
            _commit(conn)
            return result
        except Exception:
            _rollback(conn)
            raise


@router.get("/api/admin/menu-policy/versions", response_model=list[MenuPolicyVersionSummary])
def menu_policy_versions(request: Request) -> list[dict[str, Any]]:
    require_permission(request, SYSTEM_MENU_POLICY_MANAGE)
    with connect() as conn:
        return rows(
            conn.execute(
                """
                SELECT version, created_by, created_at, source_version, change_note
                FROM menu_policy_versions ORDER BY version DESC
                """
            )
        )


def _menu_policy_version(conn: ConnectionLike, version: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT version, definition_json, created_by, created_at, source_version, change_note
        FROM menu_policy_versions WHERE version=?
        """,
        [version],
    ).fetchone()
    if not row:
        raise HTTPException(404, "메뉴 정책 버전을 찾을 수 없습니다.")
    return {
        "version": int(row[0]),
        "visibility": json_value(row[1]) or {},
        "created_by": row[2],
        "created_at": row[3],
        "source_version": row[4],
        "change_note": row[5],
    }


@router.get("/api/admin/menu-policy/versions/{version}", response_model=MenuPolicyVersionResponse)
def get_menu_policy_version(version: int, request: Request) -> dict[str, Any]:
    require_permission(request, SYSTEM_MENU_POLICY_MANAGE)
    with connect() as conn:
        return _menu_policy_version(conn, version)


@router.post("/api/admin/menu-policy/versions/{version}/restore", response_model=MenuPolicyResponse)
def restore_menu_policy(version: int, request: Request) -> dict[str, Any]:
    principal = _principal(request)
    now = _now()
    with connect() as conn:
        _begin(conn, "menu_policy_state", "role_menu_policies")
        try:
            require_permission(request, SYSTEM_MENU_POLICY_MANAGE, conn=conn)
            current = _menu_policy(conn)
            source = _menu_policy_version(conn, version)
            visibility = source["visibility"]
            # Historical snapshots are trusted only after rechecking current
            # definitions and role permissions through the regular validator.
            normalized, changed = _normalized_visibility(
                current,
                MenuPolicyUpdate(
                    expected_version=current["version"],
                    change_note=f"Restore version {version}",
                    visibility=visibility,
                ),
            )
            next_version = current["version"] + 1
            _write_menu_snapshot(
                conn,
                version=next_version,
                visibility=normalized,
                actor_id=principal.user_id,
                now=now,
                source_version=version,
                change_note=f"Restored from version {version}",
            )
            write_audit_event(
                request=request,
                principal=principal,
                status_code=200,
                action="MENU_POLICY_RESTORED",
                detail={"policy_version": next_version, "source_version": version, "changed_menu_ids": changed},
                connection=conn,
            )
            result = _menu_policy(conn)
            _commit(conn)
            return result
        except Exception:
            _rollback(conn)
            raise
