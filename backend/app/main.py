"""
ChronoKG — FastAPI Application
"""
import asyncio
import logging
import sys
from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import init_db
from app.routers import (
    entries, search, timeline, relationships,
    chat, tags, spaced_repetition, todos, reminders, thoughts,
    research, theory, maintenance, ai
)
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
    logger.info("Initializing ChronoKG Database Schema & FTS5 Index...")
    init_db()
    ticker = asyncio.create_task(_reminder_ticker())
    logger.info("ChronoKG Backend Startup Complete.")
    try:
        yield
    finally:
        ticker.cancel()
        try:
            await ticker
        except asyncio.CancelledError:
            pass
        logger.info("ChronoKG Backend Shutting Down.")


app = FastAPI(
    title="ChronoKG — Chronological Knowledge Graph",
    version="0.1.0",
    lifespan=lifespan,
)

# Restrict CORS methods and headers to required set
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:3000"
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
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


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}
