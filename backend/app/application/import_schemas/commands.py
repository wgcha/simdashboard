from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from uuid import uuid4

from ...domains.import_schemas.errors import ImportSchemaInUseError, ImportSchemaNotFoundError
from ...domains.import_schemas.models import (
    ImportSchema,
    ImportSchemaAuditContext,
    ImportSchemaDefinition,
)
from ...domains.import_schemas.policies import definition_for_create, definition_for_update, validate_definition
from ...domains.import_schemas.ports import ImportSchemaRepository


Clock = Callable[[], datetime]
IdentifierFactory = Callable[[], str]
ImportSchemaRepositoryProvider = Callable[[], AbstractContextManager[ImportSchemaRepository]]


def create_import_schema(
    *,
    name: str,
    description: str,
    definition: ImportSchemaDefinition,
    actor_name: str,
    audit: ImportSchemaAuditContext,
    repository_provider: ImportSchemaRepositoryProvider,
    clock: Clock | None = None,
    identifier_factory: IdentifierFactory | None = None,
) -> ImportSchema:
    validate_definition(definition)
    schema_id = (identifier_factory or new_schema_id)()
    now = (clock or utc_now)()
    normalized = definition_for_create(definition, schema_id)
    normalized_name, normalized_description = name.strip(), description.strip()
    with repository_provider() as repository:
        with repository.transaction():
            repository.authorize_mutation()
            repository.insert_active(schema_id, normalized_name, normalized_description, normalized, now, actor_name)
            repository.insert_version(schema_id, 1, normalized, now, actor_name)
            repository.add_audit(
                {
                    **audit,
                    "status_code": 201,
                    "action": "IMPORT_SCHEMA_CREATED",
                    "detail": {"schema_id": schema_id},
                }
            )
    return {
        "id": schema_id,
        "name": normalized_name,
        "description": normalized_description,
        "definition": normalized,
        "created_at": now,
        "updated_at": now,
        "updated_by": actor_name,
    }


def update_import_schema(
    *,
    schema_id: str,
    name: str,
    description: str,
    definition: ImportSchemaDefinition,
    actor_name: str,
    audit: ImportSchemaAuditContext,
    repository_provider: ImportSchemaRepositoryProvider,
    clock: Clock | None = None,
) -> ImportSchema:
    validate_definition(definition)
    now = (clock or utc_now)()
    normalized_name, normalized_description = name.strip(), description.strip()
    with repository_provider() as repository:
        with repository.transaction():
            repository.authorize_mutation()
            existing = repository.get_active(schema_id)
            if existing is None:
                raise ImportSchemaNotFoundError()
            normalized = definition_for_update(definition, existing["definition"], schema_id)
            version = normalized["version"]
            repository.update_active(schema_id, normalized_name, normalized_description, normalized, now, actor_name)
            repository.insert_version(schema_id, version, normalized, now, actor_name)
            repository.add_audit(
                {
                    **audit,
                    "status_code": 200,
                    "action": "IMPORT_SCHEMA_UPDATED",
                    "detail": {"schema_id": schema_id, "version": version},
                }
            )
    return {
        "id": schema_id,
        "name": normalized_name,
        "description": normalized_description,
        "definition": normalized,
        "created_at": existing["created_at"],
        "updated_at": now,
        "updated_by": actor_name,
    }


def delete_import_schema(
    *,
    schema_id: str,
    repository_provider: ImportSchemaRepositoryProvider,
    clock: Clock | None = None,
) -> dict[str, str]:
    """Deactivate a schema after the legacy router-level authorization check."""
    with repository_provider() as repository:
        if repository.get_active(schema_id) is None:
            raise ImportSchemaNotFoundError()
        if repository.active_usage_count(schema_id):
            raise ImportSchemaInUseError()
        repository.deactivate(schema_id, (clock or utc_now)())
    return {"status": "DEACTIVATED", "id": schema_id}


def new_schema_id() -> str:
    return f"import-schema-{uuid4().hex[:12]}"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
