"""
Full-text search and hybrid search router.
"""
import re
import sqlite3
from typing import Optional
from fastapi import APIRouter, Query, HTTPException
from app.db import get_db
from app.models import SearchResult, EntryResponse

router = APIRouter()

# Same definition of "study" the /studies endpoint uses (kept in sync).
_STUDY_WHERE = """archived = 0
                 AND (study_type != ''
                      OR tags LIKE '%study%'
                      OR tags LIKE '%pdf%'
                      OR content LIKE '%**PDF:**%')"""

# FTS5 boolean reserved keywords
_FTS_BOOLEAN_KEYWORDS = {"AND", "OR", "NOT", "NEAR"}


def _sanitize_fts5(query: str) -> str:
    """Escape FTS5 special characters and boolean operators to prevent syntax errors."""
    # Remove FTS5 special characters: ^ * " ( ) : ~ + -
    sanitized = re.sub(r'[\^\"\*\(\)\:\~\+\-]', ' ', query)
    tokens = sanitized.split()

    cleaned_tokens = []
    for token in tokens:
        # Wrap boolean operator words in quotes so FTS treats them as literals
        if token.upper() in _FTS_BOOLEAN_KEYWORDS:
            cleaned_tokens.append(f'"{token}"')
        else:
            cleaned_tokens.append(token)

    result = " ".join(cleaned_tokens).strip()
    return result if result else '""'


def _execute_search(q: str, from_date: Optional[str], to_date: Optional[str], tags: Optional[str], limit: int):
    """Synchronous search execution (runs in Starlette thread pool for async endpoints)."""
    conn = get_db()
    try:
        safe_query = _sanitize_fts5(q)
        params: list = [safe_query]
        # Archived entries must never surface in search results (consistent
        # with chat context building and /entries?archived=false).
        where_extra = ["e.archived = 0"]

        if from_date:
            where_extra.append("e.entry_date >= ?")
            params.append(from_date)
        if to_date:
            where_extra.append("e.entry_date <= ?")
            params.append(to_date)
        if tags:
            for t in tags.split(","):
                t = t.strip()
                if t:
                    # Exact token match on comma boundaries: '%ann%' would
                    # also match 'annual'; this only matches the whole tag.
                    # The REPLACE makes "a, b" and "a,b" behave the same.
                    where_extra.append(
                        "(',' || REPLACE(e.tags, ', ', ',') || ',') LIKE '%,' || ? || ',%'"
                    )
                    params.append(t)

        extra_sql = f" AND {' AND '.join(where_extra)}" if where_extra else ""

        try:
            rows = conn.execute(
                f"""SELECT e.*, rank FROM entries_fts
                    JOIN entries e ON entries_fts.rowid = e.id
                    WHERE entries_fts MATCH ?{extra_sql}
                    ORDER BY rank
                    LIMIT ?""",
                [*params, limit]
            ).fetchall()
        except sqlite3.OperationalError:
            # Fallback to phrase search if complex FTS syntax fails
            fallback_query = f'"{q.replace(chr(34), "")}"'
            rows = conn.execute(
                f"""SELECT e.*, rank FROM entries_fts
                    JOIN entries e ON entries_fts.rowid = e.id
                    WHERE entries_fts MATCH ?{extra_sql}
                    ORDER BY rank
                    LIMIT ?""",
                [fallback_query, *params[1:], limit]
            ).fetchall()

        results = []
        for r in rows:
            d = dict(r)
            rank = d.pop("rank", 0)
            results.append(SearchResult(entry=EntryResponse(**d), rank=float(rank)))
        return results
    finally:
        conn.close()


@router.get("/search", response_model=list[SearchResult])
def search(
    q: str = Query(..., min_length=1),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    tags: Optional[str] = Query(None),
    limit: int = Query(20, le=100),
):
    return _execute_search(q, from_date, to_date, tags, limit)


def _execute_suggest(q: str, limit: int):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, title FROM entries WHERE title LIKE ? AND archived = 0 LIMIT ?",
            [f"%{q}%", limit]
        ).fetchall()
        return [{"id": r["id"], "title": r["title"]} for r in rows]
    finally:
        conn.close()


@router.get("/search/suggest")
def suggest(q: str = Query(..., min_length=1), limit: int = Query(5, le=20)):
    return _execute_suggest(q, limit)


@router.get("/search/global")
async def global_search(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(5, le=15),
):
    """One query across everything the writer works with.

    Groups results so the command palette can answer "unde era acel pasaj?"
    in one keystroke: entries (FTS), studies, personal notes, reader
    highlights and indexed PDF chunks (semantic when embeddings are up, LIKE
    otherwise). Chunks carry page_number so the reader can open the exact page.
    """
    query = q.strip()
    if not query:
        return {"query": query, "entries": [], "studies": [], "notes": [],
                "highlights": [], "chunks": [], "total": 0}
    like = f"%{query}%"
    safe = _sanitize_fts5(query)
    conn = get_db()
    try:
        entries = [
            dict(r) for r in conn.execute(
                """SELECT e.id, e.title, e.entry_date, e.tags, e.importance,
                          substr(e.content, 1, 200) AS content_preview
                     FROM entries_fts JOIN entries e ON entries_fts.rowid = e.id
                    WHERE e.archived = 0 AND entries_fts MATCH ?
                    ORDER BY rank LIMIT ?""",
                [safe, limit],
            ).fetchall()
        ]
        studies = [
            dict(r) for r in conn.execute(
                f"""SELECT id, title, authors, entry_date, doi, pdf_path,
                           quality, study_type
                      FROM entries
                     WHERE {_STUDY_WHERE}
                       AND (title LIKE ? OR authors LIKE ? OR doi LIKE ?)
                     ORDER BY entry_date IS NULL, entry_date DESC LIMIT ?""",
                [like, like, like, limit],
            ).fetchall()
        ]
        notes = [
            dict(r) for r in conn.execute(
                """SELECT n.id, n.entry_id, n.content, n.page_number, n.course_context,
                          e.title AS entry_title
                     FROM study_notes n JOIN entries e ON n.entry_id = e.id
                    WHERE e.archived = 0 AND n.content LIKE ?
                    ORDER BY n.updated_at DESC LIMIT ?""",
                [like, limit],
            ).fetchall()
        ]
        highlights = [
            dict(r) for r in conn.execute(
                """SELECT h.id, h.entry_id, h.quote, h.note, h.page_number,
                          e.title AS entry_title
                     FROM pdf_highlights h JOIN entries e ON h.entry_id = e.id
                    WHERE e.archived = 0 AND (h.quote LIKE ? OR h.note LIKE ?)
                    ORDER BY h.id DESC LIMIT ?""",
                [like, like, limit],
            ).fetchall()
        ]
        chunks = [
            dict(r) for r in conn.execute(
                """SELECT d.id, d.entry_id, d.chunk_index, d.page_number,
                          substr(d.content, 1, 240) AS preview,
                          e.title AS entry_title
                     FROM documents d JOIN entries e ON d.entry_id = e.id
                    WHERE e.archived = 0 AND d.content LIKE ?
                    ORDER BY d.id LIMIT ?""",
                [like, limit],
            ).fetchall()
        ]
    finally:
        conn.close()

    # Semantic PDF search only adds value beyond the LIKE above; run it when
    # the embedding backend is up (offline it simply contributes nothing).
    from app.pdf_rag import search_documents
    try:
        semantic = await search_documents(query, top_k=limit)
    except Exception:
        semantic = []
    seen_chunks = {c["id"] for c in chunks}
    for r in semantic:
        if r["document_id"] not in seen_chunks:
            chunks.append({
                "id": r["document_id"],
                "entry_id": r["entry_id"],
                "chunk_index": r["chunk_index"],
                "page_number": r.get("page_number"),
                "preview": (r.get("content") or "")[:240],
                "entry_title": r.get("entry_title") or "",
                "score": r.get("score"),
            })

    total = len(entries) + len(studies) + len(notes) + len(highlights) + len(chunks)
    return {"query": query, "entries": entries, "studies": studies, "notes": notes,
            "highlights": highlights, "chunks": chunks, "total": total}
