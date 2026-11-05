"""Study text files on disk, next to the project (on the same HDD as bransk).

Why not only the database: a full text is 30-600 KB. Stored as `documents`
chunks with a JSON vector per chunk it balloons (the 506 chunks already there
take ~11 MB of the 13 MB database, with 22 KB of JSON per vector) and the
whole file must be parsed on every search. On disk, one `.txt` per study is
cheap, greppable, and survives a database restore.

Layout:
    backend/data/studytext/<entry_id>-<slug>.txt   extracted full text
    backend/data/studies/<entry_id>-<slug>.pdf      the PDF itself

The text file is the single source of truth for "do we have the full text?"
— `has_text(entry_id)` answers it without touching the database.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import anyio

logger = logging.getLogger("chronokg.studytext")

# Same switch and default as the PDFs: everything lives under backend/data,
# which is on the HDD where the project is (/media/adrian/D4C42C44C42C2B662).
TEXT_DIR = Path(
    __import__("os").getenv(
        "CHRONOKG_STUDYTEXT_DIR",
        str(Path(__file__).parent.parent / "data" / "studytext"),
    )
)


def ensure_dir() -> Path:
    TEXT_DIR.mkdir(parents=True, exist_ok=True)
    return TEXT_DIR


def _slugify(title: str, max_len: int = 60) -> str:
    slug = re.sub(r"[^\w\s-]", "", title or "").strip().lower()
    slug = re.sub(r"[\s_-]+", "-", slug).strip("-")
    return slug[:max_len] or "study"


def text_path_for(entry_id: int) -> Path | None:
    try:
        matches = sorted(ensure_dir().glob(f"{entry_id}-*.txt"))
    except OSError:
        return None
    return matches[0] if matches else None


def save_text(entry_id: int, title: str, text: str) -> Path:
    """Write the study text to disk, replacing an older, shorter extract."""
    text = (text or "").strip()
    if not text:
        raise ValueError("empty text")
    ensure_dir()
    existing = text_path_for(entry_id)
    if existing and existing.stat().st_size >= len(text.encode("utf-8")):
        return existing
    path = ensure_dir() / f"{entry_id}-{_slugify(title)}.txt"
    if existing and existing != path:
        existing.unlink(missing_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def read_text(entry_id: int) -> str:
    path = text_path_for(entry_id)
    if not path:
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def has_text(entry_id: int) -> bool:
    path = text_path_for(entry_id)
    try:
        return bool(path and path.stat().st_size > 500)
    except OSError:
        return False


async def index_text(entry_id: int, text: str | None = None) -> int:
    """Chunk the study text and embed it into `documents` for semantic search.

    The raw text lives on disk; the database keeps only the chunks needed to
    match a question, so a large library stays searchable without copying
    whole papers into SQLite.
    """
    from app.db import get_db
    from app.embeddings import embed
    from app.pdf_rag import chunk_text

    body = (text if text is not None else read_text(entry_id)) or ""
    if len(body.strip()) < 200:
        return 0
    chunks = chunk_text(body)
    if not chunks:
        return 0

    def _run() -> int:
        vectors = embed(chunks)
        conn = get_db()
        try:
            conn.execute("DELETE FROM documents WHERE entry_id = ?", [entry_id])
            for i, chunk in enumerate(chunks):
                vec = vectors[i] if vectors and i < len(vectors) and vectors[i] else []
                conn.execute(
                    "INSERT INTO documents (entry_id, chunk_index, content, embedding) "
                    "VALUES (?, ?, ?, ?)",
                    [entry_id, i, chunk[:2000], __import__("json").dumps(vec)],
                )
            conn.commit()
            return len(chunks)
        finally:
            conn.close()

    try:
        return await anyio.to_thread.run_sync(_run)
    except Exception as exc:  # noqa: BLE001 - indexing is best effort
        logger.warning("Study text indexing failed for #%s: %s", entry_id, exc)
        return 0
