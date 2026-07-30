"""
Full-text search and hybrid search router.
"""
import re
from typing import Optional
from fastapi import APIRouter, Query
from app.db import get_db
from app.models import SearchResult, EntryResponse

router = APIRouter()


def _sanitize_fts5(query: str) -> str:
    """Escape FTS5 special characters to prevent syntax errors."""
    # Remove FTS5 special characters: ^ * " ( ) : ~ + -
    sanitized = re.sub(r'[\^\"\*\(\)\:\~\+\-]', ' ', query)
    # Collapse multiple spaces
    sanitized = re.sub(r'\s+', ' ', sanitized).strip()
    return sanitized if sanitized else 'invalid'


@router.get("/search", response_model=list[SearchResult])
async def search(
    q: str = Query(..., min_length=1),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    tags: Optional[str] = Query(None),
    limit: int = Query(20, le=100),
):
    conn = get_db()
    try:
        # FTS5 search with optional date/tag filters
        safe_query = _sanitize_fts5(q)
        params: list = [safe_query]
        where_extra = []

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
                    where_extra.append("e.tags LIKE ?")
                    params.append(f"%{t}%")

        extra_sql = f" AND {' AND '.join(where_extra)}" if where_extra else ""

        rows = conn.execute(
            f"""SELECT e.*, rank FROM entries_fts
                JOIN entries e ON entries_fts.rowid = e.id
                WHERE entries_fts MATCH ?{extra_sql}
                ORDER BY rank
                LIMIT ?""",
            [*params, limit]
        ).fetchall()

        results = []
        for r in rows:
            d = dict(r)
            rank = d.pop("rank", 0)
            results.append(SearchResult(entry=EntryResponse(**d), rank=float(rank)))
        return results
    finally:
        conn.close()


@router.get("/search/suggest")
async def suggest(q: str = Query(..., min_length=1), limit: int = Query(5, le=20)):
    """Auto-complete/suggest from entry titles."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, title FROM entries WHERE title LIKE ? LIMIT ?",
            [f"%{q}%", limit]
        ).fetchall()
        return [{"id": r["id"], "title": r["title"]} for r in rows]
    finally:
        conn.close()
