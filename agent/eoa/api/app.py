"""FastAPI application factory for the EO-Analyst web API (see docs/API.md)."""

from __future__ import annotations

import traceback
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from eoa import db
from eoa.api.errors import APIError
from eoa.api.routes import (
    ask,
    bd,
    clarifications,
    conferences,
    entities,
    feedback,
    investigations,
    items,
    jobs,
    lessons,
    llm,
    mcp,
    reports,
    runs,
    status,
    surveys,
    tech,
    tenders,
)
from eoa.api.routes import settings as settings_routes
from eoa.config import REPO_ROOT

log = structlog.get_logger(__name__)

# docs/API.md: "CORS: allow http://localhost:5173 (Vite dev) and same-origin."
# (same-origin requests never trigger CORS at all, so only the dev-server
# origin needs to be listed here.)
CORS_ORIGINS = ["http://localhost:5173"]

WEB_DIST = REPO_ROOT / "web" / "dist"

_STATUS_CODES = {
    400: "bad_request",
    404: "not_found",
    405: "method_not_allowed",
    422: "validation_error",
    500: "internal_error",
}


def _code_for_status(status_code: int) -> str:
    return _STATUS_CODES.get(status_code, "http_error")


# Q2-9: global request-body cap. `PUT /api/settings/{name}` keeps its own tighter
# 256 KB cap (enforced in `eoa.api.services`, ahead of this one for that route);
# everything else gets this more permissive default.
_MAX_BODY_BYTES = 1_000_000


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject any request whose body exceeds `max_bytes` with a 413 in the app's
    own `{"error": {...}}` envelope, before it reaches any route handler.

    Checks `Content-Length` up front when present (rejects without reading any
    body at all), and also polices the actual byte count as it streams in --
    covering chunked/absent-`Content-Length` requests -- aborting as soon as
    the cap is crossed rather than buffering an arbitrarily large body first.

    The accumulated bytes are then stashed directly into `request._body`
    (rather than handing the body back to `call_next` some other way): this
    matches exactly what Starlette's own `Request.body()` does internally
    (`if not hasattr(self, "_body"): ... self._body = b"".join(chunks)`), and
    `BaseHTTPMiddleware` wraps `request` in `_CachedRequest`, whose downstream
    replay logic checks that very attribute to decide what to hand the route
    handler. Consuming `request.stream()` by hand *without* also setting
    `_body` -- an easy mistake here -- would replay an *empty* body downstream
    instead, silently breaking every POST/PUT.
    """

    def __init__(self, app: Any, max_bytes: int = _MAX_BODY_BYTES) -> None:
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Any:
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_bytes:
                    return self._reject()
            except ValueError:
                pass

        chunks: list[bytes] = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > self.max_bytes:
                return self._reject()
            chunks.append(chunk)
        request._body = b"".join(chunks)  # see docstring above: matches Request.body()'s own cache

        return await call_next(request)

    @staticmethod
    def _reject() -> JSONResponse:
        return JSONResponse(
            status_code=413,
            content={"error": {"code": "payload_too_large", "message_he": "גוף הבקשה גדול מדי", "detail": None}},
        )


class _SPAStaticFiles(StaticFiles):
    """Serves `web/dist`; falls back to `index.html` for any unmatched, non-API path."""

    async def get_response(self, path: str, scope: dict[str, Any]) -> Any:
        req_path = str(scope.get("path", ""))
        if req_path.startswith(("/api", "/ws")) or path.lstrip("/").startswith(("api/", "ws/")):
            raise StarletteHTTPException(status_code=404, detail="not found")
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the DB pool for the process lifetime; close it cleanly on shutdown."""
    db.get_pool()
    log.info("api.startup")
    try:
        yield
    finally:
        db.close_pool()
        log.info("api.shutdown")


def create_app() -> FastAPI:
    """Build the FastAPI app: routers, CORS, `{"error": {...}}` error handling, SPA static mount."""
    app = FastAPI(title="EO-Analyst API", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(BodySizeLimitMiddleware)

    @app.exception_handler(APIError)
    async def _api_error_handler(request: Request, exc: APIError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message_he": exc.message_he, "detail": exc.detail}},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": _code_for_status(exc.status_code),
                    "message_he": str(exc.detail),
                    "detail": None,
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {"code": "validation_error", "message_he": "קלט לא תקין", "detail": exc.errors()}
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # Q2-10: never surface `str(exc)` (stack internals, file paths, occasionally
        # a fragment of a DB error) to the client. Log the full traceback server-side
        # keyed by a random error id, and return only that id plus a generic Hebrew
        # message so a user/support conversation can still correlate the two.
        error_id = uuid.uuid4().hex
        log.error(
            "api.unhandled_error",
            path=request.url.path,
            error_id=error_id,
            traceback=traceback.format_exc(),
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message_he": "שגיאה פנימית בשרת",
                    "detail": {"error_id": error_id},
                }
            },
        )

    app.include_router(status.router, prefix="/api")
    app.include_router(status.ws_router)
    app.include_router(reports.router, prefix="/api")
    app.include_router(items.router, prefix="/api")
    app.include_router(entities.router, prefix="/api")
    app.include_router(investigations.router, prefix="/api")
    app.include_router(investigations.ws_router)
    app.include_router(ask.router, prefix="/api")
    app.include_router(conferences.router, prefix="/api")
    app.include_router(tenders.router, prefix="/api")
    app.include_router(clarifications.router, prefix="/api")
    app.include_router(surveys.router, prefix="/api")
    app.include_router(lessons.router, prefix="/api")
    app.include_router(feedback.router, prefix="/api")
    app.include_router(jobs.router, prefix="/api")
    app.include_router(runs.router, prefix="/api")
    app.include_router(settings_routes.router, prefix="/api")
    app.include_router(llm.router, prefix="/api")
    app.include_router(mcp.router, prefix="/api")
    app.include_router(bd.router, prefix="/api")
    app.include_router(tech.router, prefix="/api")

    if WEB_DIST.exists():
        # Registered after every API router, so `/api/*` and `/ws/*` paths
        # always resolve to their own route first; this mount only ever
        # serves the React SPA and its static assets.
        app.mount("/", _SPAStaticFiles(directory=WEB_DIST, html=True), name="web")

    return app


app = create_app()
