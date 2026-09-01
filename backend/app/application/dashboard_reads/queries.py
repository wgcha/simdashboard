from __future__ import annotations

from typing import Any

from ...domains.dashboard_reads.ports import (
    DashboardReadRepositoryProvider,
    ProjectAuthorizer,
    ProjectPermissionCheck,
    ResourceAuthorizer,
)


def get_dashboard(
    dashboard_id: str,
    repository_provider: DashboardReadRepositoryProvider,
    authorize_project: ProjectAuthorizer,
) -> dict[str, Any]:
    with repository_provider() as repository:
        return repository.get_dashboard(dashboard_id, authorize_project)


def list_dashboards(
    repository_provider: DashboardReadRepositoryProvider,
    project_id: str | None,
    has_project_permission: ProjectPermissionCheck,
) -> list[dict[str, Any]]:
    with repository_provider() as repository:
        return repository.list_dashboards(project_id, has_project_permission)


def get_dashboard_versions(
    dashboard_id: str,
    repository_provider: DashboardReadRepositoryProvider,
    include_invalid: bool,
    authorize_resource: ResourceAuthorizer,
) -> list[dict[str, Any]]:
    with repository_provider() as repository:
        return repository.get_dashboard_versions(dashboard_id, include_invalid, authorize_resource)


def get_dashboard_version(
    dashboard_id: str,
    version: int,
    repository_provider: DashboardReadRepositoryProvider,
    include_invalid: bool,
    authorize_resource: ResourceAuthorizer,
) -> dict[str, Any]:
    with repository_provider() as repository:
        return repository.get_dashboard_version(
            dashboard_id,
            version,
            include_invalid,
            authorize_resource,
        )
