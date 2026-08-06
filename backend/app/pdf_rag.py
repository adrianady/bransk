"""
PDF RAG — index downloaded study PDFs as embeddable chunks and search them.

Full text of each downloaded paper is extracted, chunked with overlap, embedded
once (stored in the `documents` table), and searched semantically via the same
Ollama embedding backend used for context re-ranking. Offline-safe: without
embeddings, search returns [] and the app simply falls back to entry content.
"""
import json
import logging
import re
from pathlib import Path

import anyio

from app.db import get_db
from app.embeddings import cosine, embed, embedding_available

logger = logging.getLogger("chronokg.pdf_rag")

CHUNK_SIZE = 900
CHUNK_OVERLAP = 120
MAX_PDF_CHARS = 60000


def extract_pdf_text(path, max_chars: int = MAX_PDF_CHARS) -> str:
    """Extract text from a PDF (PyMuPDF first, pypdf fallback)."""
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(str(path))
        try:
            return " ".join(page.get_text() for page in doc)[:max_chars]
        finally:
            doc.close()
    except Exception:
        pass
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return " ".join((page.extract_text() or "") for page in reader.pages)[:max_chars]
    except Exception:
        return ""


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks on sentence/word boundaries."""
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            # Back off to the last sentence/word boundary inside the window
            tail = text.rfind(". ", start, end)
            if tail != -1 and end - tail < size // 2:
                end = tail + 1
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [c for c in chunks if c]


def _save_chunks(entry_id: int, chunks: list[str], vectors: list[list[float]]) -> int:
    conn = get_db()
    try:
        conn.execute("DELETE FROM documents WHERE entry_id = ?", [entry_id])
        for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
            conn.execute(
                "INSERT INTO documents (entry_id, chunk_index, content, embedding) VALUES (?, ?, ?, ?)",
                [entry_id, i, chunk[:2000], json.dumps(vec)],
            )
        conn.commit()
        return len(chunks)
    finally:
        conn.close()


async def index_pdf(entry_id: int, path) -> int:
    """Extract, chunk and embed a PDF; replace any previous index for the entry.

    Returns the number of chunks indexed, or 0 when nothing could be indexed.
    """
    if path is None:
        return 0
    path = Path(path)
    if not path.exists():
        return 0

    def _run() -> int:
        text = extract_pdf_text(path)
        if not text.strip():
            return 0
        chunks = chunk_text(text)
        if not chunks:
            return 0
        vectors = embed(chunks)
        if vectors is None:
            return 0
        return _save_chunks(entry_id, chunks, vectors)

    try:
        return await anyio.to_thread.run_sync(_run)
    except Exception as exc:
        logger.warning("PDF indexing failed for entry %s: %s", entry_id, exc)
        return 0


async def search_documents(query: str, top_k: int = 5) -> list[dict]:
    """Semantic search over indexed PDF chunks. Returns [] when embeddings are off."""
    query = (query or "").strip()
    if not query:
        return []
    if not embedding_available():
        return []

    def _run() -> list[dict]:
        conn = get_db()
        try:
            rows = conn.execute(
                """SELECT d.id, d.entry_id, d.content, d.embedding, e.title AS entry_title
                   FROM documents d
                   JOIN entries e ON e.id = d.entry_id
                   WHERE e.archived = 0
                   ORDER BY d.id"""
            ).fetchall()
        finally:
            conn.close()
        if not rows:
            return []
        vectors = embed([query])
        if vectors is None:
            return []
        qv = vectors[0]
        scored = []
        for r in rows:
            try:
                emb = json.loads(r["embedding"])
            except (TypeError, json.JSONDecodeError):
                continue
            if emb and len(emb) == len(qv):
                scored.append((cosine(qv, emb), r))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {
                "id": r["id"],
                "entry_id": r["entry_id"],
                "entry_title": r["entry_title"],
                "content": r["content"][:1500],
                "score": round(score, 4),
            }
            for score, r in scored[:top_k]
        ]

    try:
        return await anyio.to_thread.run_sync(_run)
    except Exception as exc:
        logger.warning("Document search failed: %s", exc)
        return []


def pdf_chunk_count() -> int:
    conn = get_db()
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()
        return row["n"] if row else 0
    finally:
        conn.close()
