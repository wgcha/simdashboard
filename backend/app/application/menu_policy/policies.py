from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone

from ...domains.menu_policy.models import (
    MenuPermissionMismatchError,
    MenuPolicy,
    MenuPolicyAuditContext,
    MenuPolicyIncompleteError,
    MenuPolicyLockedError,
    MenuPolicyRole,
    MenuPolicyUnavailableError,
    MenuPolicyVersion,
    MenuPolicyVersionNotFoundError,
    MenuPolicyVersionSummary,
    MenuVisibility,
    StaleMenuPolicyVersionError,
    UnknownMenuIdError,
    UnknownMenuPolicyRoleError,
)
from ...domains.menu_policy.ports import (
    MenuPolicyReaderProvider,
    MenuPolicyUnitOfWorkProvider,
)


Clock = Callable[[], datetime]
RolePermissions = Mapping[MenuPolicyRole, frozenset[str]]
HistoricalVisibilityParser = Callable[[int, int, object], MenuVisibility]
_ROLES: frozenset[str] = frozenset({"general", "power", "admin"})


def get_menu_policy(reader_provider: MenuPolicyReaderProvider) -> MenuPolicy:
    with reader_provider() as reader:
        return _required_policy(reader.get_policy())


def list_menu_policy_versions(
    reader_provider: MenuPolicyReaderProvider,
) -> list[MenuPolicyVersionSummary]:
    with reader_provider() as reader:
        return reader.list_versions()


def get_menu_policy_version(
    version: int,
    reader_provider: MenuPolicyReaderProvider,
) -> MenuPolicyVersion:
    with reader_provider() as reader:
        return _required_version(reader.get_version(version))


def update_menu_policy(
    *,
    expected_version: int,
    change_note: str,
    visibility: MenuVisibility,
    role_permissions: RolePermissions,
    actor_id: str,
    audit: MenuPolicyAuditContext,
    unit_of_work_provider: MenuPolicyUnitOfWorkProvider,
    clock: Clock | None = None,
) -> MenuPolicy:
    now = (clock or utc_now)()
    with unit_of_work_provider() as unit_of_work:
        unit_of_work.authorize_manage()
        policy = _required_policy(unit_of_work.get_policy())
        if policy["version"] != expected_version:
            raise StaleMenuPolicyVersionError(policy["version"])
        normalized, changed = normalized_visibility(policy, visibility, role_permissions)
        next_version = policy["version"] + 1
        unit_of_work.write_snapshot(
            version=next_version,
            visibility=normalized,
            actor_id=actor_id,
            occurred_at=now,
            source_version=None,
            change_note=change_note.strip(),
        )
        unit_of_work.add_audit(
            {
                **audit,
                "action": "MENU_POLICY_UPDATED",
                "status_code": 200,
                "detail": {"policy_version": next_version, "changed_menu_ids": changed},
            }
        )
        return _required_policy(unit_of_work.get_policy())


def restore_menu_policy(
    *,
    version: int,
    role_permissions: RolePermissions,
    actor_id: str,
    audit: MenuPolicyAuditContext,
    unit_of_work_provider: MenuPolicyUnitOfWorkProvider,
    historical_visibility_parser: HistoricalVisibilityParser,
    clock: Clock | None = None,
) -> MenuPolicy:
    now = (clock or utc_now)()
    with unit_of_work_provider() as unit_of_work:
        unit_of_work.authorize_manage()
        current = _required_policy(unit_of_work.get_policy())
        source = _required_version(unit_of_work.get_version(version))
        visibility = historical_visibility_parser(current["version"], version, source["visibility"])
        normalized, changed = normalized_visibility(current, visibility, role_permissions)
        next_version = current["version"] + 1
        unit_of_work.write_snapshot(
            version=next_version,
            visibility=normalized,
            actor_id=actor_id,
            occurred_at=now,
            source_version=version,
            change_note=f"Restored from version {version}",
        )
        unit_of_work.add_audit(
            {
                **audit,
                "action": "MENU_POLICY_RESTORED",
                "status_code": 200,
                "detail": {
                    "policy_version": next_version,
                    "source_version": version,
                    "changed_menu_ids": changed,
                },
            }
        )
        return _required_policy(unit_of_work.get_policy())


def normalized_visibility(
    policy: MenuPolicy,
    update: MenuVisibility,
    role_permissions: RolePermissions,
) -> tuple[MenuVisibility, list[str]]:
    current = current_visibility(policy)
    menus = {menu["id"]: menu for menu in policy["menus"]}
    changed: set[str] = set()
    unknown_roles = set(update) - _ROLES
    if unknown_roles:
        raise UnknownMenuPolicyRoleError(sorted(unknown_roles))
    for role, menu_updates in update.items():
        unknown_menus = set(menu_updates) - set(menus)
        if unknown_menus:
            raise UnknownMenuIdError(sorted(unknown_menus))
        for menu_id, is_visible in menu_updates.items():
            menu = menus[menu_id]
            if not menu["is_policy_editable"] and is_visible != current[role][menu_id]:
                raise MenuPolicyLockedError(menu_id)
            if is_visible and menu["required_permission"] not in role_permissions[role]:
                raise MenuPermissionMismatchError(role, menu_id, menu["required_permission"])
            if current[role][menu_id] != is_visible:
                changed.add(menu_id)
            current[role][menu_id] = is_visible
    return current, sorted(changed)


def current_visibility(policy: MenuPolicy) -> MenuVisibility:
    result: MenuVisibility = {"general": {}, "power": {}, "admin": {}}
    for menu in policy["menus"]:
        for role in result:
            result[role][menu["id"]] = bool(menu["visibility"][role])
    return result


def _required_policy(policy: MenuPolicy | None) -> MenuPolicy:
    if policy is None:
        raise MenuPolicyUnavailableError()
    if any(set(menu["visibility"]) != _ROLES for menu in policy["menus"]):
        raise MenuPolicyIncompleteError()
    return policy


def _required_version(version: MenuPolicyVersion | None) -> MenuPolicyVersion:
    if version is None:
        raise MenuPolicyVersionNotFoundError()
    return version


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
