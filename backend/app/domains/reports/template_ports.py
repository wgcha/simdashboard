from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime
from typing import Protocol

from .template_models import (
    ReportTemplate,
    ReportTemplateAuditRecord,
    ReportTemplateInspection,
    StoredReportTemplate,
)


class ReportTemplateRepository(Protocol):
    def list_active_templates(self) -> list[ReportTemplate]: ...

    def get_active_template(self, template_id: str) -> StoredReportTemplate | None: ...

    def transaction(self) -> AbstractContextManager[None]: ...

    def insert_template(
        self,
        *,
        template_id: str,
        name: str,
        filename: str,
        file_path: str,
        slide_count: int,
        definition: dict[str, object],
        actor_name: str,
        occurred_at: datetime,
    ) -> None: ...

    def add_upload_audit(self, audit: ReportTemplateAuditRecord) -> None: ...

    def deactivate(self, template_id: str, occurred_at: datetime) -> None: ...


class ReportTemplateFiles(Protocol):
    def relative_path(self, template_id: str) -> str: ...

    def promote_upload(self, relative_path: str, data: bytes) -> None: ...

    def remove_upload(self, relative_path: str) -> None: ...

    def read_for_render(self, relative_path: str) -> bytes: ...

    def quarantine_for_delete(self, relative_path: str) -> object | None: ...

    def restore_quarantine(self, quarantine: object | None) -> None: ...

    def purge_quarantine(self, quarantine: object | None) -> None: ...


class PptxTemplateProcessor(Protocol):
    def inspect(self, data: bytes) -> ReportTemplateInspection: ...

    def render(self, data: bytes, replacements: dict[str, str]) -> bytes: ...
