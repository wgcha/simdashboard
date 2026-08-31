from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any

from ...domains.workspace_layouts.errors import (
    WorkspaceLayoutNotFoundError,
    WorkspaceLayoutProjectNotFoundError,
)
from ...domains.workspace_layouts.models import WorkspaceLayout, WorkspaceLayoutAuditContext
from ...domains.workspace_layouts.policies import _validated_workspace_layout
from ...domains.workspace_layouts.ports import WorkspaceLayoutRepository


Clock = Callable[[], datetime]
WorkspaceLayoutRepositoryProvider = Callable[[], AbstractContextManager[WorkspaceLayoutRepository]]


def save_project_workspace_layout(
    *,
    project_id: str,
    layout_kind: str,
    definition: dict[str, Any],
    actor_name: str,
    audit: WorkspaceLayoutAuditContext,
    repository_provider: WorkspaceLayoutRepositoryProvider,
    clock: Clock | None = None,
) -> WorkspaceLayout:
    normalized_definition = _validated_workspace_layout(layout_kind, definition)
    now = (clock or utc_now)()
    with repository_provider() as repository:
        with repository.transaction():
            if not repository.project_exists(project_id):
                raise WorkspaceLayoutProjectNotFoundError()
            repository.authorize_write(project_id)
            existing_version = repository.get_layout_version(project_id, layout_kind)
            if existing_version is None:
                raise WorkspaceLayoutNotFoundError()
            version = existing_version + 1
            repository.update_layout(
                project_id,
                layout_kind,
                version,
                normalized_definition,
                actor_name,
                now,
            )
            repository.insert_version(
                project_id,
                layout_kind,
                version,
                normalized_definition,
                actor_name,
                now,
            )
            repository.add_audit(
                {
                    **audit,
                    "action": "PROJECT_WORKSPACE_LAYOUT_UPDATED",
                    "status_code": 200,
                    "detail": {"project_id": project_id, "layout_kind": layout_kind, "version": version},
                }
            )
    return {
        "project_id": project_id,
        "layout_kind": layout_kind,
        "version": version,
        "definition": normalized_definition,
        "updated_by": actor_name,
        "updated_at": now,
    }


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
