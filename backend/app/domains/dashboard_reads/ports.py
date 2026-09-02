from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, Protocol

ProjectAuthorizer = Callable[[str, Any], None]
ProjectPermissionCheck = Callable[[str, Any], bool]
ResourceAuthorizer = Callable[[str, Any], None]


class DashboardReadRepository(Protocol):
    def get_dashboard(self, dashboard_id: str, authorize_project: ProjectAuthorizer) -> dict[str, Any]: ...

    def list_dashboards(
        self,
        project_id: str | None,
        has_project_permission: ProjectPermissionCheck,
    ) -> list[dict[str, Any]]: ...

    def get_dashboard_versions(
        self,
        dashboard_id: str,
        include_invalid: bool,
        authorize_resource: ResourceAuthorizer,
    ) -> list[dict[str, Any]]: ...

    def get_dashboard_version(
        self,
        dashboard_id: str,
        version: int,
        include_invalid: bool,
        authorize_resource: ResourceAuthorizer,
    ) -> dict[str, Any]: ...


class DashboardReadRepositoryProvider(Protocol):
    def __call__(self) -> AbstractContextManager[DashboardReadRepository]: ...
