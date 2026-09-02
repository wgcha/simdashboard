from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
import logging
from typing import Any
from uuid import uuid4

from ...domains.reports.template_models import (
    InvalidReportTemplateError,
    ReportTemplate,
    ReportTemplateAuditContext,
    ReportTemplateFileMissingError,
    ReportTemplateNotFoundError,
)
from ...domains.reports.template_ports import (
    PptxTemplateProcessor,
    ReportTemplateFiles,
    ReportTemplateRepository,
)


AuthorizationCheck = Callable[[], object]
Clock = Callable[[], datetime]
TemplateIdFactory = Callable[[], str]
ReportTemplateRepositoryProvider = Callable[[], AbstractContextManager[ReportTemplateRepository]]
logger = logging.getLogger(__name__)


def create_report_template(
    *,
    name: str,
    filename: str,
    data: bytes,
    actor_name: str,
    audit: ReportTemplateAuditContext,
    authorize: AuthorizationCheck,
    repository_provider: ReportTemplateRepositoryProvider,
    files: ReportTemplateFiles,
    processor: PptxTemplateProcessor,
    id_factory: TemplateIdFactory | None = None,
    clock: Clock | None = None,
) -> ReportTemplate:
    authorize()
    inspected = processor.inspect(data)
    if not inspected["slide_count"]:
        raise InvalidReportTemplateError("슬라이드가 없는 PPTX는 사용할 수 없습니다.")
    template_id = (id_factory or new_template_id)()
    relative_path = files.relative_path(template_id)
    now = (clock or utc_now)()
    definition: dict[str, Any] = {
        "slideWidth": inspected["slide_width"],
        "slideHeight": inspected["slide_height"],
        "placeholders": inspected["placeholders"],
    }
    # The file is promoted first so a committed row can never reference a
    # partial upload. Every later failure removes only this exact managed file.
    files.promote_upload(relative_path, data)
    try:
        with repository_provider() as repository:
            with repository.transaction():
                repository.insert_template(
                    template_id=template_id,
                    name=name.strip(),
                    filename=filename,
                    file_path=relative_path,
                    slide_count=inspected["slide_count"],
                    definition=definition,
                    actor_name=actor_name,
                    occurred_at=now,
                )
                repository.add_upload_audit(
                    {
                        **audit,
                        "action": "REPORT_TEMPLATE_UPLOADED",
                        "status_code": 201,
                        "detail": {"template_id": template_id},
                    }
                )
    except Exception:
        files.remove_upload(relative_path)
        raise
    return {
        "id": template_id,
        "name": name.strip(),
        "filename": filename,
        "slide_count": inspected["slide_count"],
        "definition": definition,
        "created_at": now,
        "updated_at": now,
        "updated_by": actor_name,
    }


def render_report_template(
    *,
    template_id: str,
    replacements: dict[str, str],
    authorize: AuthorizationCheck,
    repository_provider: ReportTemplateRepositoryProvider,
    files: ReportTemplateFiles,
    processor: PptxTemplateProcessor,
) -> bytes:
    authorize()
    with repository_provider() as repository:
        stored = repository.get_active_template(template_id)
    if stored is None:
        raise ReportTemplateNotFoundError()
    try:
        source = files.read_for_render(stored["file_path"])
    except (FileNotFoundError, OSError):
        raise ReportTemplateFileMissingError() from None
    return processor.render(source, replacements)


def deactivate_report_template(
    *,
    template_id: str,
    authorize: AuthorizationCheck,
    repository_provider: ReportTemplateRepositoryProvider,
    files: ReportTemplateFiles,
    clock: Clock | None = None,
) -> dict[str, str]:
    authorize()
    with repository_provider() as repository:
        stored = repository.get_active_template(template_id)
        if stored is None:
            raise ReportTemplateNotFoundError()
        # Renaming to a private quarantine happens before the database commit.
        # A rollback restores the file; success purges it after commit.
        quarantine = files.quarantine_for_delete(stored["file_path"])
        try:
            with repository.transaction():
                repository.deactivate(template_id, (clock or utc_now)())
        except Exception:
            files.restore_quarantine(quarantine)
            raise
    try:
        files.purge_quarantine(quarantine)
    except OSError:
        # The deactivation is already durable. Implementations retain the
        # private quarantine for reconciliation, so this must not turn a
        # successful delete into an unretryable client-visible failure.
        logger.exception("report template quarantine purge deferred")
    return {"status": "deactivated", "id": template_id}


def new_template_id() -> str:
    return f"report-template-{uuid4().hex[:12]}"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
