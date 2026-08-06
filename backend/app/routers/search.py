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
                    where_extra.append("(',' || e.tags || ',') LIKE '%,' || ? || ',%'")
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
            "SELECT id, title FROM entries WHERE title LIKE ? LIMIT ?",
            [f"%{q}%", limit]
        ).fetchall()
        return [{"id": r["id"], "title": r["title"]} for r in rows]
    finally:
        conn.close()


@router.get("/search/suggest")
def suggest(q: str = Query(..., min_length=1), limit: int = Query(5, le=20)):
    return _execute_suggest(q, limit)
