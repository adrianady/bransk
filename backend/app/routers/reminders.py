"""
Reminders router — ported from BrainBook.
"""
from fastapi import APIRouter, HTTPException
from app.db import get_db
from app.models import ReminderCreate, ReminderResponse

router = APIRouter()


@router.get("/entries/{entry_id}/reminders", response_model=list[ReminderResponse])
async def list_reminders(entry_id: int):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM reminders WHERE entry_id = ? ORDER BY trigger_at ASC",
            [entry_id]
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/reminders", response_model=list[ReminderResponse])
async def list_all_reminders(due: bool = False):
    """Get all reminders. If due=True, only get non-notified past reminders."""
    conn = get_db()
    try:
        if due:
            rows = conn.execute(
                "SELECT r.* FROM reminders r JOIN entries e ON r.entry_id = e.id WHERE r.notified = 0 AND r.trigger_at <= datetime('now') AND e.archived = 0 ORDER BY r.trigger_at ASC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT r.* FROM reminders r JOIN entries e ON r.entry_id = e.id WHERE e.archived = 0 ORDER BY r.trigger_at ASC"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/entries/{entry_id}/reminders", response_model=ReminderResponse, status_code=201)
async def create_reminder(entry_id: int, data: ReminderCreate):
    conn = get_db()
    try:
        entry = conn.execute("SELECT id FROM entries WHERE id = ?", [entry_id]).fetchone()
        if not entry:
            raise HTTPException(404, "Entry not found")

        cur = conn.execute(
            "INSERT INTO reminders (entry_id, title, trigger_at, reminder_type, recurrence) VALUES (?, ?, ?, ?, ?)",
            [entry_id, data.title, data.trigger_at, data.reminder_type, data.recurrence]
        )
        conn.commit()
        row = conn.execute("SELECT * FROM reminders WHERE id = ?", [cur.lastrowid]).fetchone()
        return dict(row)
    finally:
        conn.close()


@router.post("/reminders/{reminder_id}/notify", status_code=204)
async def mark_notified(reminder_id: int):
    conn = get_db()
    try:
        existing = conn.execute("SELECT id FROM reminders WHERE id = ?", [reminder_id]).fetchone()
        if not existing:
            raise HTTPException(404, "Reminder not found")
        conn.execute("UPDATE reminders SET notified = 1 WHERE id = ?", [reminder_id])
        conn.commit()
    finally:
        conn.close()


@router.delete("/entries/{entry_id}/reminders/{reminder_id}", status_code=204)
async def delete_reminder(entry_id: int, reminder_id: int):
    conn = get_db()
    try:
        cur = conn.execute("DELETE FROM reminders WHERE id = ? AND entry_id = ?", [reminder_id, entry_id])
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "Reminder not found")
    finally:
        conn.close()
