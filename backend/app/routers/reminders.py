"""
Reminders router — ported from BrainBook.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from app.db import get_db
from app.models import ReminderCreate, ReminderResponse

router = APIRouter()

_RECURRENCE_TYPES = ("daily", "weekly", "monthly")


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        next_month = datetime(year + 1, 1, 1)
    else:
        next_month = datetime(year, month + 1, 1)
    return (next_month - datetime(year, month, 1)).days


def _next_future_occurrence(trigger_at: str, recurrence: str) -> str | None:
    """Return the first occurrence of a recurring reminder that is in the future.

    Jumps directly (no per-day iteration) so a reminder that is years overdue
    still schedules its next firing without hundreds of loop steps.
    """
    if recurrence not in _RECURRENCE_TYPES:
        return None
    try:
        value = datetime.fromisoformat(str(trigger_at).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    value = value.replace(tzinfo=None)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    if recurrence == "daily":
        delta = (now.date() - value.date()).days + 1
        next_value = value + timedelta(days=max(delta, 1))
    elif recurrence == "weekly":
        delta = max((now - value).days // 7 + 1, 1)
        next_value = value + timedelta(weeks=delta)
    else:  # monthly — manual month arithmetic (no dateutil dependency)
        # Advance by the number of complete months elapsed; the +1 overshoot
        # used for daily/weekly is WRONG here: it skips the current month's
        # occurrence whenever its day-of-month has not passed yet (e.g. due the
        # 20th, advanced on the 5th of the next month would jump straight to
        # the month after). Only bump when the clamped candidate is still past.
        delta = max((now.year - value.year) * 12 + (now.month - value.month), 1)
        month_index = value.year * 12 + (value.month - 1) + delta
        year = month_index // 12
        month = month_index % 12 + 1
        day = min(value.day, _days_in_month(year, month))
        next_value = value.replace(year=year, month=month, day=day)
        if next_value <= now:
            month_index += 1
            year = month_index // 12
            month = month_index % 12 + 1
            day = min(value.day, _days_in_month(year, month))
            next_value = value.replace(year=year, month=month, day=day)
    return next_value.strftime("%Y-%m-%dT%H:%M:%S")


def advance_overdue_recurring() -> int:
    """Advance dismissed recurring reminders that are already past due.

    Runs periodically from the app lifespan so a recurring reminder keeps
    firing even when the app was closed at the scheduled time. Returns the
    number of reminders advanced.
    """
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT id, trigger_at, recurrence FROM reminders
               WHERE notified = 1 AND recurrence IN ('daily', 'weekly', 'monthly')
                 AND julianday(trigger_at) <= julianday('now')"""
        ).fetchall()
        advanced = 0
        for row in rows:
            trigger = _next_future_occurrence(row["trigger_at"], row["recurrence"])
            if trigger is None:
                continue
            conn.execute(
                "UPDATE reminders SET trigger_at = ?, notified = 0 WHERE id = ?",
                [trigger, row["id"]],
            )
            advanced += 1
        if advanced:
            conn.commit()
        return advanced
    finally:
        conn.close()


@router.get("/entries/{entry_id}/reminders", response_model=list[ReminderResponse])
def list_reminders(entry_id: int):
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
def list_all_reminders(due: bool = False):
    """Get all reminders. If due=True, only get non-notified past reminders."""
    conn = get_db()
    try:
        if due:
            rows = conn.execute(
                "SELECT r.* FROM reminders r JOIN entries e ON r.entry_id = e.id WHERE r.notified = 0 AND julianday(r.trigger_at) <= julianday('now') AND e.archived = 0 ORDER BY r.trigger_at ASC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT r.* FROM reminders r JOIN entries e ON r.entry_id = e.id WHERE e.archived = 0 ORDER BY r.trigger_at ASC"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/entries/{entry_id}/reminders", response_model=ReminderResponse, status_code=201)
def create_reminder(entry_id: int, data: ReminderCreate):
    conn = get_db()
    try:
        entry = conn.execute("SELECT id FROM entries WHERE id = ?", [entry_id]).fetchone()
        if not entry:
            raise HTTPException(404, "Entry not found")

        # A daily/weekly/monthly reminder_type with no explicit recurrence is
        # treated as recurring — this is how the UI creates recurring reminders.
        recurrence = data.recurrence or (
            data.reminder_type if data.reminder_type in _RECURRENCE_TYPES else None
        )
        cur = conn.execute(
            "INSERT INTO reminders (entry_id, title, trigger_at, reminder_type, recurrence) VALUES (?, ?, ?, ?, ?)",
            [entry_id, data.title, data.trigger_at, data.reminder_type, recurrence]
        )
        conn.commit()
        row = conn.execute("SELECT * FROM reminders WHERE id = ?", [cur.lastrowid]).fetchone()
        return dict(row)
    finally:
        conn.close()


@router.post("/reminders/{reminder_id}/notify", status_code=204)
def mark_notified(reminder_id: int):
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT id, trigger_at, recurrence FROM reminders WHERE id = ?", [reminder_id]
        ).fetchone()
        if not existing:
            raise HTTPException(404, "Reminder not found")
        next_at = _next_future_occurrence(
            existing["trigger_at"], existing["recurrence"] or ""
        )
        if next_at:
            # Recurring reminder: schedule the next occurrence instead of
            # deactivating the reminder entirely.
            conn.execute(
                "UPDATE reminders SET trigger_at = ?, notified = 0 WHERE id = ?",
                [next_at, reminder_id],
            )
        else:
            conn.execute("UPDATE reminders SET notified = 1 WHERE id = ?", [reminder_id])
        conn.commit()
    finally:
        conn.close()


@router.delete("/entries/{entry_id}/reminders/{reminder_id}", status_code=204)
def delete_reminder(entry_id: int, reminder_id: int):
    conn = get_db()
    try:
        cur = conn.execute("DELETE FROM reminders WHERE id = ? AND entry_id = ?", [reminder_id, entry_id])
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "Reminder not found")
    finally:
        conn.close()
