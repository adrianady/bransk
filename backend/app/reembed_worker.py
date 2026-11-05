"""
Repair PDF-chunk vectors in the background, at startup.

Why this exists: `documents.embedding` holds vectors from whichever model
indexed a PDF first. When the active backend changes (this machine went from
Ollama nomic-embed-text, 768 dims, to llama.cpp bge-m3, 1024 dims) the old
vectors become incomparable and `search_documents` silently skips them: the UI
kept saying "semantic search: on" while 92% of the corpus was unreachable.

The app is normally left running for hours, so the repair runs here instead of
being a command someone has to remember. It is deliberately polite:

* only chunks whose dimension differs from the active one are redone;
* small batches with a pause between them, so the CPU stays usable;
* one worker per process, and it stops cleanly on shutdown;
* `CHRONOKG_REEMBED_ON_START=0` disables it entirely.

Progress is exposed through `reembed_state()` for `/api/ai/status`.
"""
import json
import logging
import os
import sqlite3
import threading
import time

from app.db import DB_PATH, get_db
from app.embeddings import embed, embedding_dimension

logger = logging.getLogger("chronokg.reembed")

# How many chunks per embedding call. bge-m3 on CPU handles 8 short texts per
# request in roughly a second, which keeps the pause between batches honest.
BATCH_SIZE = 8
# Sleep between batches so a repair never monopolises every core.
PAUSE_SECONDS = 0.15
# Give the app time to finish starting (DB init, first requests) first.
START_DELAY_SECONDS = 20.0
# How often to look for newly mismatched chunks after the first pass.
RESCAN_SECONDS = 900.0

_stopping = threading.Event()

_state: dict = {
    "running": False,
    "done": 0,
    "total": 0,
    "finished_at": None,
    "last_error": None,
    "passes": 0,
}


def reembed_state() -> dict:
    """Snapshot for the status endpoint (never raises)."""
    return dict(_state)


def _pending_ids(active_dim: int) -> list[int]:
    """Ids of chunks whose stored vector is not `active_dim`-dimensional.

    Reads only the `id` and `embedding` columns; the dimension is measured on
    the parsed JSON, so a NULL/blank/garbage vector also counts as pending.
    """
    conn = get_db()
    try:
        rows = conn.execute("SELECT id, embedding FROM documents").fetchall()
    finally:
        conn.close()

    pending = []
    for r in rows:
        try:
            emb = json.loads(r["embedding"])
        except (TypeError, json.JSONDecodeError):
            emb = None
        dim = len(emb) if isinstance(emb, list) and emb else 0
        if dim != active_dim:
            pending.append(r["id"])
    return pending


def _repair_one_pass(active_dim: int) -> int:
    """Re-embed every pending chunk once. Returns how many were written."""
    pending = _pending_ids(active_dim)
    _state["total"] = len(pending)
    _state["done"] = 0
    # Clear the previous error only when a pass actually starts, so a failure
    # recorded mid-pass survives for `/api/ai/status` instead of being erased.
    _state["last_error"] = None
    if not pending:
        return 0

    logger.info("Re-embedding %d chunk(s) to %dd in the background", len(pending), active_dim)
    written = 0
    conn = get_db()
    try:
        for start in range(0, len(pending), BATCH_SIZE):
            chunk_ids = pending[start:start + BATCH_SIZE]
            placeholders = ",".join("?" * len(chunk_ids))
            rows = conn.execute(
                f"SELECT id, content FROM documents WHERE id IN ({placeholders})",
                chunk_ids,
            ).fetchall()
            if not rows:
                continue
            vectors = embed([r["content"] or "" for r in rows])
            if vectors is None:
                # Backend went away mid-pass: stop and let the next rescan
                # pick the remainder up instead of writing empty vectors.
                _state["last_error"] = "embedding backend unavailable"
                break
            for row, vec in zip(rows, vectors):
                if not vec or len(vec) != active_dim:
                    continue
                conn.execute("UPDATE documents SET embedding = ? WHERE id = ?",
                             [json.dumps(vec), row["id"]])
                written += 1
            conn.commit()
            _state["done"] = start + len(rows)
            if len(pending) > BATCH_SIZE:
                time.sleep(PAUSE_SECONDS)
    except sqlite3.Error as exc:
        _state["last_error"] = f"{type(exc).__name__}: {exc}"
        logger.warning("Re-embed pass failed: %s", exc)
    finally:
        conn.close()
    return written


def _worker() -> None:
    """Blocking loop, meant to run in a worker thread."""
    _state["running"] = True
    try:
        time.sleep(START_DELAY_SECONDS)
        while True:
            active_dim = embedding_dimension()
            if active_dim is None:
                _state["last_error"] = "no embedding backend"
                # Nothing to do until an embedding server answers.
                time.sleep(RESCAN_SECONDS)
                if _stopping.is_set():
                    return
                continue
            written = _repair_one_pass(active_dim)
            _state["passes"] += 1
            _state["finished_at"] = time.time()
            if written:
                logger.info("Re-embed pass done: %d chunk(s) now %dd", written, active_dim)
            # Wait for either the next rescan or shutdown.
            if _stopping.wait(RESCAN_SECONDS):
                return
    finally:
        _state["running"] = False


def start_background_reembed() -> bool:
    """Start the repair thread once per process. Returns True if started."""
    if os.getenv("CHRONOKG_REEMBED_ON_START", "1") != "1":
        logger.info("Background re-embed disabled (CHRONOKG_REEMBED_ON_START=0)")
        return False
    existing = getattr(start_background_reembed, "_thread", None)
    if existing is not None and existing.is_alive():
        return False
    _stopping.clear()
    thread = threading.Thread(target=_worker, name="chronokg-reembed", daemon=True)
    start_background_reembed._thread = thread  # type: ignore[attr-defined]
    thread.start()
    logger.info("Background re-embed worker started (db: %s)", DB_PATH)
    return True


def stop_background_reembed() -> None:
    """Ask the worker to finish; used from the app lifespan on shutdown."""
    _stopping.set()
