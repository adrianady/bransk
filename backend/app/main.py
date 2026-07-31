"""
ChronoKG — FastAPI Application
"""
import logging
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import init_db
from app.routers import (
    entries, search, timeline, relationships,
    chat, tags, spaced_repetition, todos, reminders, thoughts,
    research, theory
)

# Structured Python Logging Configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("chronokg")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing ChronoKG Database Schema & FTS5 Index...")
    init_db()
    logger.info("ChronoKG Backend Startup Complete.")
    yield
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


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}
