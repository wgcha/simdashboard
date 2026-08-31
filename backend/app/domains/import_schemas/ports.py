from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime
from typing import Protocol

from .models import (
    ImportSchema,
    ImportSchemaAuditRecord,
    ImportSchemaDefinition,
    ImportSchemaStored,
)


class ImportSchemaRepository(Protocol):
    def transaction(self) -> AbstractContextManager[None]: ...

    def authorize_mutation(self) -> None: ...

    def list_active(self) -> list[ImportSchema]: ...

    def get_active(self, schema_id: str) -> ImportSchemaStored | None: ...

    def insert_active(
        self,
        schema_id: str,
        name: str,
        description: str,
        definition: ImportSchemaDefinition,
        occurred_at: datetime,
        actor_name: str,
    ) -> None: ...

    def update_active(
        self,
        schema_id: str,
        name: str,
        description: str,
        definition: ImportSchemaDefinition,
        occurred_at: datetime,
        actor_name: str,
    ) -> None: ...

    def insert_version(
        self,
        schema_id: str,
        version: int,
        definition: ImportSchemaDefinition,
        occurred_at: datetime,
        actor_name: str,
    ) -> None: ...

    def active_usage_count(self, schema_id: str) -> int: ...

    def deactivate(self, schema_id: str, occurred_at: datetime) -> None: ...

    def add_audit(self, audit: ImportSchemaAuditRecord) -> None: ...
