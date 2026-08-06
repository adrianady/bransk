"""
Entries CRUD router.
"""
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from app.db import get_db
from app.models import EntryCreate, EntryUpdate, EntryResponse

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
            where.append("(',' || tags || ',') LIKE '%,' || ? || ',%'")
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
        cur = conn.execute(
            """INSERT INTO entries (title, content, entry_date, tags, source, importance,
                                    study_type, authors, quality, url_validated, design, effect_direction, pdf_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                data.title,
                data.content,
                data.entry_date or None,
                data.tags,
                data.source,
                data.importance,
                data.study_type,
                data.authors,
                data.quality,
                1 if data.url_validated else 0,
                data.design,
                data.effect_direction,
                data.pdf_path,
            ]
        )
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
        for field in ("title", "content", "entry_date", "tags", "source", "importance",
                      "study_type", "authors", "quality", "url_validated", "design", "effect_direction", "pdf_path"):
            val = getattr(data, field, None)
            if val is not None:
                updates[field] = 1 if field == "url_validated" and val else val
        if data.archived is not None:
            updates["archived"] = 1 if data.archived else 0

        if not updates:
            return dict(existing)

        updates["updated_at"] = "datetime('now')"
        set_clause = ", ".join(f"{k} = ?" for k in updates if k != "updated_at")
        set_clause += ", updated_at = datetime('now')"
        values = [v for k, v in updates.items() if k != "updated_at"]

        conn.execute(f"UPDATE entries SET {set_clause} WHERE id = ?", [*values, entry_id])
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
