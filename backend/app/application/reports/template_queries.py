from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager

from ...domains.reports.template_models import ReportTemplate
from ...domains.reports.template_ports import ReportTemplateRepository


AuthorizationCheck = Callable[[], object]
ReportTemplateRepositoryProvider = Callable[[], AbstractContextManager[ReportTemplateRepository]]


def list_report_templates(
    authorize: AuthorizationCheck,
    repository_provider: ReportTemplateRepositoryProvider,
) -> list[ReportTemplate]:
    authorize()
    with repository_provider() as repository:
        return repository.list_active_templates()
