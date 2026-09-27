"""Public booking endpoints.

`public_router` is mounted in the owner app (LAN preview at /book) and is the
*only* thing in `create_public_app`, which is what gets exposed to the
internet (Tailscale Funnel -> 127.0.0.1:8001). No owner route exists there.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Body, FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse

from . import __version__
from .db.connection import connect, migrate, utcnow
from .services import public
from .services.core import CoreService, ServiceError

STATIC = Path(__file__).resolve().parent / "static"
PUBLIC_STATIC = STATIC / "public"


def client_ip(request: Request) -> str:
    host = request.client.host if request.client else ""
    if host in ("127.0.0.1", "::1"):  # behind a local proxy (tailscale funnel / serve)
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return host


def public_router(open_conn, now_fn) -> APIRouter:
    r = APIRouter()

    def svc_call(fn, *args):
        conn = open_conn()
        try:
            return fn(CoreService(conn, now_fn), *args)
        finally:
            conn.close()

    @r.get("/book", include_in_schema=False)
    @r.get("/book/", include_in_schema=False)
    @r.get("/book/status/{ref}", include_in_schema=False)
    def page(ref: str | None = None):
        return FileResponse(PUBLIC_STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @r.get("/book/assets/{path:path}", include_in_schema=False)
    def assets(path: str):
        base = PUBLIC_STATIC.resolve()
        f = (base / path).resolve()
        if base not in f.parents or not f.is_file():
            return JSONResponse({"error": "NOT_FOUND"}, status_code=404)
        return FileResponse(f, headers={"Cache-Control": "no-cache"})

    @r.get("/book/fonts/{name}", include_in_schema=False)
    def fonts(name: str):
        f = (STATIC / "fonts" / name).resolve()
        if (STATIC / "fonts").resolve() not in f.parents or not f.is_file():
            return JSONResponse({"error": "NOT_FOUND"}, status_code=404)
        return FileResponse(f, headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @r.get("/book/theme.css", include_in_schema=False)
    def theme():
        f = STATIC / "theme.css"
        if not f.is_file():
            return JSONResponse({"error": "NOT_FOUND"}, status_code=404)
        return FileResponse(f, media_type="text/css", headers={"Cache-Control": "no-cache"})

    @r.get("/api/public/profile")
    def profile():
        return svc_call(public.profile)

    @r.post("/api/public/suggest")
    def suggest(body: dict = Body(...)):
        return svc_call(public.suggest, int(body.get("duration_min") or 0))

    @r.get("/api/public/day/{d}")
    def day(d: date, duration_min: int = 0):
        return svc_call(public.public_day, d, duration_min)

    @r.post("/api/public/requests")
    def create(request: Request, body: dict = Body(...)):
        return svc_call(public.create_request, body, client_ip(request))

    @r.get("/api/public/requests/{ref}")
    def status(ref: str):
        return svc_call(public.request_status, ref)

    @r.post("/api/public/requests/{ref}/withdraw")
    def withdraw(ref: str):
        return svc_call(public.withdraw, ref)

    return r


def add_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ServiceError)
    async def _service_error(request: Request, exc: ServiceError):
        return JSONResponse(exc.to_dict(), status_code=exc.status)

    @app.exception_handler(ValueError)
    async def _value_error(request: Request, exc: ValueError):
        return JSONResponse({"error": "INVALID", "message": str(exc)}, status_code=400)


def create_public_app(cfg=None, now_fn=utcnow) -> FastAPI:
    from . import config as config_mod
    cfg = cfg or config_mod.load()

    def open_conn():
        return connect(cfg.db_path)

    conn = open_conn()
    migrate(conn)
    conn.close()
    app = FastAPI(title="Booking", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    add_error_handlers(app)
    app.include_router(public_router(open_conn, now_fn))

    @app.get("/", include_in_schema=False)
    def root():
        return FileResponse(PUBLIC_STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    return app
