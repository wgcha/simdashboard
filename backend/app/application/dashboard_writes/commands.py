from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from ...domains.dashboard_writes.errors import (
    AnalysisPageUserEditError,
    DashboardIdMismatchError,
    DashboardNotFoundError,
    DashboardVersionNotFoundError,
    LastDashboardHistoryError,
    LiveDashboardVersionError,
    PublishedEmptyRestoreError,
    RestorableVersionNotFoundError,
    SystemDashboardVersionError,
)
from ...domains.dashboard_writes.policies import SYSTEM_ANALYSIS_PAGE_IDS, analysis_page_meta
from ...domains.dashboard_writes.ports import DashboardWriteRepository, DashboardWriteRepositoryProvider


AuthorizeResource = Callable[[str, object], None]
DefinitionValidator = Callable[[dict[str, object]], dict[str, object]]
IdentifierFactory = Callable[[], str]
Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_identifier() -> str:
    return uuid4().hex[:12]


def save_dashboard(
    *,
    dashboard_id: str,
    definition: dict[str, object],
    authorize: AuthorizeResource,
    repository_provider: DashboardWriteRepositoryProvider,
) -> dict[str, object]:
    if dashboard_id != definition.get("id"):
        raise DashboardIdMismatchError()
    with repository_provider() as repository:
        repository.authorize_resource(lambda connection: authorize(dashboard_id, connection))
        existing = repository.get_dashboard(dashboard_id)
        if not existing:
            raise DashboardNotFoundError()
        stored_definition = existing[1]
        stored_page = analysis_page_meta(stored_definition)
        if stored_page:
            if (
                definition.get("name") != stored_definition.get("name")
                or definition.get("description", "") != stored_definition.get("description", "")
                or definition.get("page") != stored_page
            ):
                raise AnalysisPageUserEditError()
        version, now = repository.write_dashboard_definition(dashboard_id, definition, "대시보드 사용자")
    return {"status": "saved", "version": version, "updated_at": now}


def delete_dashboard_version(
    *,
    dashboard_id: str,
    version: int,
    authorize: AuthorizeResource,
    repository_provider: DashboardWriteRepositoryProvider,
) -> dict[str, object]:
    with repository_provider() as repository:
        repository.authorize_resource(lambda connection: authorize(dashboard_id, connection))
        current = repository.get_dashboard(dashboard_id)
        if not current:
            raise DashboardNotFoundError()
        current_version = int(current[0])
        definition = current[1]
        page = analysis_page_meta(definition)
        if version == 1 and (dashboard_id in SYSTEM_ANALYSIS_PAGE_IDS or (page and page.get("is_system") is True)):
            raise SystemDashboardVersionError()
        stored = repository.get_dashboard_version_for_delete(dashboard_id, version)
        if not stored or stored[0] is not True:
            raise DashboardVersionNotFoundError()
        if version == current_version:
            raise LiveDashboardVersionError()
        if repository.count_valid_history(dashboard_id, current_version) <= 1:
            raise LastDashboardHistoryError()
        repository.invalidate_dashboard_version(dashboard_id, version)
    return {"status": "invalidated", "dashboard_id": dashboard_id, "version": version}


def clone_dashboard(
    *,
    dashboard_id: str,
    name: str,
    description: str = "",
    principal_user_id: str,
    authorize: AuthorizeResource,
    repository_provider: DashboardWriteRepositoryProvider,
    identifier_factory: IdentifierFactory | None = None,
    clock: Clock | None = None,
) -> dict[str, object]:
    clone_id = f"dashboard-{(identifier_factory or new_identifier)()}"
    now = (clock or utc_now)()
    with repository_provider() as repository:
        repository.authorize_resource(lambda connection: authorize(dashboard_id, connection))
        source = repository.get_clone_source(dashboard_id)
        if not source:
            raise DashboardNotFoundError()
        definition = source[3]
        definition.pop("page", None)
        definition.update({"id": clone_id, "name": name.strip(), "description": description.strip()})
        repository.insert_cloned_dashboard(
            clone_id,
            source[0],
            source[1],
            source[2],
            definition,
            principal_user_id,
            now,
        )
    return {"id": clone_id, "version": 1, "status": "cloned"}


def restore_dashboard(
    *,
    dashboard_id: str,
    version: int,
    authorize: AuthorizeResource,
    repository_provider: DashboardWriteRepositoryProvider,
    validate_definition: DefinitionValidator,
) -> dict[str, object]:
    with repository_provider() as repository:
        repository.authorize_resource(lambda connection: authorize(dashboard_id, connection))
        stored = repository.get_valid_dashboard_version(dashboard_id, version)
        current = repository.get_dashboard(dashboard_id)
        if not stored or not current:
            raise RestorableVersionNotFoundError()
        definition = stored[0]
        current_definition = current[1]
        current_page = analysis_page_meta(current_definition)
        if current_page:
            if current_page.get("status") == "published" and not definition.get("widgets"):
                raise PublishedEmptyRestoreError()
            definition.update(
                {
                    "id": current_definition["id"],
                    "name": current_definition["name"],
                    "description": current_definition.get("description", ""),
                    "page": current_page,
                }
            )
        definition = validate_definition(definition)
        next_version, _ = repository.write_dashboard_definition(dashboard_id, definition, "복구 작업")
    return {"status": "restored", "version": next_version, "restored_from": version}
