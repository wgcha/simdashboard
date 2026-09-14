"""Read-only impact preview for a saved semantic bundle candidate."""
from fastapi import APIRouter, HTTPException, Request

from ..database_connection import connect
from ..domains.semantic_mapping.engine import SemanticValidationError
from ..modules.access_control import SYSTEM_CATALOG_MANAGE, require_permission
from ..schemas.semantic_impact import BundleImpactResponse
from ..services.semantic_impact import assess_bundle_impact
from .semantic_activation import BundleActivation
from .semantic_body_limit import SemanticBodyLimitRoute


router = APIRouter(prefix="/api/semantic-mapping", tags=["semantic-mapping"], route_class=SemanticBodyLimitRoute)


@router.post("/impact-bundle")
def impact_bundle(payload: BundleActivation, request: Request) -> BundleImpactResponse:
    with connect() as conn:
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
        try:
            return assess_bundle_impact(conn, payload.model_dump())
        except SemanticValidationError as error:
            raise HTTPException(422, {"code": error.code, "message": error.message}) from error
