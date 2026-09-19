"""Profile editing and registration history share the survey admin boundary."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..database_connection import connect
from ..security import write_audit_event
from ..services import environment_folder_profiles as service, folder_discovery as legacy
from ..services.semantic_mapping import semantic_transaction
from .folder_discovery_environment import admin
from .semantic_body_limit import SemanticBodyLimitRoute

router = APIRouter(prefix="/api/folder-discovery/environments", tags=["folder-discovery-environments"], route_class=SemanticBodyLimitRoute)


class Profile(BaseModel):
    environment: Literal["USAGE", "DISTRIBUTION"]
    name: str = Field(min_length=1, max_length=128)
    rules: dict = Field(default_factory=dict)


class ProfileUpdate(Profile):
    expected_revision: int = Field(ge=1)


class LegacyCopy(BaseModel):
    environment: Literal["USAGE", "DISTRIBUTION"]
    name: str = Field(min_length=1, max_length=128)
    relative_path: str = Field(default="", max_length=1024)


def mutate(request, operation):
    with connect() as conn:
        admin(request, conn)
        try:
            with legacy.WRITE_LOCK, semantic_transaction(conn):
                result = operation(conn)
                write_audit_event(request=request, principal=request.state.principal, status_code=200,
                    action="FOLDER_ENVIRONMENT_PROFILE_SAVED", detail={"profile_id": result["id"], "revision": result["revision"]}, connection=conn)
                return result
        except ValueError as exc:
            raise HTTPException(422, {"code": "ENVIRONMENT_PROFILE_INVALID", "message": str(exc)}) from exc


@router.post("/profiles")
def create(payload: Profile, request: Request):
    return mutate(request, lambda conn: service.save_profile(conn, **payload.model_dump()))


@router.put("/profiles/{profile_id}")
def update(profile_id: str, payload: ProfileUpdate, request: Request):
    return mutate(request, lambda conn: service.save_profile(conn, profile_id=profile_id, **payload.model_dump()))


@router.post("/profiles/from-legacy")
def copy(payload: LegacyCopy, request: Request):
    return mutate(request, lambda conn: service.copy_legacy(conn, **payload.model_dump()))


@router.get("/history")
def history(request: Request, limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    with connect() as conn:
        admin(request, conn)
        return service.history(conn, limit, offset)
