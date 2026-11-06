"""
 Bransk — FastAPI application built on ChronoKG
"""
import asyncio
import logging
import sys
from contextlib import asynccontextmanager

import anyio
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.auth import (
    PUBLIC_API_PATHS,
    SESSION_COOKIE,
    extract_token,
    session_matches,
    token_matches,
    token_required,
    trusted_request_origin,
)
from app.db import init_db
from app.routers import (
    entries, search, timeline, relationships,
    chat, tags, spaced_repetition, todos, reminders, thoughts,
    research, theory, maintenance, ai, study_qa, project, sources,
    citations, highlights
)
from app.routers import auth as auth_router
from app.routers.reminders import advance_overdue_recurring

# Structured Python Logging Configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("chronokg")


async def _reminder_ticker() -> None:
    """Periodically advance dismissed recurring reminders that are past due."""
    while True:
        try:
            advanced = await anyio.to_thread.run_sync(advance_overdue_recurring)
            if advanced:
                logger.info("Advanced %d recurring reminder(s)", advanced)
        except Exception:
            logger.exception("Reminder scheduler tick failed")
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing Bransk Database Schema & FTS5 Index...")
    init_db()
    ticker = asyncio.create_task(_reminder_ticker())
    # The app is usually left running for hours, so repair PDF vectors that were
    # written by a different embedding model on startup instead of waiting for
    # someone to remember a script. Runs in a worker thread, batches + pauses.
    try:
        from app.reembed_worker import start_background_reembed
        start_background_reembed()
    except Exception:
        logger.exception("Could not start the background re-embed worker")
    logger.info("Bransk Backend Startup Complete.")
    try:
        yield
    finally:
        try:
            from app.reembed_worker import stop_background_reembed
            stop_background_reembed()
        except Exception:
            pass
        ticker.cancel()
        try:
            await ticker
        except asyncio.CancelledError:
            pass
        logger.info("Bransk Backend Shutting Down.")


app = FastAPI(
    title="Bransk — Research Knowledge Graph",
    version="0.1.0",
    lifespan=lifespan,
)

# Restrict CORS methods and headers to required set. Origins are the dev
# frontend plus any local / Tailscale address the phone may use to reach this
# machine (Tailscale IPs live in 100.64.0.0/10).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:3000",
    ],
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|100\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})(:\d+)?",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-ChronoKG-Token", "X-Requested-With"],
)

# ── Optional network authentication ────────────────────────────────────────
# Registered after CORS, so it wraps it: a request is authorized before any
# protected route is reached. With no CHRONOKG_AUTH_TOKEN this is a pass-through
# and the app behaves exactly as before (local-first, no login).
@app.middleware("http")
async def require_api_token(request, call_next):
    path = request.url.path

    async def continue_request():
        response = await call_next(request)
        if path.startswith("/api/auth/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    if not token_required():
        return await continue_request()

    # Preflight requests carry no headers; the browser sends the real one next.
    # Static files and the SPA shell stay public so the login screen can load.
    if request.method == "OPTIONS" or not path.startswith("/api/"):
        return await continue_request()

    if path in PUBLIC_API_PATHS:
        if path in {"/api/auth/login", "/api/auth/logout"} and request.method not in {"GET", "HEAD", "OPTIONS"}:
            if not trusted_request_origin(request):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Originea cererii nu este acceptată."},
                    headers={"Cache-Control": "no-store"},
                )
        return await continue_request()

    if token_matches(extract_token(request.headers, dict(request.query_params))):
        return await continue_request()

    if await anyio.to_thread.run_sync(session_matches, request.cookies.get(SESSION_COOKIE)):
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not trusted_request_origin(request):
            return JSONResponse(
                status_code=403,
                content={"detail": "Originea cererii nu este acceptată."},
                headers={"Cache-Control": "no-store"},
            )
        return await continue_request()

    headers = {"WWW-Authenticate": "Bearer"}
    if path.startswith("/api/auth/"):
        headers["Cache-Control"] = "no-store"
    return JSONResponse(
        status_code=401,
        content={"detail": "Autentificare necesară: token sau sesiune lipsă ori expirată."},
        headers=headers,
    )


# Register API Routers
app.include_router(entries.router, prefix="/api", tags=["entries"])
app.include_router(search.router, prefix="/api", tags=["search"])
app.include_router(timeline.router, prefix="/api", tags=["timeline"])
app.include_router(relationships.router, prefix="/api", tags=["relationships"])
app.include_router(tags.router, prefix="/api", tags=["tags"])
app.include_router(chat.router, prefix="/api", tags=["chat"])
app.include_router(spaced_repetition.router, prefix="/api", tags=["spaced-repetition"])
app.include_router(todos.router, prefix="/api", tags=["todos"])
app.include_router(reminders.router, prefix="/api", tags=["reminders"])
app.include_router(thoughts.router, prefix="/api", tags=["thoughts"])
app.include_router(research.router, prefix="/api", tags=["research"])
app.include_router(theory.router, prefix="/api", tags=["theory"])
app.include_router(maintenance.router, prefix="/api", tags=["maintenance"])
app.include_router(ai.router, prefix="/api", tags=["ai"])
app.include_router(study_qa.router, prefix="/api", tags=["study_qa"])
app.include_router(project.router, prefix="/api", tags=["project"])
app.include_router(citations.router, prefix="/api", tags=["citations"])
app.include_router(highlights.router, prefix="/api", tags=["highlights"])
app.include_router(sources.router, prefix="/api", tags=["sources"])
app.include_router(auth_router.router, prefix="/api/auth", tags=["auth"])


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


# --- Serve the built frontend (single link on the phone) --------------------
# The React app is built into frontend/dist; when that folder exists it is
# served from the same origin as the API, so one address works on the phone.
_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

if _DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/")
    async def spa_root():
        return FileResponse(_DIST / "index.html")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        """Serve static files, falling back to index.html for client-side routes."""
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = (_DIST / full_path).resolve()
        if candidate.is_file() and _DIST.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(_DIST / "index.html")
