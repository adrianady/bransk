"""
Todos router — task management ported from BrainBook.
"""
from fastapi import APIRouter, HTTPException
from app.db import get_db
from app.models import TodoCreate, TodoUpdate, TodoResponse

router = APIRouter()


@router.get("/todos", response_model=list[TodoResponse])
def list_todos(completed: bool | None = None, priority: str | None = None):
    conn = get_db()
    try:
        where = ["1=1"]
        params = []
        if completed is not None:
            where.append("t.completed = ?")
            params.append(1 if completed else 0)
        if priority:
            where.append("t.priority = ?")
            params.append(priority)

        rows = conn.execute(
            f"SELECT t.*, e.title AS entry_title, substr(e.content, 1, 300) AS content_preview FROM todos t JOIN entries e ON t.entry_id = e.id WHERE {' AND '.join(where)} AND e.archived = 0 ORDER BY t.due_date IS NULL, t.due_date ASC",
            params
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/entries/{entry_id}/todo", response_model=TodoResponse, status_code=201)
def create_todo(entry_id: int, data: TodoCreate):
    conn = get_db()
    try:
        entry = conn.execute("SELECT id FROM entries WHERE id = ?", [entry_id]).fetchone()
        if not entry:
            raise HTTPException(404, "Entry not found")

        # Check if todo already exists for this entry
        existing = conn.execute("SELECT id FROM todos WHERE entry_id = ?", [entry_id]).fetchone()
        if existing:
            raise HTTPException(409, "Entry already has a todo")

        cur = conn.execute(
            "INSERT INTO todos (entry_id, due_date, priority, effort) VALUES (?, ?, ?, ?)",
            [entry_id, data.due_date or None, data.priority, data.effort]
        )
        conn.commit()
        row = conn.execute("SELECT * FROM todos WHERE entry_id = ?", [entry_id]).fetchone()
        return dict(row)
    finally:
        conn.close()


@router.patch("/entries/{entry_id}/todo", response_model=TodoResponse)
def update_todo(entry_id: int, data: TodoUpdate):
    conn = get_db()
    try:
        existing = conn.execute("SELECT * FROM todos WHERE entry_id = ?", [entry_id]).fetchone()
        if not existing:
            raise HTTPException(404, "Todo not found")

        updates = {}
        if data.due_date is not None:
            updates["due_date"] = data.due_date
        if data.completed is not None:
            updates["completed"] = 1 if data.completed else 0
        if data.priority is not None:
            updates["priority"] = data.priority
        if data.effort is not None:
            updates["effort"] = data.effort

        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(f"UPDATE todos SET {set_clause} WHERE entry_id = ?", [*updates.values(), entry_id])
            conn.commit()

        row = conn.execute("SELECT * FROM todos WHERE entry_id = ?", [entry_id]).fetchone()
        return dict(row)
    finally:
        conn.close()


@router.delete("/entries/{entry_id}/todo", status_code=204)
def delete_todo(entry_id: int):
    conn = get_db()
    try:
        cur = conn.execute("DELETE FROM todos WHERE entry_id = ?", [entry_id])
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "Todo not found")
    finally:
        conn.close()
