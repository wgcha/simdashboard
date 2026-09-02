from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager

from ...domains.workspace_layouts.errors import (
    UnsupportedWorkspaceLayoutKindError,
    WorkspaceLayoutNotFoundError,
    WorkspaceLayoutProjectNotFoundError,
)
from ...domains.workspace_layouts.models import WorkspaceLayout, WorkspaceLayoutVersion
from ...domains.workspace_layouts.policies import WORKSPACE_LAYOUT_KINDS
from ...domains.workspace_layouts.ports import WorkspaceLayoutRepository


WorkspaceLayoutRepositoryProvider = Callable[[], AbstractContextManager[WorkspaceLayoutRepository]]


def get_project_workspace_layout(
    project_id: str,
    layout_kind: str,
    repository_provider: WorkspaceLayoutRepositoryProvider,
) -> WorkspaceLayout:
    _validate_kind(layout_kind)
    with repository_provider() as repository:
        if not repository.project_exists(project_id):
            raise WorkspaceLayoutProjectNotFoundError()
        repository.authorize_read(project_id)
        stored = repository.get_layout(project_id, layout_kind)
    if stored is None:
        raise WorkspaceLayoutNotFoundError()
    return stored


def list_workspace_layout_versions(
    project_id: str,
    layout_kind: str,
    repository_provider: WorkspaceLayoutRepositoryProvider,
) -> list[WorkspaceLayoutVersion]:
    _validate_kind(layout_kind)
    with repository_provider() as repository:
        if not repository.project_exists(project_id):
            raise WorkspaceLayoutProjectNotFoundError()
        repository.authorize_read(project_id)
        return repository.list_versions(project_id, layout_kind)


def _validate_kind(layout_kind: str) -> None:
    if layout_kind not in WORKSPACE_LAYOUT_KINDS:
        raise UnsupportedWorkspaceLayoutKindError()
