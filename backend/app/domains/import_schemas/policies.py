from __future__ import annotations

from .errors import ImportSchemaValidationError
from .models import ImportSchemaDefinition


def validate_definition(definition: ImportSchemaDefinition) -> None:
    """Keep the established, deliberately narrow mappings validation."""
    if not isinstance(definition.get("mappings"), list):
        raise ImportSchemaValidationError()


def definition_for_create(definition: ImportSchemaDefinition, schema_id: str) -> ImportSchemaDefinition:
    return {**definition, "schema_id": definition.get("schema_id") or schema_id, "version": 1}


def definition_for_update(
    definition: ImportSchemaDefinition,
    previous_definition: ImportSchemaDefinition,
    schema_id: str,
) -> ImportSchemaDefinition:
    version = int(previous_definition.get("version") or 1) + 1
    return {
        **definition,
        "schema_id": previous_definition.get("schema_id") or schema_id,
        "version": version,
    }
