from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from uuid import uuid4

from ...domains.projects.models import CreateProjectCommand, CreatedProject, ProjectAuditContext
from ...domains.projects.ports import ProjectUnitOfWork


AuthorizationCheck = Callable[[], object]
IdFactory = Callable[[str, int], str]
Clock = Callable[[], datetime]
ProjectUnitOfWorkProvider = Callable[[], AbstractContextManager[ProjectUnitOfWork]]


def create_project(
    command: CreateProjectCommand,
    audit: ProjectAuditContext,
    authorize: AuthorizationCheck,
    id_factory: IdFactory,
    clock: Clock,
    unit_of_work_provider: ProjectUnitOfWorkProvider,
) -> CreatedProject:
    """Authorize and create a project plus all required defaults atomically."""
    authorize()
    project_id = id_factory("project", 12)
    created_at = clock()
    membership_id = id_factory("membership", 20)
    audit_id = id_factory("", 0)
    with unit_of_work_provider() as unit_of_work:
        unit_of_work.add_project(project_id, command, created_at)
        unit_of_work.add_product_information(project_id, command)
        unit_of_work.add_quality_thresholds(project_id, created_at)
        unit_of_work.add_workspace_layouts(project_id)
        unit_of_work.add_admin_membership(membership_id, project_id, command, created_at)
        unit_of_work.add_audit_event(
            {**audit, "id": audit_id, "occurred_at": clock(), "project_id": project_id}
        )
    return {
        "id": project_id,
        "name": command["name"],
        "product_name": command["product_name"],
        "description": command["description"],
        "manufacturer": command["manufacturer"],
        "display_size_inch": command["display_size_inch"],
        "created_at": created_at,
    }


def new_identifier(prefix: str, length: int) -> str:
    value = str(uuid4()) if not prefix else uuid4().hex[:length]
    return value if not prefix else f"{prefix}-{value}"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
