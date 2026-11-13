"""
Highlights router — text selections saved from the native PDF reader.

A highlight is the working layer between "reading a study" and "writing the
thesis": the quote keeps the exact wording with its page, an optional note
captures why it matters, and the chat and study-QA pipelines read highlights
as user-marked evidence with priority over raw chunks.
"""
import re
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
    start_offset: Optional[int] = Field(None, ge=0)
    end_offset: Optional[int] = Field(None, ge=0)


class HighlightUpdate(BaseModel):
    note: str = Field(..., max_length=4000)
    start_offset: Optional[int] = Field(None, ge=0)
    end_offset: Optional[int] = Field(None, ge=0)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _highlight_row(conn, highlight_id: int):
    row = conn.execute(
        """SELECT h.*, d.chunk_index AS document_chunk
             FROM pdf_highlights h
             LEFT JOIN documents d ON d.id = h.document_id
            WHERE h.id = ?""",
        [highlight_id],
    ).fetchone()
    return row


def _validate_offsets_against_quote(
    conn, entry_id: int, page_number, quote: str, start_offset, end_offset
) -> str:
    """Validate offsets; return the (possibly filled) quote. Raises 422."""
    from app.page_text import get_page_text

    if page_number is None:
        raise HTTPException(
            422, "Pagina lipsește: offset-urile necesită page_number și text canonic."
        )
    page = get_page_text(conn, entry_id, page_number)
    if page is None or not (page.get("text") or ""):
        raise HTTPException(
            422, "Pagina nu are text canonic indexat pentru acest studiu."
        )
    text = page["text"]
    if end_offset <= start_offset:
        raise HTTPException(
            422,
            "Intervalul de offset-uri este invalid: "
            "end_offset trebuie să fie mai mare decât start_offset.",
        )
    if end_offset > len(text):
        raise HTTPException(
            422, "Intervalul depășește lungimea textului paginii."
        )
    stored = (quote or "").strip()
    if not stored:
        filled = text[start_offset:end_offset]
        if not filled.strip():
            raise HTTPException(
                422, "Fragmentul din text nu corespunde citatului (după normalizare)."
            )
        return filled
    if _norm(text[start_offset:end_offset]) != _norm(stored):
        raise HTTPException(
            422, "Fragmentul din text nu corespunde citatului (după normalizare)."
        )
    return quote


def _best_effort_offsets(conn, entry_id: int, page_number, quote):
    """Return (start, end) via locate_quote, or (None, None) on any failure."""
    try:
        if page_number is None or not (quote or "").strip():
            return (None, None)
        from app.page_text import locate_quote

        found = locate_quote(conn, entry_id, page_number, quote)
        if found is None:
            return (None, None)
        return found
    except Exception:
        return (None, None)


@router.get("/studies/{entry_id}/highlights")
def list_highlights(entry_id: int):
    conn = get_db()
    try:
        if not conn.execute("SELECT id FROM entries WHERE id = ?", [entry_id]).fetchone():
            raise HTTPException(404, "Entry not found")
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(pdf_highlights)").fetchall()}
        has_offsets = "start_offset" in cols and "end_offset" in cols
        if has_offsets:
            rows = conn.execute(
                """SELECT id, entry_id, document_id, page_number, quote, note,
                          start_offset, end_offset, created_at
                     FROM pdf_highlights WHERE entry_id = ?
                    ORDER BY COALESCE(page_number, 0), id""",
                [entry_id],
            ).fetchall()
        else:
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
        has_start = data.start_offset is not None
        has_end = data.end_offset is not None
        if has_start != has_end:
            raise HTTPException(
                422, "Trebuie specificate ambele offset-uri: start_offset și end_offset."
            )
        quote = data.quote.strip()
        start_offset = data.start_offset
        end_offset = data.end_offset
        if has_start and has_end:
            quote = _validate_offsets_against_quote(
                conn, entry_id, data.page_number, quote, start_offset, end_offset
            )
        else:
            found_start, found_end = _best_effort_offsets(
                conn, entry_id, data.page_number, quote
            )
            start_offset, end_offset = found_start, found_end
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(pdf_highlights)").fetchall()}
        has_offsets = "start_offset" in cols and "end_offset" in cols
        if has_offsets:
            cur = conn.execute(
                """INSERT INTO pdf_highlights
                   (entry_id, document_id, page_number, quote, note, start_offset, end_offset)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [entry_id, data.document_id, data.page_number, quote,
                 data.note.strip(), start_offset, end_offset],
            )
        else:
            cur = conn.execute(
                """INSERT INTO pdf_highlights (entry_id, document_id, page_number, quote, note)
                   VALUES (?, ?, ?, ?, ?)""",
                [entry_id, data.document_id, data.page_number, quote, data.note.strip()],
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
        existing = conn.execute(
            "SELECT * FROM pdf_highlights WHERE id = ?", [highlight_id]
        ).fetchone()
        if not existing:
            raise HTTPException(404, "Highlight not found")
        existing = dict(existing)
        has_start = data.start_offset is not None
        has_end = data.end_offset is not None
        if has_start != has_end:
            raise HTTPException(
                422, "Trebuie specificate ambele offset-uri: start_offset și end_offset."
            )
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(pdf_highlights)").fetchall()}
        has_offsets = "start_offset" in cols and "end_offset" in cols
        note = data.note.strip()
        if has_start and has_end:
            quote = _validate_offsets_against_quote(
                conn, existing["entry_id"], existing.get("page_number"),
                existing.get("quote") or "", data.start_offset, data.end_offset,
            )
            if has_offsets:
                if quote != (existing.get("quote") or ""):
                    conn.execute(
                        "UPDATE pdf_highlights SET note = ?, start_offset = ?, "
                        "end_offset = ?, quote = ? WHERE id = ?",
                        [note, data.start_offset, data.end_offset, quote, highlight_id],
                    )
                else:
                    conn.execute(
                        "UPDATE pdf_highlights SET note = ?, start_offset = ?, "
                        "end_offset = ? WHERE id = ?",
                        [note, data.start_offset, data.end_offset, highlight_id],
                    )
            else:
                conn.execute(
                    "UPDATE pdf_highlights SET note = ? WHERE id = ?",
                    [note, highlight_id],
                )
        else:
            # No offsets supplied: keep existing offsets, but fill them
            # best-effort when the row still has none.
            fill_start, fill_end = (None, None)
            if has_offsets and existing.get("start_offset") is None \
                    and existing.get("end_offset") is None:
                fill_start, fill_end = _best_effort_offsets(
                    conn, existing["entry_id"], existing.get("page_number"),
                    existing.get("quote") or "",
                )
            if has_offsets and fill_start is not None:
                conn.execute(
                    "UPDATE pdf_highlights SET note = ?, start_offset = ?, "
                    "end_offset = ? WHERE id = ?",
                    [note, fill_start, fill_end, highlight_id],
                )
            else:
                conn.execute(
                    "UPDATE pdf_highlights SET note = ? WHERE id = ?",
                    [note, highlight_id],
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
