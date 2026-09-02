from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from ...domains.reports.models import (
    ReportLayoutAuditContext,
    ReportLayoutNotFoundError,
    SavedReportLayout,
    SystemReportLayoutDeactivationError,
)
from ...domains.reports.ports import ReportLayoutRepository
from .policies import validate_report_layout


AuthorizationCheck = Callable[[], object]
Clock = Callable[[], datetime]
LayoutIdFactory = Callable[[], str]
ReportLayoutRepositoryProvider = Callable[[], AbstractContextManager[ReportLayoutRepository]]


def create_report_layout(
    *,
    name: str,
    description: str,
    definition: dict[str, Any],
    actor_name: str,
    audit: ReportLayoutAuditContext,
    authorize: AuthorizationCheck,
    repository_provider: ReportLayoutRepositoryProvider,
    id_factory: LayoutIdFactory | None = None,
    clock: Clock | None = None,
) -> SavedReportLayout:
    authorize()
    layout_id = (id_factory or new_layout_id)()
    now = (clock or utc_now)()
    trimmed_name = name.strip()
    trimmed_description = description.strip()
    stored_definition = validate_report_layout(
        {
            **definition,
            "id": layout_id,
            "name": trimmed_name,
            "description": trimmed_description,
            "version": 1,
        }
    )
    with repository_provider() as repository:
        with repository.transaction():
            repository.insert_layout(
                layout_id=layout_id,
                name=trimmed_name,
                description=trimmed_description,
                definition=stored_definition,
                actor_name=actor_name,
                occurred_at=now,
            )
            repository.insert_version(
                layout_id=layout_id,
                version=1,
                definition=stored_definition,
                actor_name=actor_name,
                occurred_at=now,
            )
            repository.add_audit(
                {
                    **audit,
                    "action": "REPORT_LAYOUT_CREATED",
                    "status_code": 201,
                    "detail": {"layout_id": layout_id},
                }
            )
    return {
        "id": layout_id,
        "name": trimmed_name,
        "description": trimmed_description,
        "version": 1,
        "definition": stored_definition,
        "is_system": False,
        "updated_at": now,
        "updated_by": actor_name,
    }


def update_report_layout(
    *,
    layout_id: str,
    name: str,
    description: str,
    definition: dict[str, Any],
    actor_name: str,
    audit: ReportLayoutAuditContext,
    authorize: AuthorizationCheck,
    repository_provider: ReportLayoutRepositoryProvider,
    clock: Clock | None = None,
) -> SavedReportLayout:
    authorize()
    now = (clock or utc_now)()
    trimmed_name = name.strip()
    trimmed_description = description.strip()
    with repository_provider() as repository:
        existing = repository.get_active_state(layout_id)
        if existing is None:
            raise ReportLayoutNotFoundError()
        version = existing["version"] + 1
        stored_definition = validate_report_layout(
            {
                **definition,
                "id": layout_id,
                "name": trimmed_name,
                "description": trimmed_description,
                "version": version,
            }
        )
        with repository.transaction():
            repository.update_layout(
                layout_id=layout_id,
                name=trimmed_name,
                description=trimmed_description,
                version=version,
                definition=stored_definition,
                actor_name=actor_name,
                occurred_at=now,
            )
            repository.insert_version(
                layout_id=layout_id,
                version=version,
                definition=stored_definition,
                actor_name=actor_name,
                occurred_at=now,
            )
            repository.add_audit(
                {
                    **audit,
                    "action": "REPORT_LAYOUT_UPDATED",
                    "status_code": 200,
                    "detail": {"layout_id": layout_id, "version": version},
                }
            )
    return {
        "id": layout_id,
        "name": trimmed_name,
        "description": trimmed_description,
        "version": version,
        "definition": stored_definition,
        "is_system": bool(existing["is_system"]),
        "updated_at": now,
        "updated_by": actor_name,
    }


def deactivate_report_layout(
    *,
    layout_id: str,
    authorize: AuthorizationCheck,
    repository_provider: ReportLayoutRepositoryProvider,
    clock: Clock | None = None,
) -> dict[str, str]:
    authorize()
    with repository_provider() as repository:
        existing = repository.get_active_state(layout_id)
        if existing is None:
            raise ReportLayoutNotFoundError()
        if existing["is_system"]:
            raise SystemReportLayoutDeactivationError()
        repository.deactivate(layout_id, (clock or utc_now)())
    return {"status": "deactivated", "id": layout_id}


def new_layout_id() -> str:
    return f"report-layout-{uuid4().hex[:12]}"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
