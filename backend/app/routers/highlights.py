"""
Highlights router — text selections saved from the native PDF reader.

A highlight is the working layer between "reading a study" and "writing the
thesis": the quote keeps the exact wording with its page, an optional note
captures why it matters, and the chat and study-QA pipelines read highlights
as user-marked evidence with priority over raw chunks.
"""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db import get_db

router = APIRouter()


class HighlightCreate(BaseModel):
    quote: str = Field(..., min_length=1, max_length=4000)
    note: str = Field("", max_length=4000)
    page_number: Optional[int] = Field(None, ge=1)
    document_id: Optional[int] = None


class HighlightUpdate(BaseModel):
    note: str = Field(..., max_length=4000)


def _highlight_row(conn, highlight_id: int):
    row = conn.execute(
        """SELECT h.*, d.chunk_index AS document_chunk
             FROM pdf_highlights h
             LEFT JOIN documents d ON d.id = h.document_id
            WHERE h.id = ?""",
        [highlight_id],
    ).fetchone()
    return row


@router.get("/studies/{entry_id}/highlights")
def list_highlights(entry_id: int):
    conn = get_db()
    try:
        if not conn.execute("SELECT id FROM entries WHERE id = ?", [entry_id]).fetchone():
            raise HTTPException(404, "Entry not found")
        rows = conn.execute(
            """SELECT id, entry_id, document_id, page_number, quote, note, created_at
                 FROM pdf_highlights WHERE entry_id = ?
                ORDER BY COALESCE(page_number, 0), id""",
            [entry_id],
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/studies/{entry_id}/highlights", status_code=201)
def create_highlight(entry_id: int, data: HighlightCreate):
    conn = get_db()
    try:
        if not conn.execute("SELECT id FROM entries WHERE id = ?", [entry_id]).fetchone():
            raise HTTPException(404, "Entry not found")
        if data.document_id is not None:
            doc = conn.execute(
                "SELECT id FROM documents WHERE id = ? AND entry_id = ?",
                [data.document_id, entry_id],
            ).fetchone()
            if not doc:
                raise HTTPException(400, "document_id does not belong to this study")
        cur = conn.execute(
            """INSERT INTO pdf_highlights (entry_id, document_id, page_number, quote, note)
               VALUES (?, ?, ?, ?, ?)""",
            [entry_id, data.document_id, data.page_number, data.quote.strip(), data.note.strip()],
        )
        conn.commit()
        row = _highlight_row(conn, cur.lastrowid)
        return dict(row)
    finally:
        conn.close()


@router.put("/highlights/{highlight_id}")
def update_highlight(highlight_id: int, data: HighlightUpdate):
    conn = get_db()
    try:
        if not conn.execute("SELECT id FROM pdf_highlights WHERE id = ?", [highlight_id]).fetchone():
            raise HTTPException(404, "Highlight not found")
        conn.execute(
            "UPDATE pdf_highlights SET note = ? WHERE id = ?",
            [data.note.strip(), highlight_id],
        )
        conn.commit()
        row = _highlight_row(conn, highlight_id)
        return dict(row)
    finally:
        conn.close()


@router.delete("/highlights/{highlight_id}", status_code=204)
def delete_highlight(highlight_id: int):
    conn = get_db()
    try:
        cur = conn.execute("DELETE FROM pdf_highlights WHERE id = ?", [highlight_id])
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "Highlight not found")
    finally:
        conn.close()
