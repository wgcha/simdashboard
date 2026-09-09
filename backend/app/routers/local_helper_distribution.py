from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse

from ..schemas.local_helper_distribution import LocalHelperDistributionReady, LocalHelperDistributionUnavailable
from ..services.local_helper_distribution import load_distribution


router = APIRouter(prefix="/api/local-helper", tags=["local-helper-distribution"])


@router.get(
    "/distribution",
    response_model=LocalHelperDistributionReady | LocalHelperDistributionUnavailable,
)
def distribution(response: Response) -> dict[str, object]:
    response.headers["Cache-Control"] = "no-store"
    artifact, reason = load_distribution()
    if artifact is None:
        return {"status": "unavailable", "reason": reason or "Windows 로컬 도우미 배포본이 준비되지 않았습니다."}
    return artifact.response()


@router.get("/distribution/download", response_class=FileResponse)
@router.head("/distribution/download", response_class=FileResponse, include_in_schema=False)
def download_distribution() -> FileResponse:
    artifact, reason = load_distribution()
    if artifact is None:
        raise HTTPException(404, detail={"code": "LOCAL_HELPER_DISTRIBUTION_UNAVAILABLE", "message": reason})
    return FileResponse(
        artifact.path,
        media_type="application/zip",
        filename=artifact.filename,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )
