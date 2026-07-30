"""
Relationships (graph edges) router.
"""
from fastapi import APIRouter, HTTPException
from app.db import get_db
from app.models import RelationshipCreate, RelationshipResponse

router = APIRouter()


@router.get("/relationships", response_model=list[RelationshipResponse])
def list_relationships(entry_id: int | None = None):
    conn = get_db()
    try:
        if entry_id:
            rows = conn.execute(
                "SELECT * FROM relationships WHERE from_id = ? OR to_id = ? ORDER BY created_at",
                [entry_id, entry_id]
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM relationships ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/relationships/graph")
def get_graph_data():
    """Get full graph data (nodes + edges) for vis-network."""
    conn = get_db()
    try:
        entries = conn.execute(
            "SELECT id, title, tags, importance FROM entries WHERE archived = 0"
        ).fetchall()
        rels = conn.execute(
            "SELECT r.id, r.from_id, r.to_id, r.label FROM relationships r"
        ).fetchall()

        nodes = []
        for e in entries:
            d = dict(e)
            # Color by importance
            colors = {1: "#94a3b8", 2: "#6366f1", 3: "#f59e0b", 4: "#ef4444", 5: "#8b5cf6"}
            nodes.append({
                "id": d["id"],
                "label": d["title"][:50],
                "title": d["title"],
                "value": d["importance"],
                "color": colors.get(d["importance"], "#6366f1"),
                "group": d["tags"].split(",")[0] if d["tags"] else "default",
            })

        edges = []
        for r in rels:
            d = dict(r)
            edges.append({
                "id": d["id"],
                "from": d["from_id"],
                "to": d["to_id"],
                "label": d["label"] or "",
                "arrows": "to",
            })

        return {"nodes": nodes, "edges": edges}
    finally:
        conn.close()


@router.post("/relationships", response_model=RelationshipResponse, status_code=201)
def create_relationship(data: RelationshipCreate):
    if data.from_id == data.to_id:
        raise HTTPException(400, "Cannot create self-referential relationship")
    conn = get_db()
    try:
        # Verify both entries exist
        e1 = conn.execute("SELECT id FROM entries WHERE id = ?", [data.from_id]).fetchone()
        e2 = conn.execute("SELECT id FROM entries WHERE id = ?", [data.to_id]).fetchone()
        if not e1 or not e2:
            raise HTTPException(404, "One or both entries not found")

        # Check if relationship already exists
        existing = conn.execute(
            "SELECT id FROM relationships WHERE from_id = ? AND to_id = ? AND label = ?",
            [data.from_id, data.to_id, data.label]
        ).fetchone()
        if existing:
            raise HTTPException(409, "Relationship already exists")

        cur = conn.execute(
            "INSERT INTO relationships (from_id, to_id, label) VALUES (?, ?, ?)",
            [data.from_id, data.to_id, data.label]
        )
        conn.commit()
        row = conn.execute("SELECT * FROM relationships WHERE id = ?", [cur.lastrowid]).fetchone()
        return dict(row)
    finally:
        conn.close()


@router.delete("/relationships/{rel_id}", status_code=204)
def delete_relationship(rel_id: int):
    conn = get_db()
    try:
        cur = conn.execute("DELETE FROM relationships WHERE id = ?", [rel_id])
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "Relationship not found")
    finally:
        conn.close()
