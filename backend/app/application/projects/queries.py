from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager

from ...domains.projects.models import Project
from ...domains.projects.ports import ProjectRepository


AuthorizationCheck = Callable[[], object]
ProjectRepositoryProvider = Callable[[], AbstractContextManager[ProjectRepository]]


def list_projects(
    authorize: AuthorizationCheck,
    repository_provider: ProjectRepositoryProvider,
) -> list[Project]:
    """Authorize first, then own the repository connection lifecycle."""
    authorize()
    with repository_provider() as repository:
        return repository.list_projects()
