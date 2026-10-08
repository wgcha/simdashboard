"""Read-only solver materials catalog for request-owned scenes (distribution: Radioss, usage: OptiStruct)."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

from ..database_connection import connect
from ..modules.access_control import PROJECT_DATA_VIEW, require_resource_permission
from ..services.drive import reads as drive_reads
from ..services import materials_catalog as service
from ..services import result_registration_paths, spdm_storage

router = APIRouter(prefix="/api/materials", tags=["materials"])


def _raise_error(exc: Exception) -> None:
    if isinstance(exc, service.MaterialsCatalogError):
        status_code, code, message = exc.status_code, exc.code, str(exc)
    elif isinstance(exc, result_registration_paths.ResultRegistrationError):
        status_code, code, message = 422, exc.code, str(exc)
    elif isinstance(exc, spdm_storage.SpdmStorageError):
        status_code, code, message = 422, exc.code, str(exc)
    else:
        raise exc
    raise HTTPException(status_code, detail={"code": code, "message": message}) from exc


@router.get("/catalog")
@drive_reads.read_session()
def materials_catalog(request: Request, request_id: str = Query(min_length=1, max_length=128),
                      environment: Literal["DISTRIBUTION", "USAGE"] = "DISTRIBUTION"):
    with connect() as conn:
        require_resource_permission(request, PROJECT_DATA_VIEW, "request", request_id, conn=conn)
        try:
            return service.catalog(conn, request_id, environment)
        except (service.MaterialsCatalogError, result_registration_paths.ResultRegistrationError,
                spdm_storage.SpdmStorageError) as exc:
            _raise_error(exc)


@router.get("/deck")
@drive_reads.read_session()
def materials_deck(request: Request, request_id: str = Query(min_length=1, max_length=128),
                   environment: Literal["DISTRIBUTION", "USAGE"] = "DISTRIBUTION",
                   scene_id: str | None = Query(default=None, max_length=256),
                   relative_path: str | None = Query(default=None, max_length=2048),
                   retry: bool = Query(default=False, description="USAGE: re-run a FAILED OptiStruct parse")):
    with connect() as conn:
        require_resource_permission(request, PROJECT_DATA_VIEW, "request", request_id, conn=conn)
        try:
            return service.deck(conn, request_id, environment, scene_id, relative_path, retry=retry)
        except (service.MaterialsCatalogError, result_registration_paths.ResultRegistrationError,
                spdm_storage.SpdmStorageError) as exc:
            _raise_error(exc)
