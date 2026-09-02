from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Protocol

from .models import (
    MenuPolicy,
    MenuPolicyAuditRecord,
    MenuPolicyVersion,
    MenuPolicyVersionSummary,
    MenuVisibility,
)


class MenuPolicyReader(Protocol):
    """Read-side persistence contract for the policy and its snapshots."""

    def authorize_manage(self) -> None: ...

    def get_policy(self) -> MenuPolicy | None: ...

    def list_versions(self) -> list[MenuPolicyVersionSummary]: ...

    def get_version(self, version: int) -> MenuPolicyVersion | None: ...


class MenuPolicyUnitOfWork(MenuPolicyReader, Protocol):
    """A locked transaction that reauthorizes before a policy mutation."""

    def write_snapshot(
        self,
        *,
        version: int,
        visibility: MenuVisibility,
        actor_id: str,
        occurred_at: datetime,
        source_version: int | None,
        change_note: str,
    ) -> None: ...

    def add_audit(self, audit: MenuPolicyAuditRecord) -> None: ...


MenuPolicyReaderProvider = Callable[[], AbstractContextManager[MenuPolicyReader]]
MenuPolicyUnitOfWorkProvider = Callable[[], AbstractContextManager[MenuPolicyUnitOfWork]]
