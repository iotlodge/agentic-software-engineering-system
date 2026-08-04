"""HTTP API for the URL shortener (baseline).

Contract highlights:
- ``POST /v1/links`` creates (201) or idempotently replays (200) a short link.
- ``GET /{code}`` issues a temporary redirect (307) so destinations can change
  without permanent client-side caching; unknown codes 404, disabled 410.
- ``DELETE /v1/links/{code}`` is a soft disable (204), never physical deletion.
- Analytics are eventually consistent.
- Errors use one envelope: ``{"error": {"code", "message"}}``.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from .analytics import AnalyticsSink
from .domain import Link, LinkValidationError, is_servable, validate_url
from .observability import RequestContextMiddleware
from .persistence import Database, NotFoundError


class CreateLinkRequest(BaseModel):
    url: str
    idempotency_key: str | None = None


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"error": {"code": code, "message": message}})


def _link_body(link: Link, base_url: str) -> dict:
    return {
        "code": link.code,
        "short_url": f"{base_url.rstrip('/')}/{link.code}",
        "url": link.url,
        "created_at": link.created_at,
        "disabled": link.disabled,
    }


def create_app(db_path: str | None = None, admin_token: str | None = None,
               now_fn=None) -> FastAPI:
    app = FastAPI(title="URL Shortener", version="1.0.0",
                  description="Short links with click analytics.")
    app.add_middleware(RequestContextMiddleware)

    db = Database(db_path or os.environ.get("SHORTENER_DB") or ":memory:")
    db.migrate()
    state = app.state
    state.db = db
    state.admin_token = admin_token or os.environ.get("SHORTENER_ADMIN_TOKEN") or "dev-admin"
    state.analytics = AnalyticsSink(db)
    state.now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    @app.exception_handler(NotFoundError)
    async def _not_found(request: Request, exc: NotFoundError):
        return _error(404, "not_found", f"unknown link code: {exc}")

    @app.exception_handler(LinkValidationError)
    async def _invalid(request: Request, exc: LinkValidationError):
        return _error(400, "invalid_request", str(exc))

    @app.post("/v1/links", status_code=201)
    async def create_link(payload: CreateLinkRequest, request: Request):
        url = validate_url(payload.url)
        link, created = db.create_link(url, payload.idempotency_key)
        body = _link_body(link, str(request.base_url))
        return body if created else JSONResponse(status_code=200, content=body)

    @app.get("/v1/links/{code}")
    async def link_details(code: str, request: Request):
        return _link_body(db.get_link(code), str(request.base_url))

    @app.get("/v1/links/{code}/stats")
    async def link_stats(code: str):
        db.get_link(code)  # 404 for unknown codes
        return {"code": code, **state.analytics.stats(code)}

    @app.delete("/v1/links/{code}", status_code=204)
    async def disable_link(code: str,
                           x_admin_token: str | None = Header(default=None)):
        if x_admin_token is None:
            return _error(401, "unauthenticated", "X-Admin-Token header required")
        if x_admin_token != state.admin_token:
            return _error(403, "forbidden", "invalid admin token")
        db.disable_link(code)

    @app.get("/health/live")
    async def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready():
        if not db.ping():
            return _error(503, "not_ready", "database unavailable")
        return {"status": "ready",
                "analytics_loss_risk": state.analytics.loss_risk_events}

    @app.get("/{code}")
    async def resolve(code: str):
        link = db.get_link(code)
        if not is_servable(link):
            return _error(410, "gone", f"link {code} is disabled")
        state.analytics.record(code, state.now_fn().isoformat())
        return RedirectResponse(link.url, status_code=307)

    return app


app = create_app()
