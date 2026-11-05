"""
Entries CRUD router.
"""
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from app.db import get_db
from app.models import EntryCreate, EntryUpdate, EntryResponse
from app.tagging import normalize_tags, sync_tags

router = APIRouter()


@router.get("/entries", response_model=list[EntryResponse])
def list_entries(
    archived: bool = False,
    tag: Optional[str] = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
):
    conn = get_db()
    try:
        where = ["archived = ?"]
        params: list = [1 if archived else 0]
        if tag:
            # Exact token match on comma boundaries ('%ann%' would also match
            # 'annual'; this only matches the whole comma-separated tag).
            # Tags may be stored as "a, b" or "a,b"; normalize the separator so
            # the second tag is findable either way.
            where.append("(',' || REPLACE(tags, ', ', ',') || ',') LIKE '%,' || ? || ',%'")
            params.append(tag)
        rows = conn.execute(
            f"SELECT * FROM entries WHERE {' AND '.join(where)} ORDER BY entry_date IS NULL, entry_date DESC, created_at DESC LIMIT ? OFFSET ?",
            [*params, limit, offset]
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/entries/{entry_id}", response_model=EntryResponse)
def get_entry(entry_id: int):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM entries WHERE id = ?", [entry_id]).fetchone()
        if not row:
            raise HTTPException(404, "Entry not found")
        return dict(row)
    finally:
        conn.close()


@router.post("/entries", response_model=EntryResponse, status_code=201)
def create_entry(data: EntryCreate):
    conn = get_db()
    try:
        # Normalize at the boundary: the UI, the chat actions and the research
        # pipeline all write here, and none of them should be able to add a
        # date-as-tag or a duplicate spelling to the vocabulary.
        tags = normalize_tags(data.tags)
        cur = conn.execute(
            """INSERT INTO entries (title, content, entry_date, tags, source, importance,
                                    study_type, authors, quality, url_validated, design, effect_direction, pdf_path, doi)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                data.title,
                data.content,
                data.entry_date or None,
                tags,
                data.source,
                data.importance,
                data.study_type,
                data.authors,
                data.quality,
                1 if data.url_validated else 0,
                data.design,
                data.effect_direction,
                data.pdf_path,
                data.doi,
            ]
        )
        sync_tags(conn, tags)
        conn.commit()
        row = conn.execute("SELECT * FROM entries WHERE id = ?", [cur.lastrowid]).fetchone()
        return dict(row)
    finally:
        conn.close()


@router.put("/entries/{entry_id}", response_model=EntryResponse)
def update_entry(entry_id: int, data: EntryUpdate):
    conn = get_db()
    try:
        existing = conn.execute("SELECT * FROM entries WHERE id = ?", [entry_id]).fetchone()
        if not existing:
            raise HTTPException(404, "Entry not found")

        updates = {}
        # `doi_checked_at` is set through SQL, not as a bound value.
        raw_updates: list[str] = []
        for field in ("title", "content", "entry_date", "tags", "source", "importance",
                      "study_type", "authors", "quality", "url_validated", "design",
                      "effect_direction", "pdf_path", "doi"):
            val = getattr(data, field, None)
            if val is not None:
                if field == "url_validated":
                    updates[field] = 1 if val else 0
                elif field == "tags":
                    updates[field] = normalize_tags(val)
                elif field == "doi":
                    # An explicit empty string clears the DOI; anything else was
                    # already validated by the model.
                    updates[field] = val
                    if val:
                        raw_updates += ["doi_checked_at = datetime('now')", "doi_source = 'manual'"]
                else:
                    updates[field] = val
        if data.archived is not None:
            updates["archived"] = 1 if data.archived else 0

        if not updates and not raw_updates:
            return dict(existing)

        set_parts = [f"{k} = ?" for k in updates] + raw_updates + ["updated_at = datetime('now')"]
        conn.execute(
            f"UPDATE entries SET {', '.join(set_parts)} WHERE id = ?",
            [*updates.values(), entry_id],
        )
        if "tags" in updates:
            sync_tags(conn, updates["tags"])
        conn.commit()

        row = conn.execute("SELECT * FROM entries WHERE id = ?", [entry_id]).fetchone()
        return dict(row)
    finally:
        conn.close()


@router.delete("/entries/{entry_id}", status_code=204)
def delete_entry(entry_id: int):
    conn = get_db()
    try:
        cur = conn.execute("DELETE FROM entries WHERE id = ?", [entry_id])
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "Entry not found")
    finally:
        conn.close()
