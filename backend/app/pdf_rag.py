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
from app.embeddings import cosine, embed, embedding_available, embedding_dimension

logger = logging.getLogger("chronokg.pdf_rag")

CHUNK_SIZE = 900
CHUNK_OVERLAP = 120
MAX_PDF_CHARS = 60000

# A dimension mismatch is a data problem, not a per-query one. Warn once per
# process so a chat that searches five times does not write five identical
# lines — but never let the mismatch pass unmentioned again.
_dimension_warning_logged = False


def extract_pdf_pages(path, max_chars: int = MAX_PDF_CHARS) -> list[tuple[int, str]]:
    """Extract ``(1-based page number, text)`` pairs from a PDF.

    Page numbers originate from the PDF reader itself. They are never inferred
    from chunk order, and are left absent for pre-migration chunks.
    """
    pages: list[tuple[int, str]] = []
    remaining = max(0, max_chars)
    if remaining == 0:
        return pages
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(str(path))
        try:
            for page_number, page in enumerate(doc, start=1):
                if remaining <= 0:
                    break
                text = page.get_text() or ""
                text = text[:remaining]
                remaining -= len(text)
                if text.strip():
                    pages.append((page_number, text))
            return pages
        finally:
            doc.close()
    except Exception:
        pass
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        for page_number, page in enumerate(reader.pages, start=1):
            if remaining <= 0:
                break
            text = page.extract_text() or ""
            text = text[:remaining]
            remaining -= len(text)
            if text.strip():
                pages.append((page_number, text))
        return pages
    except Exception:
        return []


def extract_pdf_text(path, max_chars: int = MAX_PDF_CHARS) -> str:
    """Compatibility helper returning extracted PDF text as one string."""
    return " ".join(text for _, text in extract_pdf_pages(path, max_chars=max_chars))


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


def chunk_pdf_pages(pages: list[tuple[int, str]]) -> tuple[list[str], list[int]]:
    """Chunk pages independently so a chunk always has one verifiable page."""
    chunks: list[str] = []
    page_numbers: list[int] = []
    for page_number, text in pages:
        for chunk in chunk_text(text):
            chunks.append(chunk)
            page_numbers.append(page_number)
    return chunks, page_numbers


def _save_chunks(
    entry_id: int,
    chunks: list[str],
    vectors: list[list[float]],
    page_numbers: list[int | None] | None = None,
) -> int:
    if page_numbers is None:
        page_numbers = [None] * len(chunks)
    conn = get_db()
    try:
        conn.execute("DELETE FROM documents WHERE entry_id = ?", [entry_id])
        for i, (chunk, vec, page_number) in enumerate(zip(chunks, vectors, page_numbers)):
            conn.execute(
                "INSERT INTO documents (entry_id, chunk_index, page_number, content, embedding) VALUES (?, ?, ?, ?, ?)",
                [entry_id, i, page_number, chunk[:2000], json.dumps(vec)],
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
        chunks, page_numbers = chunk_pdf_pages(extract_pdf_pages(path))
        if not chunks:
            return 0
        vectors = embed(chunks)
        if vectors is None:
            # No embedding backend (Ollama not installed): still store the text.
            # Retrieval then falls back to keyword ranking over these chunks,
            # which beats having no PDF content at all for study questions.
            logger.info(
                "Embeddings unavailable for entry %s — storing %d chunks without vectors",
                entry_id, len(chunks),
            )
            return _save_chunks(entry_id, chunks, [[] for _ in chunks], page_numbers)
        return _save_chunks(entry_id, chunks, vectors, page_numbers)

    try:
        return await anyio.to_thread.run_sync(_run)
    except Exception as exc:
        logger.warning("PDF indexing failed for entry %s: %s", entry_id, exc)
        return 0


def vector_stats() -> dict:
    """Read-only summary of the vectors stored in the `documents` table.

    Returns ``{"chunks", "dims", "active_dim", "matchable"}``: `dims` maps a
    stored dimension to how many chunks carry it (key 0 covers NULL, blank and
    unparseable JSON as well as empty vectors), `active_dim` is the dimension
    the active embedding backend produces (None when no backend can be probed)
    and `matchable` is how many chunks `search_documents` can actually compare
    against a query vector. The query is read-only: it never touches a column.
    """
    active_dim = embedding_dimension()
    conn = get_db()
    try:
        rows = conn.execute("SELECT embedding FROM documents").fetchall()
    finally:
        conn.close()

    dims: dict[int, int] = {}
    for r in rows:
        try:
            emb = json.loads(r["embedding"])
        except (TypeError, json.JSONDecodeError):
            emb = None
        dim = len(emb) if isinstance(emb, list) and emb else 0
        dims[dim] = dims.get(dim, 0) + 1

    matchable = dims.get(active_dim, 0) if active_dim else 0
    return {
        "chunks": len(rows),
        "dims": dict(sorted(dims.items())),
        "active_dim": active_dim,
        "matchable": matchable,
    }


def _warn_dimension_mismatch(chunks: int, active_dim: int, dims: dict[int, int]) -> None:
    """Say loudly, once per process, that no stored vector can be compared."""
    global _dimension_warning_logged
    if _dimension_warning_logged:
        return
    _dimension_warning_logged = True
    stored = ", ".join(f"{dim}d: {count}" for dim, count in sorted(dims.items()))
    logger.warning(
        "0 of %d document vectors match the active embedding dimension %dd (stored: %s); "
        "run scripts/reembed_documents.py to re-embed them",
        chunks, active_dim, stored or "none",
    )


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
                """SELECT d.id, d.entry_id, d.chunk_index, d.page_number,
                          d.content, d.embedding, e.title AS entry_title
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
        if not qv:
            return []
        scored = []
        dims: dict[int, int] = {}
        for r in rows:
            try:
                emb = json.loads(r["embedding"])
            except (TypeError, json.JSONDecodeError):
                emb = None
            dim = len(emb) if isinstance(emb, list) and emb else 0
            dims[dim] = dims.get(dim, 0) + 1
            if dim == len(qv):
                scored.append((cosine(qv, emb), r))
        if not scored:
            # Silence here is what hid the 768-vs-1024 breakage for weeks.
            _warn_dimension_mismatch(len(rows), len(qv), dims)
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {
                "id": r["id"],
                "document_id": r["id"],
                "entry_id": r["entry_id"],
                "chunk_index": r["chunk_index"],
                "page_number": r["page_number"],
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
