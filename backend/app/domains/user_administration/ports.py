from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Protocol

from .models import AccountStatus, UserAccount, UserAdministrationAuditRecord


class UserAdministrationUnitOfWork(Protocol):
    """A locked transaction that reauthorizes before changing an account."""

    def authorize_user_approval(self) -> None: ...

    def find_user_for_status(self, user_id: str) -> tuple[AccountStatus, bool, str | None, datetime] | None: ...

    def find_user_for_global_admin(self, user_id: str) -> tuple[bool, AccountStatus, datetime] | None: ...

    def count_active_global_admins(self) -> int: ...

    def update_account_status(
        self,
        user_id: str,
        account_status: AccountStatus,
        actor_id: str,
        occurred_at: datetime,
    ) -> None: ...

    def ready_pending_invitations(
        self, employee_id: str, user_id: str, actor_id: str, occurred_at: datetime
    ) -> None: ...

    def update_global_admin(
        self, user_id: str, is_global_admin: bool, occurred_at: datetime
    ) -> None: ...

    def add_audit(self, audit: UserAdministrationAuditRecord) -> None: ...

    def find_user(self, user_id: str) -> UserAccount | None: ...


UserAdministrationUnitOfWorkProvider = Callable[[], AbstractContextManager[UserAdministrationUnitOfWork]]
