from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.routing import APIRoute

from ..database_connection import connect
from ..schemas.managed_local_execution import (
    CentralRun,
    DeviceAuthorize,
    DeviceAuthorizeResponse,
    DeviceEvents,
    DeviceEventsResponse,
    DevicePair,
    DevicePairPreview,
    DeviceRead,
    DeviceSessionResponse,
    PairingCreate,
    PairingTokenResponse,
)
from ..services import managed_local_execution as service


class _LimitedManagedExecutionBodyRoute(APIRoute):
    """Reject oversized device JSON before FastAPI parses it."""

    max_body_bytes = 64 * 1024
    events_max_body_bytes = service.MAX_EVENTS_BODY_BYTES

    def get_route_handler(self):  # type: ignore[no-untyped-def]
        handler = super().get_route_handler()

        async def limited(request: Request):
            receive = request._receive
            received = 0
            limit = (
                self.events_max_body_bytes
                if request.url.path == "/api/local-execution/device/events"
                else self.max_body_bytes
            )

            async def guarded_receive():
                nonlocal received
                message = await receive()
                if message["type"] == "http.request":
                    received += len(message.get("body", b""))
                    if received > limit:
                        raise HTTPException(413, detail={"code": "DEVICE_EVENTS_TOO_LARGE", "message": "PC 실행 보고 크기가 제한을 초과했습니다."})
                return message

            request._receive = guarded_receive
            return await handler(request)

        return limited


router = APIRouter(prefix="/api/local-execution", tags=["managed-local-execution"], route_class=_LimitedManagedExecutionBodyRoute)


@router.get("/devices", response_model=list[DeviceRead])
def list_devices(request: Request) -> list[dict[str, Any]]:
    with connect() as conn:
        return service.list_devices(conn, request.state.principal)


@router.post("/pairing", response_model=PairingTokenResponse)
def create_pairing(payload: PairingCreate, request: Request) -> dict[str, Any]:
    with connect() as conn:
        return service.create_pairing(conn, request.state.principal, payload.device_id)


@router.post("/devices/{binding_id}/session", response_model=DeviceSessionResponse)
def create_session(binding_id: str, request: Request) -> dict[str, Any]:
    with connect() as conn:
        return service.create_session(conn, request.state.principal, binding_id)


@router.post("/devices/{binding_id}/revoke")
def revoke_device(binding_id: str, request: Request) -> dict[str, bool]:
    with connect() as conn:
        service.revoke_device(conn, request.state.principal, binding_id)
    return {"ok": True}


@router.get("/runs", response_model=list[CentralRun])
def list_runs(request_id: str, work_item_id: str, request: Request) -> list[dict[str, Any]]:
    with connect() as conn:
        return service.list_central_runs(conn, request, request_id, work_item_id)


@router.post("/device/pair-preview")
def pair_preview(payload: DevicePairPreview, request: Request) -> dict[str, str]:
    with connect() as conn:
        return service.pairing_preview(conn, service.bearer_token(request, purpose="연결 요청"), payload.device_id)


@router.post("/device/pair", response_model=DeviceRead)
def pair(payload: DevicePair, request: Request) -> dict[str, Any]:
    with connect() as conn:
        return service.pair_device_atomic(conn, service.bearer_token(request, purpose="연결 요청"), payload)


@router.post("/device/authorize", response_model=DeviceAuthorizeResponse)
def authorize(payload: DeviceAuthorize, request: Request) -> dict[str, Any]:
    with connect() as conn:
        return service.authorize_device(conn, request, service.bearer_token(request, purpose="PC 연결"), payload)


@router.post("/device/events", response_model=DeviceEventsResponse)
def events(payload: DeviceEvents, request: Request) -> dict[str, Any]:
    body_bytes = len(service._json(payload.model_dump(mode="json")).encode("utf-8"))
    with connect() as conn:
        accepted = service.accept_events_atomic(conn, payload.binding_id, service.bearer_token(request, purpose="PC 연결"), payload.events, body_bytes)
    return {"accepted": accepted}
