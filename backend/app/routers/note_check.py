"""Verified notes API: check a note against the source PDF and keep the result.

A checked statement survives only with a citable quote, the printed page and
an explicit status; when the researcher misread the source, the correction
row records "what I noted" vs "what the text actually says".
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db import get_db
from app.note_check import NOTE_CHECK_STATUSES, check_note

router = APIRouter()


class NoteCheckPatch(BaseModel):
    status: str | None = None
    quote: str | None = Field(None, max_length=8000)
    correction: str | None = Field(None, max_length=8000)
    page_printed: str | None = Field(None, max_length=32)


def _row_dict(row) -> dict:
    try:
        return dict(row)
    except Exception:
        return {}


@router.post("/studies/{entry_id}/notes/{note_id}/check")
def check_study_note(entry_id: int, note_id: int, apply: bool = False):
    """Check every statement of a note; write rows only when ``apply=true``."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id FROM study_notes WHERE id = ? AND entry_id = ?",
            [note_id, entry_id],
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Nota nu aparține acestui studiu")
        return check_note(conn, note_id, apply=apply)
    finally:
        conn.close()


@router.get("/studies/{entry_id}/note-checks")
def list_note_checks(entry_id: int):
    """All checked statements of an entry, with their note text, by status."""
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT nc.id, nc.note_id, nc.entry_id, nc.statement, nc.status,
                      nc.page_pdf, nc.page_printed, nc.quote, nc.correction,
                      nc.method, nc.confidence, nc.created_at,
                      n.content AS note_content, n.course_context
                 FROM note_checks nc
                 JOIN study_notes n ON n.id = nc.note_id
                WHERE nc.entry_id = ?
                ORDER BY nc.note_id, nc.id""",
            [entry_id],
        ).fetchall()
        statements = [_row_dict(row) for row in rows]
        by_status: dict[str, list[dict]] = {status: [] for status in NOTE_CHECK_STATUSES}
        for item in statements:
            by_status.setdefault(item["status"], []).append(item)
        return {"statements": statements, "by_status": by_status}
    finally:
        conn.close()


@router.patch("/note-checks/{check_id}")
def patch_note_check(check_id: int, payload: NoteCheckPatch):
    """Human override of a checked statement; always recorded as ``manual``."""
    if payload.status is not None and payload.status not in NOTE_CHECK_STATUSES:
        raise HTTPException(
            422,
            "Status invalid. Acceptat: " + ", ".join(NOTE_CHECK_STATUSES),
        )
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id FROM note_checks WHERE id = ?", [check_id]
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Verificarea nu există")

        fields = ["method = 'manual'"]
        params: list = []
        for column, value in (
            ("status", payload.status),
            ("quote", payload.quote),
            ("correction", payload.correction),
            ("page_printed", payload.page_printed),
        ):
            if value is not None:
                fields.append(f"{column} = ?")
                params.append(value)
        params.append(check_id)
        conn.execute(
            f"UPDATE note_checks SET {', '.join(fields)} WHERE id = ?", params
        )
        conn.commit()
        updated = conn.execute(
            "SELECT * FROM note_checks WHERE id = ?", [check_id]
        ).fetchone()
        return _row_dict(updated)
    finally:
        conn.close()


@router.get("/studies/{entry_id}/verify/summary")
def verify_summary(entry_id: int):
    """How much of this source is actually verified, for the UI."""
    conn = get_db()
    try:
        by_status = {status: 0 for status in NOTE_CHECK_STATUSES}
        for row in conn.execute(
            """SELECT status, COUNT(*) AS n FROM note_checks
                WHERE entry_id = ? GROUP BY status""",
            [entry_id],
        ).fetchall():
            by_status[row["status"]] = row["n"]
        statements = sum(by_status.values())

        notes_total = conn.execute(
            "SELECT COUNT(*) AS n FROM study_notes WHERE entry_id = ?", [entry_id]
        ).fetchone()["n"]
        notes_fully_verified = conn.execute(
            """SELECT COUNT(*) AS n FROM (
                   SELECT nc.note_id
                     FROM note_checks nc
                    WHERE nc.entry_id = ?
                    GROUP BY nc.note_id
                   HAVING SUM(CASE WHEN nc.status IN
                              ('verificat','verificat_cu_nuanta') THEN 0 ELSE 1 END) = 0
               )""",
            [entry_id],
        ).fetchone()["n"]

        pages_total = conn.execute(
            "SELECT COUNT(*) AS n FROM page_texts WHERE entry_id = ?", [entry_id]
        ).fetchone()["n"]
        pages_with_printed = conn.execute(
            "SELECT COUNT(*) AS n FROM page_texts"
            " WHERE entry_id = ? AND printed_page != ''",
            [entry_id],
        ).fetchone()["n"]

        return {
            "statements": statements,
            "by_status": by_status,
            "notes_total": notes_total,
            "notes_fully_verified": notes_fully_verified,
            "pages_with_printed_numbers": pages_with_printed,
            "pages_total": pages_total,
        }
    finally:
        conn.close()
