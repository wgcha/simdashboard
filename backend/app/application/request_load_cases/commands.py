from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from ...domains.request_load_cases.errors import RequestNotFoundError
from ...domains.request_load_cases.ports import RequestLoadCaseWriteRepositoryProvider


AuthorizeResource = Callable[[str, object], None]
IdentifierFactory = Callable[[], str]
Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_identifier() -> str:
    return uuid4().hex[:12]


def create_load_case(
    *,
    request_id: str,
    name: str,
    analysis_type: str,
    parameters: dict[str, Any],
    authorize: AuthorizeResource,
    repository_provider: RequestLoadCaseWriteRepositoryProvider,
    identifier_factory: IdentifierFactory | None = None,
    clock: Clock | None = None,
) -> dict[str, Any]:
    load_case_id = f"loadcase-{(identifier_factory or new_identifier)()}"
    now = (clock or utc_now)()
    normalized_name = name.strip()
    with repository_provider() as repository:
        repository.authorize_resource(lambda connection: authorize(request_id, connection))
        if not repository.request_exists(request_id):
            raise RequestNotFoundError()
        repository.insert_load_case(
            load_case_id,
            request_id,
            normalized_name,
            analysis_type,
            parameters,
            now,
        )
    return {
        "id": load_case_id,
        "request_id": request_id,
        "name": normalized_name,
        "analysis_type": analysis_type,
        "status": "READY",
        "parameters": parameters,
        "created_at": now,
    }
