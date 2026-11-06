"""
Tags CRUD router.
"""
from fastapi import APIRouter, HTTPException
from app.db import get_db
from app.models import TagCreate, TagResponse
from app.tagging import normalize_tags

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
        row = conn.execute("SELECT name FROM tags WHERE id = ?", [tag_id]).fetchone()
        if not row:
            raise HTTPException(404, "Tag not found")
        name = row["name"]

        # Removing the tag row alone would leave the tag stringed into
        # entries.tags forever; strip it from every entry that carries it.
        affected = conn.execute(
            "SELECT id, tags FROM entries WHERE (',' || REPLACE(tags, ', ', ',') || ',') LIKE '%,' || ? || ',%'",
            [name],
        ).fetchall()
        for entry in affected:
            kept = [
                t.strip()
                for t in (entry["tags"] or "").split(",")
                if t.strip() and t.strip().casefold() != name.casefold()
            ]
            conn.execute(
                "UPDATE entries SET tags = ?, updated_at = datetime('now') WHERE id = ?",
                [normalize_tags(kept), entry["id"]],
            )

        conn.execute("DELETE FROM tags WHERE id = ?", [tag_id])
        conn.commit()
    finally:
        conn.close()
