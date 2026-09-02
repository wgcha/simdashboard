from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager

from ...domains.import_schemas.models import ImportSchema
from ...domains.import_schemas.ports import ImportSchemaRepository


ImportSchemaRepositoryProvider = Callable[[], AbstractContextManager[ImportSchemaRepository]]


def list_import_schemas(repository_provider: ImportSchemaRepositoryProvider) -> list[ImportSchema]:
    with repository_provider() as repository:
        return repository.list_active()
