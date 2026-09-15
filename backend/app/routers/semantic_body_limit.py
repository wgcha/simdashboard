"""Bound JSON configuration uploads before FastAPI parses their bodies."""
from fastapi import HTTPException, Request
from fastapi.routing import APIRoute


class SemanticBodyLimitRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded(request: Request):
            # Multipart routes stream through their own stricter MIME boundary.
            upload_paths = {"/api/semantic-mapping/inspect", "/api/semantic-mapping/preview", "/api/semantic-mapping/import"}
            if request.method in {"POST", "PUT"} and request.url.path not in upload_paths:
                limit = 6 * 1024 * 1024
                length = request.headers.get("content-length")
                if length is not None:
                    try:
                        size = int(length)
                    except ValueError:
                        raise HTTPException(400, {"code": "SEMANTIC_CONTENT_LENGTH_INVALID"})
                    if size < 0 or size > limit:
                        raise HTTPException(413, {"code": "SEMANTIC_REQUEST_SIZE_LIMIT"})
                chunks, size = [], 0
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(413, {"code": "SEMANTIC_REQUEST_SIZE_LIMIT"})
                    chunks.append(chunk)
                # Populate Starlette's normal body cache so FastAPI's validation
                # reads these bounded bytes without consuming the stream twice.
                request._body = b"".join(chunks)
            return await handler(request)

        return bounded
