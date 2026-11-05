"""
Timeline router — date-range queries for the horizontal timeline view.

The stored dates span more than sixteen centuries (0350 → 2026), and most of
the entries sit in the last century. A single linear axis therefore squeezes
the part that matters into a few pixels, so this router also serves a density
view (`/timeline/buckets`) the UI can draw as a clickable overview.
"""
from typing import Optional
from fastapi import APIRouter, Query
from app.db import get_db
from app.models import TimelineResponse, TimelineEntry

router = APIRouter()

# Only ISO dates belong on the axis; free text would break ordering.
_ISO_PATTERN = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]"
_ISO_LIKE = "(e.entry_date IS NULL OR e.entry_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]')"


def _fold(text: str) -> str:
    """Lowercase and strip Romanian diacritics for a forgiving search."""
    table = str.maketrans("ăâîșțĂÂÎȘȚ", "aaistAAIST")
    return text.translate(table).lower()


def _where(params: list, from_date, to_date, tags, q) -> str:
    where = ["e.archived = 0", _ISO_LIKE]
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
                # Tolerate both "a, b" and "a,b" storage formats.
                where.append(
                    "(',' || REPLACE(e.tags, ', ', ',') || ',') LIKE '%,' || ? || ',%'"
                )
                params.append(t)
    if q and q.strip():
        needle = "%" + q.strip().lower() + "%"
        # LIKE is case-insensitive for ASCII only, so fold the needle and
        # match the raw text too; the folded comparison happens after the
        # SQL pre-filter in Python (`_matches`).
        where.append("(LOWER(COALESCE(e.title, '')) LIKE ? OR LOWER(COALESCE(e.content, '')) LIKE ?"
                     " OR LOWER(COALESCE(e.title, '')) LIKE ? OR LOWER(COALESCE(e.content, '')) LIKE ?)")
        params.extend([needle, needle, "%" + _fold(q.strip()) + "%", "%" + _fold(q.strip()) + "%"])
    return " AND ".join(where)


def _matches(row: dict, q: Optional[str]) -> bool:
    if not q or not q.strip():
        return True
    needle = _fold(q.strip())
    haystack = _fold((row.get("title") or "") + " " + (row.get("content") or ""))
    return needle in haystack


@router.get("/timeline", response_model=TimelineResponse)
def get_timeline(
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    tags: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="Substring în titlu sau conținut"),
    limit: int = Query(500, le=2000),
    offset: int = Query(0, ge=0),
):
    conn = get_db()
    try:
        params: list = []
        where_sql = _where(params, from_date, to_date, tags, q)

        count_row = conn.execute(
            f"SELECT COUNT(*) as cnt FROM entries e WHERE {where_sql}", params
        ).fetchone()
        total = count_row["cnt"]

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
            if not _matches(d, q):
                continue
            content = d.pop("content", "") or ""
            d["content_preview"] = content[:300]
            d["has_relationships"] = (d.get("relationship_count") or 0) > 0
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
            f"""SELECT MIN(entry_date) as earliest, MAX(entry_date) as latest, COUNT(*) as total
               FROM entries e
               WHERE archived = 0 AND entry_date GLOB '{_ISO_PATTERN}'"""
        ).fetchone()
        return dict(row) if row else {"earliest": None, "latest": None, "total": 0}
    finally:
        conn.close()


# How many buckets a density strip may return; centuries only need a handful,
# years over a 1676-year span would be 1676 empty bars.
_BUCKET_LIMIT = 300


@router.get("/timeline/buckets")
def timeline_buckets(
    bucket: str = Query("century", pattern="^(century|decade|year)$"),
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    tags: Optional[str] = None,
):
    """Entry counts per period, for the density strip above the timeline.

    Returns only non-empty buckets plus the range, so the UI can size each bar
    against the largest one and jump straight to a crowded era instead of
    zooming by hand across sixteen centuries.
    """
    conn = get_db()
    try:
        params: list = []
        where_sql = _where(params, from_date, to_date, tags, None)
        rows = conn.execute(
            f"""SELECT e.entry_date, e.tags, e.importance
                FROM entries e
                WHERE {where_sql} AND e.entry_date GLOB '{_ISO_PATTERN}'""",
            params
        ).fetchall()

        if bucket == "year":
            key = lambda d: d[:4]
        elif bucket == "decade":
            key = lambda d: d[:3] + "0"
        else:
            # Century of a year: 1–100 → "Sec. I", 1901–2000 → "Sec. XX".
            def key(d):
                year = int(d[:4])
                if year <= 0:
                    return "sec. av. Hr."
                return f"{((year - 1) // 100) + 1}"

        buckets: dict[str, dict] = {}
        for r in rows:
            d = r["entry_date"]
            if not d or len(d) < 4:
                continue
            k = key(d)
            b = buckets.setdefault(k, {"key": k, "count": 0, "min": d, "max": d,
                                       "importance_sum": 0, "tags": {}})
            b["count"] += 1
            b["min"] = min(b["min"], d)
            b["max"] = max(b["max"], d)
            b["importance_sum"] += r["importance"] or 0
            for t in (r["tags"] or "").split(","):
                t = t.strip()
                if t and not t.startswith(("19", "20", "18")) and len(t) > 4:
                    b["tags"][t] = b["tags"].get(t, 0) + 1

        out = []
        for b in buckets.values():
            top = sorted(b["tags"].items(), key=lambda kv: -kv[1])[:3]
            out.append({
                "key": b["key"],
                "count": b["count"],
                "start": b["min"],
                "end": b["max"],
                "top_tags": [t for t, _ in top],
            })

        # Century buckets sort numerically ("3" before "12" is wrong).
        if bucket == "century":
            out.sort(key=lambda b: int(b["key"]) if b["key"].isdigit() else -10**6)
        else:
            out.sort(key=lambda b: b["start"])

        if len(out) > _BUCKET_LIMIT:
            out = out[-_BUCKET_LIMIT:]
        return {
            "bucket": bucket,
            "total": sum(b["count"] for b in out),
            "max_count": max((b["count"] for b in out), default=0),
            "buckets": out,
        }
    finally:
        conn.close()
