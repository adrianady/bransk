"""
Thoughts router — tag-based dynamic collections ported from BrainBook.
"""
from fastapi import APIRouter, HTTPException
from app.db import get_db
from app.models import ThoughtCreate, ThoughtResponse

router = APIRouter()


@router.get("/thoughts", response_model=list[ThoughtResponse])
def list_thoughts():
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM thoughts ORDER BY name").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/thoughts", response_model=ThoughtResponse, status_code=201)
def create_thought(data: ThoughtCreate):
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO thoughts (name, description, tag_ids) VALUES (?, ?, ?)",
            [data.name, data.description, data.tag_ids]
        )
        conn.commit()
        row = conn.execute("SELECT * FROM thoughts WHERE id = ?", [cur.lastrowid]).fetchone()
        return dict(row)
    finally:
        conn.close()


@router.get("/thoughts/{thought_id}", response_model=ThoughtResponse)
def get_thought(thought_id: int):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM thoughts WHERE id = ?", [thought_id]).fetchone()
        if not row:
            raise HTTPException(404, "Thought not found")
        return dict(row)
    finally:
        conn.close()


@router.put("/thoughts/{thought_id}", response_model=ThoughtResponse)
def update_thought(thought_id: int, data: ThoughtCreate):
    conn = get_db()
    try:
        existing = conn.execute("SELECT * FROM thoughts WHERE id = ?", [thought_id]).fetchone()
        if not existing:
            raise HTTPException(404, "Thought not found")

        conn.execute(
            "UPDATE thoughts SET name = ?, description = ?, tag_ids = ? WHERE id = ?",
            [data.name, data.description, data.tag_ids, thought_id]
        )
        conn.commit()
        row = conn.execute("SELECT * FROM thoughts WHERE id = ?", [thought_id]).fetchone()
        return dict(row)
    finally:
        conn.close()


@router.delete("/thoughts/{thought_id}", status_code=204)
def delete_thought(thought_id: int):
    conn = get_db()
    try:
        cur = conn.execute("DELETE FROM thoughts WHERE id = ?", [thought_id])
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "Thought not found")
    finally:
        conn.close()


@router.get("/thoughts/{thought_id}/entries", response_model=list[dict])
def get_thought_entries(thought_id: int):
    """Get entries matching a thought's tag criteria."""
    conn = get_db()
    try:
        thought = conn.execute("SELECT * FROM thoughts WHERE id = ?", [thought_id]).fetchone()
        if not thought:
            raise HTTPException(404, "Thought not found")

        tag_ids = [t.strip() for t in thought["tag_ids"].split(",") if t.strip()]
        if not tag_ids:
            return []

        # Get tag names
        placeholders = ",".join("?" for _ in tag_ids)
        tags = conn.execute(f"SELECT name FROM tags WHERE id IN ({placeholders})", tag_ids).fetchall()
        tag_names = [t["name"] for t in tags]

        # Find entries with any of these tags
        if not tag_names:
            return []

        # Tags may be stored as "a, b" or "a,b"; normalize the separator so a
        # tag in the second position still matches (same expression as entries.py).
        like_clauses = " OR ".join(
            "(',' || REPLACE(tags, ', ', ',') || ',') LIKE '%,' || ? || ',%'"
            for _ in tag_names
        )
        params = list(tag_names)
        rows = conn.execute(
            f"SELECT id, title, entry_date, tags, importance FROM entries WHERE archived = 0 AND ({like_clauses}) ORDER BY entry_date DESC LIMIT 100",
            params
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
