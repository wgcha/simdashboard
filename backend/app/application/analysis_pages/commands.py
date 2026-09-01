"""Framework-neutral commands for the analysis-page administration API."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from uuid import uuid4

from ...domains.analysis_pages.errors import (
    AnalysisPageLoadCaseMismatchError,
    AnalysisPageNameConflictError,
    AnalysisPageNameTooShortError,
    AnalysisPageNotFoundError,
    AnalysisPageNotManageableError,
    AnalysisPageOrderMismatchError,
    CustomAnalysisPageDeletionError,
    DuplicateAnalysisPageOrderError,
    EmptyPublishedAnalysisPageError,
    LoadCaseNotFoundError,
    SystemAnalysisPageLifecycleError,
)
from ...domains.analysis_pages.policies import SYSTEM_ANALYSIS_PAGE_IDS, analysis_page_meta
from ...domains.analysis_pages.ports import (
    AnalysisPageCommandRepository,
    AnalysisPageCommandRepositoryProvider,
)


AuthorizeProject = Callable[[str, object], None]
AuthorizeResource = Callable[[str, object], None]
IdentifierFactory = Callable[[], str]
Clock = Callable[[], datetime]
DeleteRecords = Callable[[AnalysisPageCommandRepository, str], None]
DefinitionValidator = Callable[[dict[str, object]], dict[str, object]]


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_identifier() -> str:
    return uuid4().hex[:12]


def _require_context(repository: AnalysisPageCommandRepository, load_case_id: str) -> tuple[object, ...]:
    context = repository.load_case_context(load_case_id)
    if context is None:
        raise LoadCaseNotFoundError()
    return context


def _validate_name(name: str) -> str:
    normalized = name.strip()
    if len(normalized) < 2:
        raise AnalysisPageNameTooShortError()
    return normalized


def create_analysis_page(
    *,
    load_case_id: str,
    name: str,
    description: str = "",
    authorize: AuthorizeProject,
    repository_provider: AnalysisPageCommandRepositoryProvider,
    validate_definition: DefinitionValidator,
    identifier_factory: IdentifierFactory | None = None,
    clock: Clock | None = None,
) -> dict[str, object]:
    """Create a custom page, retaining legacy preflight and write order."""
    name = _validate_name(name)
    dashboard_id = f"dashboard-{(identifier_factory or new_identifier)()}"
    now = (clock or utc_now)()
    with repository_provider() as repository:
        project_id, request_id = _require_context(repository, load_case_id)
        repository.authorize(lambda connection: authorize(str(project_id), connection))
        if repository.page_name_exists(load_case_id, name):
            raise AnalysisPageNameConflictError()
        display_order = repository.next_custom_display_order(load_case_id)
        definition = {
            "id": dashboard_id,
            "name": name,
            "description": description.strip(),
            "widgets": [],
            "page": {
                "kind": "analysis_page",
                "analysis_key": "custom",
                "status": "draft",
                "display_order": display_order,
                "is_system": False,
            },
        }
        validated = validate_definition(definition)
        repository.insert_analysis_page(
            dashboard_id,
            str(project_id),
            str(request_id),
            load_case_id,
            validated,
            now,
        )
    return validated


def update_analysis_page(
    *,
    dashboard_id: str,
    name: str | None = None,
    description: str | None = None,
    status: str | None = None,
    authorize: AuthorizeResource,
    repository_provider: AnalysisPageCommandRepositoryProvider,
    validate_definition: DefinitionValidator,
) -> dict[str, object]:
    """Update lifecycle metadata after resource authorization on the same connection."""
    with repository_provider() as repository:
        repository.authorize_resource(lambda connection: authorize(dashboard_id, connection))
        item = repository.get_analysis_page(dashboard_id)
        if item is None:
            raise AnalysisPageNotFoundError()
        definition = item["definition"]
        page = analysis_page_meta(definition)
        if not page:
            raise AnalysisPageNotManageableError()
        if dashboard_id in SYSTEM_ANALYSIS_PAGE_IDS or page.get("is_system") is True:
            raise SystemAnalysisPageLifecycleError()

        name = _validate_name(name if name is not None else definition["name"])
        target_status = status or page["status"]
        if target_status == "published" and not definition.get("widgets"):
            raise EmptyPublishedAnalysisPageError()
        if target_status != "archived" and repository.page_name_exists(item["load_case_id"], name, dashboard_id):
            raise AnalysisPageNameConflictError()

        definition["name"] = name
        if description is not None:
            definition["description"] = description.strip()
        page["status"] = target_status
        definition["page"] = page
        validated = validate_definition(definition)
        repository.write_dashboard_definition(dashboard_id, validated, "관리자")
    return validated


def delete_analysis_page(
    *,
    dashboard_id: str,
    load_case_id: str,
    authorize: AuthorizeResource,
    repository_provider: AnalysisPageCommandRepositoryProvider,
    delete_records: DeleteRecords | None = None,
) -> dict[str, str]:
    """Permanently delete a custom page with the legacy rollback boundary."""
    with repository_provider() as repository:
        repository.authorize_resource(lambda connection: authorize(dashboard_id, connection))
        stored = repository.get_analysis_page_for_delete(dashboard_id)
        if stored is None:
            raise AnalysisPageNotFoundError()
        stored_load_case_id, definition = stored
        page = analysis_page_meta(definition)
        if (
            dashboard_id in SYSTEM_ANALYSIS_PAGE_IDS
            or not page
            or page.get("is_system") is not False
            or page.get("analysis_key") != "custom"
        ):
            raise CustomAnalysisPageDeletionError()
        if stored_load_case_id != load_case_id:
            raise AnalysisPageLoadCaseMismatchError()
        _require_context(repository, load_case_id)
        repository.begin_transaction()
        try:
            (delete_records or _delete_records)(repository, dashboard_id)
            repository.commit_transaction()
        except Exception:
            # Intentionally let rollback failures replace the body failure, as
            # the legacy conn.execute("ROLLBACK") path did.
            repository.rollback_transaction()
            raise
    return {"status": "deleted", "id": dashboard_id, "load_case_id": load_case_id}


def _delete_records(repository: AnalysisPageCommandRepository, dashboard_id: str) -> None:
    repository.delete_analysis_page_records(dashboard_id)


def reorder_analysis_pages(
    *,
    load_case_id: str,
    page_ids: list[str],
    authorize: AuthorizeProject,
    repository_provider: AnalysisPageCommandRepositoryProvider,
    validate_definition: DefinitionValidator,
) -> list[dict[str, object]]:
    """Reorder all active custom pages and list them from the same connection."""
    if len(page_ids) != len(set(page_ids)):
        raise DuplicateAnalysisPageOrderError()
    with repository_provider() as repository:
        project_id, _ = _require_context(repository, load_case_id)
        repository.authorize(lambda connection: authorize(str(project_id), connection))
        stored = repository.reorderable_analysis_pages(load_case_id)
        custom_pages = {item["id"]: item for item in stored}
        if set(page_ids) != set(custom_pages):
            raise AnalysisPageOrderMismatchError()
        for offset, dashboard_id in enumerate(page_ids):
            item = custom_pages[dashboard_id]
            item["definition"]["page"]["display_order"] = 100 + offset
            definition = validate_definition(item["definition"])
            repository.write_dashboard_definition(dashboard_id, definition, "관리자 순서 변경")
        _require_context(repository, load_case_id)
        return repository.list_analysis_pages(
            load_case_id,
            include_private=True,
            include_archived=False,
        )
