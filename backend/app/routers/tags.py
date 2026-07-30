"""
Tags CRUD router.
"""
from fastapi import APIRouter, HTTPException
from app.db import get_db
from app.models import TagCreate, TagResponse

router = APIRouter()


@router.get("/tags", response_model=list[TagResponse])
def list_tags():
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM tags ORDER BY name").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/tags", response_model=TagResponse, status_code=201)
def create_tag(data: TagCreate):
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO tags (name, color) VALUES (?, ?)",
            [data.name, data.color]
        )
        conn.commit()
        row = conn.execute("SELECT * FROM tags WHERE name = ?", [data.name]).fetchone()
        return dict(row)
    finally:
        conn.close()


@router.delete("/tags/{tag_id}", status_code=204)
def delete_tag(tag_id: int):
    conn = get_db()
    try:
        cur = conn.execute("DELETE FROM tags WHERE id = ?", [tag_id])
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "Tag not found")
    finally:
        conn.close()
