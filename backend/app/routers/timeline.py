"""
Timeline router — date-range queries for the horizontal timeline view.
"""
from typing import Optional
from fastapi import APIRouter, Query
from app.db import get_db
from app.models import TimelineResponse, TimelineEntry

router = APIRouter()


@router.get("/timeline", response_model=TimelineResponse)
def get_timeline(
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    tags: Optional[str] = Query(None),
    limit: int = Query(500, le=2000),
    offset: int = Query(0, ge=0),
):
    conn = get_db()
    try:
        # Only ISO YYYY-MM-DD dates belong on the timeline — free-text values
        # (e.g. a description pasted into the date field) would break both the
        # chronological ordering and the vis-timeline parser. Undated entries
        # (NULL) are kept: the UI lists them below the chart and the chart
        # itself skips them.
        where = [
            "e.archived = 0",
            "(e.entry_date IS NULL OR e.entry_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]')",
        ]
        params: list = []

        if from_date:
            where.append("e.entry_date >= ?")
            params.append(from_date)
        if to_date:
            where.append("e.entry_date <= ?")
            params.append(to_date)
        if tags:
            for t in tags.split(","):
                t = t.strip()
                if t:
                    # Exact token match on comma boundaries, not substring.
                    where.append("(',' || e.tags || ',') LIKE '%,' || ? || ',%'")
                    params.append(t)

        where_sql = " AND ".join(where)

        # Count total
        count_row = conn.execute(
            f"SELECT COUNT(*) as cnt FROM entries e WHERE {where_sql}", params
        ).fetchone()
        total = count_row["cnt"]

        # Get entries with relationship counts
        rows = conn.execute(
            f"""SELECT e.id, e.title, e.entry_date, e.tags, e.importance, e.content,
                       (SELECT COUNT(*) FROM relationships r WHERE r.from_id = e.id OR r.to_id = e.id) as relationship_count
                FROM entries e
                WHERE {where_sql}
                ORDER BY e.entry_date IS NULL, e.entry_date ASC, e.created_at ASC
                LIMIT ? OFFSET ?""",
            [*params, limit, offset]
        ).fetchall()

        entries = []
        for r in rows:
            d = dict(r)
            content = d.pop("content", "") or ""
            d["content_preview"] = content[:300]
            entries.append(TimelineEntry(**d))
        return TimelineResponse(
            entries=entries,
            total=total,
            from_date=from_date,
            to_date=to_date,
        )
    finally:
        conn.close()


@router.get("/timeline/stats")
def timeline_stats():
    """Get timeline date range and counts for the UI."""
    conn = get_db()
    try:
        row = conn.execute(
            """SELECT MIN(entry_date) as earliest, MAX(entry_date) as latest, COUNT(*) as total
               FROM entries
               WHERE archived = 0
                 AND entry_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'"""
        ).fetchone()
        return dict(row) if row else {"earliest": None, "latest": None, "total": 0}
    finally:
        conn.close()
