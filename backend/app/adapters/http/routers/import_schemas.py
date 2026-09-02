from __future__ import annotations

from typing import Any, NoReturn
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request

from ....adapters.persistence.import_schemas import SQLImportSchemaRepositoryProvider
from ....application.import_schemas.commands import (
    create_import_schema as create_import_schema_command,
    delete_import_schema as delete_import_schema_command,
    update_import_schema as update_import_schema_command,
)
from ....application.import_schemas.queries import list_import_schemas as list_import_schemas_query
from ....database_connection import ConnectionLike
from ....domains.import_schemas.errors import (
    ImportSchemaError,
    ImportSchemaInUseError,
    ImportSchemaNotFoundError,
    ImportSchemaValidationError,
)
from ....domains.import_schemas.models import ImportSchemaAuditContext
from ....modules.access_control import SYSTEM_CATALOG_MANAGE, require_permission
from ....schemas.api import ImportSchemaPayload
from ....security import write_audit_event


router = APIRouter()


def _authorizer(request: Request):
    def authorize(connection: ConnectionLike) -> None:
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=connection)

    return authorize


def _audit(request: Request) -> ImportSchemaAuditContext:
    principal = request.state.principal
    return {
        "user_id": principal.user_id,
        "username": principal.username,
        "role": principal.role,
        "method": request.method,
        "path": request.url.path,
        "request_id": getattr(request.state, "request_id", str(uuid4())),
        "client_ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent", "")[:500],
    }


def _audit_writer(request: Request):
    principal = request.state.principal

    def write(audit: Any, connection: ConnectionLike) -> None:
        write_audit_event(
            request=request,
            principal=principal,
            status_code=audit["status_code"],
            action=audit["action"],
            detail=audit["detail"],
            connection=connection,
        )

    return write


def _raise_mapped(error: ImportSchemaError) -> NoReturn:
    if isinstance(error, ImportSchemaValidationError):
        raise HTTPException(422, error.message) from error
    if isinstance(error, ImportSchemaNotFoundError):
        raise HTTPException(404, error.message) from error
    if isinstance(error, ImportSchemaInUseError):
        raise HTTPException(409, error.message) from error
    raise error


@router.get("/api/import-schemas")
def list_import_schemas() -> list[dict[str, Any]]:
    return list_import_schemas_query(SQLImportSchemaRepositoryProvider())


@router.post("/api/import-schemas", status_code=201)
def create_import_schema(payload: ImportSchemaPayload, request: Request) -> dict[str, Any]:
    try:
        return create_import_schema_command(
            name=payload.name,
            description=payload.description,
            definition=payload.definition,
            actor_name=request.state.principal.display_name,
            audit=_audit(request),
            repository_provider=SQLImportSchemaRepositoryProvider(
                authorize=_authorizer(request),
                audit_writer=_audit_writer(request),
            ),
        )
    except ImportSchemaError as error:
        _raise_mapped(error)


@router.put("/api/import-schemas/{schema_id}")
def update_import_schema(schema_id: str, payload: ImportSchemaPayload, request: Request) -> dict[str, Any]:
    try:
        return update_import_schema_command(
            schema_id=schema_id,
            name=payload.name,
            description=payload.description,
            definition=payload.definition,
            actor_name=request.state.principal.display_name,
            audit=_audit(request),
            repository_provider=SQLImportSchemaRepositoryProvider(
                authorize=_authorizer(request),
                audit_writer=_audit_writer(request),
            ),
        )
    except ImportSchemaError as error:
        _raise_mapped(error)


@router.delete("/api/import-schemas/{schema_id}")
def delete_import_schema(schema_id: str, request: Request) -> dict[str, str]:
    # This intentionally opens its own authorization connection before the repository.
    require_permission(request, SYSTEM_CATALOG_MANAGE)
    try:
        return delete_import_schema_command(
            schema_id=schema_id,
            repository_provider=SQLImportSchemaRepositoryProvider(),
        )
    except ImportSchemaError as error:
        _raise_mapped(error)
