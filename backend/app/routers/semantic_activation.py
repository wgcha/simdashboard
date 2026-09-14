"""Atomic publication boundary for a sample-verified configuration pair."""
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..database_connection import connect
from ..domains.semantic_mapping.engine import SemanticValidationError
from ..modules.access_control import SYSTEM_CATALOG_MANAGE, require_permission
from ..security import write_audit_event
from ..services.semantic_activation import ActivationConflict, activate_bundle
from .semantic_body_limit import SemanticBodyLimitRoute

router = APIRouter(prefix="/api/semantic-mapping", tags=["semantic-mapping"], route_class=SemanticBodyLimitRoute)


class BundleActivation(BaseModel):
    recipe_id: str = Field(min_length=1, max_length=160)
    recipe_version: int = Field(ge=1)
    template_id: str = Field(min_length=1, max_length=160)
    template_version: int = Field(ge=1)
    expected_recipe_active_version: int | None = Field(default=None, ge=1)
    expected_template_active_version: int | None = Field(default=None, ge=1)


@router.post("/activate-bundle")
def publish_bundle(payload: BundleActivation, request: Request) -> dict:
    with connect() as conn:
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
        try:
            return activate_bundle(
                conn, payload.model_dump(), request.state.principal.user_id,
                datetime.now(timezone.utc).replace(tzinfo=None),
                lambda: write_audit_event(request=request, principal=request.state.principal, status_code=200,
                                         action="SEMANTIC_BUNDLE_ACTIVATED", detail=payload.model_dump(), connection=conn),
            )
        except ActivationConflict as error:
            raise HTTPException(409, {"code": "SEMANTIC_REVISION_CONFLICT", "message": str(error), "current_active_versions": error.current}) from error
        except SemanticValidationError as error:
            raise HTTPException(422, {"code": error.code, "message": error.message}) from error
