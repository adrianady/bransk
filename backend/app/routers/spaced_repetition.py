"""
Spaced Repetition router — SM-2 algorithm ported from BrainBook.
"""
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException
from app.db import get_db
from app.models import SpacedRepetitionItem, ReviewSubmit

router = APIRouter()


def _sm2_calc(quality: int, ease_factor: float, interval: int, repetitions: int) -> tuple:
    """
    SM-2 algorithm as used by Anki / BrainBook.
    Returns (new_ease_factor, new_interval, new_repetitions).
    """
    if quality < 3:
        # Failed — reset
        return ease_factor, 1, 0

    if repetitions == 0:
        new_interval = 1
    elif repetitions == 1:
        new_interval = 6
    else:
        new_interval = round(interval * ease_factor)

    new_ease = ease_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    if new_ease < 1.3:
        new_ease = 1.3

    return new_ease, new_interval, repetitions + 1


@router.get("/spaced-repetition/items", response_model=list[SpacedRepetitionItem])
async def list_items():
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM spaced_repetition ORDER BY next_review IS NULL, next_review ASC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/spaced-repetition/queue", response_model=list[SpacedRepetitionItem])
async def get_review_queue():
    """Get items due for review."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM spaced_repetition WHERE next_review IS NULL OR next_review <= datetime('now') ORDER BY next_review ASC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/spaced-repetition/items/{entry_id}", response_model=SpacedRepetitionItem)
async def add_item(entry_id: int):
    conn = get_db()
    try:
        entry = conn.execute("SELECT id FROM entries WHERE id = ?", [entry_id]).fetchone()
        if not entry:
            raise HTTPException(404, "Entry not found")

        # Check if item already exists
        existing = conn.execute("SELECT id FROM spaced_repetition WHERE entry_id = ?", [entry_id]).fetchone()
        if existing:
            raise HTTPException(409, "Entry already has a spaced repetition item")

        cur = conn.execute(
            "INSERT INTO spaced_repetition (entry_id) VALUES (?)",
            [entry_id]
        )
        conn.commit()
        row = conn.execute("SELECT * FROM spaced_repetition WHERE entry_id = ?", [entry_id]).fetchone()
        return dict(row)
    finally:
        conn.close()


@router.delete("/spaced-repetition/items/{entry_id}", status_code=204)
async def remove_item(entry_id: int):
    conn = get_db()
    try:
        cur = conn.execute("DELETE FROM spaced_repetition WHERE entry_id = ?", [entry_id])
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "Item not found")
    finally:
        conn.close()


@router.post("/spaced-repetition/items/{item_id}/review", response_model=SpacedRepetitionItem)
async def submit_review(item_id: int, data: ReviewSubmit):
    conn = get_db()
    try:
        item = conn.execute("SELECT * FROM spaced_repetition WHERE id = ?", [item_id]).fetchone()
        if not item:
            raise HTTPException(404, "Item not found")

        ef, interval, reps = _sm2_calc(
            data.quality, item["ease_factor"], item["interval"], item["repetitions"]
        )
        next_review = (datetime.now(timezone.utc) + timedelta(days=interval)).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Record review
        conn.execute(
            "INSERT INTO review_history (item_id, quality) VALUES (?, ?)",
            [item_id, data.quality]
        )
        # Update item
        conn.execute(
            "UPDATE spaced_repetition SET ease_factor = ?, interval = ?, repetitions = ?, next_review = ? WHERE id = ?",
            [ef, interval, reps, next_review, item_id]
        )
        conn.commit()

        row = conn.execute("SELECT * FROM spaced_repetition WHERE id = ?", [item_id]).fetchone()
        return dict(row)
    finally:
        conn.close()
