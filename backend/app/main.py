"""FastAPI application: wiring, security headers, static frontend serving.

Deliberately has no CORS middleware — the frontend is served by this same
process on the same origin, so there is nothing to allow-list. If this is
ever split into separate origins, CORS must be added back restrictively.
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import config, security
from .db import get_connection, init_db
from .routes import auth, importexport, migrate, patients

logging.basicConfig(
    level=logging.DEBUG if config.DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("clinic")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FRONTEND_HTML = PROJECT_ROOT / "clinic-v2.html"
FRONTEND_JS = PROJECT_ROOT / "clinic-v2.js"

CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "script-src-attr 'unsafe-inline'; "  # narrow, documented exception — see SECURITY_AUDIT.md
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'; "
    "form-action 'self'"
)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    conn = get_connection()
    init_db(conn)
    security.ensure_bootstrap_password(conn)
    security.purge_expired_sessions(conn)
    logger.info(
        "clinic-tracker backend starting on %s:%s (https=%s debug=%s)",
        config.HOST,
        config.PORT,
        config.HTTPS,
        config.DEBUG,
    )
    yield


app = FastAPI(
    title="Clinic Tracker API",
    docs_url="/api/docs" if config.DEBUG else None,
    redoc_url=None,
    lifespan=lifespan,
)

app.add_middleware(TrustedHostMiddleware, allowed_hosts=[config.HOST, "127.0.0.1", "localhost"])


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    if config.HTTPS:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"error": "Internal server error"})


app.include_router(auth.router)
app.include_router(patients.router)
app.include_router(importexport.router)
app.include_router(migrate.router)


@app.get("/")
def serve_index():
    return FileResponse(FRONTEND_HTML, media_type="text/html")


@app.get("/clinic-v2.html")
def serve_index_alias():
    return FileResponse(FRONTEND_HTML, media_type="text/html")


@app.get("/clinic-v2.js")
def serve_frontend_js():
    return FileResponse(FRONTEND_JS, media_type="application/javascript")
