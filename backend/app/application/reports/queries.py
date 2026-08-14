from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager

from ...domains.reports.models import ReportLayout
from ...domains.reports.ports import ReportLayoutRepository


AuthorizationCheck = Callable[[], object]
ReportLayoutRepositoryProvider = Callable[[], AbstractContextManager[ReportLayoutRepository]]


def list_report_layouts(
    authorize: AuthorizationCheck,
    repository_provider: ReportLayoutRepositoryProvider,
) -> list[ReportLayout]:
    """Read active layouts after the request has passed authentication.

    The legacy endpoint intentionally has no catalog-management permission
    requirement.  ``authorize`` therefore only proves the middleware supplied
    a principal before any persistence resource is opened.
    """
    authorize()
    with repository_provider() as repository:
        return repository.list_active_layouts()
