"""
ChronoKG — FastAPI Application
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import init_db
from app.routers import (
    entries, search, timeline, relationships,
    chat, tags, spaced_repetition, todos, reminders, thoughts
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="ChronoKG — Chronological Knowledge Graph",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
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


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}
