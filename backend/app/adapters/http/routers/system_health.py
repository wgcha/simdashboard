from __future__ import annotations

from fastapi import APIRouter

from ....adapters.persistence.system_health import SQLSystemHealthProbe
from ....application.system_health.queries import health as health_query
from ....config import database_settings


router = APIRouter()


@router.get("/api/health")
def health() -> dict[str, str]:
    return health_query(SQLSystemHealthProbe(), lambda: database_settings().backend)
