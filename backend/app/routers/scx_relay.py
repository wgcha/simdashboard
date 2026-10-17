"""SCX relay internal API for PhysicsAI (docs/features/scx-relay.md §5).

``/internal/scx-relay/v1`` lies outside ``/api``: the Vite dev proxy and Caddy forward only
``/api``·``/assets`` (and ``/physicsai`` in development), so a browser can never reach it through
them.  Every handler runs :func:`relay.guard` first (disabled 404, loopback and no proxy header
403, shared secret 401, scx mode 503).  The routes are not in the OpenAPI document.

Errors use the contract §7 body (``code``, ``message``, ``retryable``, ``retry_after``,
``adapter_code``) and every response carries ``Cache-Control: no-store``.  No handler holds a
database connection while it waits for the drive (the service functions open short ones).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from ..services.drive import relay

logger = logging.getLogger("app.routers.scx_relay")

router = APIRouter(prefix=relay.PREFIX, include_in_schema=False)
NO_STORE = {"Cache-Control": "no-store"}


def _json(status: int, body: Any) -> JSONResponse:
    return JSONResponse(body, status_code=status, headers=dict(NO_STORE))


def _error(error: relay.RelayError) -> JSONResponse:
    headers = dict(NO_STORE)
    if error.retry_after:
        headers["Retry-After"] = str(error.retry_after)
    return JSONResponse(error.body(), status_code=error.status, headers=headers)


def _host(request: Request) -> str | None:
    return request.client.host if request.client else None


def _guarded(request: Request, body: Callable[[str], tuple[int, Any]]) -> Response:
    try:
        client = relay.guard(_host(request), request.headers)
        status, payload = body(client)
    except relay.RelayError as error:
        return _error(error)
    return _json(status, payload)


async def _json_body(request: Request) -> Any:
    """The JSON body, bounded (contract shapes are small); malformed → 422 ``INVALID_REQUEST``."""
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > relay.JSON_BODY_MAX:
            raise relay.RelayError(413, "TOO_LARGE", "요청 본문이 너무 큽니다.")
    try:
        return json.loads(bytes(raw).decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise relay.RelayError(422, "INVALID_REQUEST", "본문이 JSON이 아닙니다.") from None


async def _guarded_json(request: Request, body: Callable[[Any, str], tuple[int, Any]]) -> Response:
    try:
        client = relay.guard(_host(request), request.headers)
        payload = await _json_body(request)
        status, result = await run_in_threadpool(body, payload, client)
    except relay.RelayError as error:
        return _error(error)
    return _json(status, result)


@router.get("/health")
def health(request: Request) -> Response:
    return _guarded(request, lambda _client: (200, relay.health()))


@router.post("/mkdirs")
async def mkdirs(request: Request) -> Response:
    return await _guarded_json(request, relay.mkdirs)


@router.post("/files")
async def upload_file(request: Request) -> Response:
    """Streaming body → server staging (sha256 checked) → one queue batch ``[MKDIR, FILE]`` (202)."""
    plan: relay.UploadPlan | None = None
    writer: relay.StagingWriter | None = None
    try:
        client = relay.guard(_host(request), request.headers)
        planned = await run_in_threadpool(relay.plan_upload, client, request.query_params, request.headers)
        if not isinstance(planned, relay.UploadPlan):
            status, payload = planned          # same request_id replayed: the body is not read
            return _json(status, payload)
        plan = planned
        writer = relay.StagingWriter(plan)
        async for chunk in request.stream():
            writer.write(chunk)
        status, payload = await run_in_threadpool(relay.commit_upload, plan, writer)
        plan = None
    except relay.RelayError as error:
        return _error(error)
    finally:
        if plan is not None:              # failed or interrupted before the batch was queued
            relay.abort_upload(plan, writer)
    return _json(status, payload)


@router.get("/batches/{batch_id}")
def batch(request: Request, batch_id: str) -> Response:
    return _guarded(request, lambda _client: (200, relay.batch(batch_id)))


@router.get("/list")
def list_folder(request: Request) -> Response:
    return _guarded(request, lambda _client: (200, relay.list_entries(request.query_params)))


class _DownloadResponse(FileResponse):
    """The relay download; its ``relay-dl-*`` folder is removed when the send ends or breaks."""

    def __init__(self, download: relay.Download) -> None:
        headers = {**NO_STORE, "X-Content-SHA256": download.sha256}
        if download.version_token:
            headers["X-Drive-Version"] = download.version_token
        super().__init__(download.local_path, media_type="application/octet-stream", headers=headers,
                         filename=Path(download.local_path).name, content_disposition_type="attachment")
        self.download = download

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            relay.finish_download(self.download)


@router.get("/files")
def download_file(request: Request) -> Response:
    try:
        relay.guard(_host(request), request.headers)
        download = relay.prepare_download(request.query_params)
    except relay.RelayError as error:
        return _error(error)
    return _DownloadResponse(download)


@router.post("/jobs/radioss")
async def submit_radioss(request: Request) -> Response:
    return await _guarded_json(request, relay.submit)


@router.get("/jobs/{job_id}")
def get_job(request: Request, job_id: str) -> Response:
    return _guarded(request, lambda _client: (200, relay.get_job(job_id)))


@router.post("/jobs/{job_id}/terminate")
def terminate_job(request: Request, job_id: str) -> Response:
    return _guarded(request, lambda _client: (200, relay.terminate_job(job_id)))


@router.post("/submissions/{request_id}/resolve")
async def resolve_submission(request: Request, request_id: str) -> Response:
    return await _guarded_json(request, lambda payload, _client: (200, relay.resolve(request_id, payload)))
