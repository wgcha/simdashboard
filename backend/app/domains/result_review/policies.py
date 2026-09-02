from __future__ import annotations

from .errors import ResultVariableNotFoundError, ReviewEntityIdRequiredError


def validate_bookmark_target(
    variable_key: str | None,
    available_result_keys: set[str],
    entity_type: str | None,
    entity_id: str | None,
) -> None:
    if variable_key and variable_key not in available_result_keys:
        raise ResultVariableNotFoundError()
    if entity_type and not entity_id:
        raise ReviewEntityIdRequiredError()


def body_for_update(new_body: str | None, current_body: str) -> str:
    return new_body.strip() if new_body else current_body
