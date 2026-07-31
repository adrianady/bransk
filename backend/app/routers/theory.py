"""
Theory Aggregator router — hierarchical theories with pro/contra study evidence.

A theory is a node in a tree (a theory can derive into several sub-theories).
Studies (entries) are attached to theories as PRO or CONTRA evidence, which
drives the tree visualization and the evidence panels.
"""
from fastapi import APIRouter, HTTPException

from app.db import get_db
from app.models import Theory, TheoryCreate, TheoryNode, TheoryUpdate, TheoryEvidence, TheoryEvidenceIn

router = APIRouter()


def _fetch_theory(conn, theory_id: int) -> dict:
    row = conn.execute("SELECT * FROM theories WHERE id = ?", [theory_id]).fetchone()
    if not row:
        raise HTTPException(404, "Theory not found")
    return dict(row)


@router.get("/theories", response_model=list[Theory])
def list_theories():
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM theories ORDER BY name").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/theories/tree", response_model=list[TheoryNode])
def get_theory_tree():
    """Nested theory tree with pro/contra evidence counts."""
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT t.*,
                      (SELECT COUNT(*) FROM theory_evidence te WHERE te.theory_id = t.id AND te.stance = 'pro') AS pro_count,
                      (SELECT COUNT(*) FROM theory_evidence te WHERE te.theory_id = t.id AND te.stance = 'contra') AS contra_count
               FROM theories t
               ORDER BY t.name"""
        ).fetchall()
        nodes = {r["id"]: TheoryNode(**dict(r)) for r in rows}
        roots = []
        for node in nodes.values():
            if node.parent_id and node.parent_id in nodes:
                nodes[node.parent_id].children.append(node)
            else:
                roots.append(node)
        return roots
    finally:
        conn.close()


@router.post("/theories", response_model=Theory, status_code=201)
def create_theory(data: TheoryCreate):
    conn = get_db()
    try:
        if data.parent_id:
            _fetch_theory(conn, data.parent_id)
        cur = conn.execute(
            "INSERT INTO theories (name, description, parent_id, status) VALUES (?, ?, ?, ?)",
            [data.name, data.description, data.parent_id, data.status],
        )
        conn.commit()
        return _fetch_theory(conn, cur.lastrowid)
    finally:
        conn.close()


@router.put("/theories/{theory_id}", response_model=Theory)
def update_theory(theory_id: int, data: TheoryUpdate):
    conn = get_db()
    try:
        _fetch_theory(conn, theory_id)
        updates = {}
        if data.name is not None:
            updates["name"] = data.name
        if data.description is not None:
            updates["description"] = data.description
        if data.parent_id is not None:
            if data.parent_id == theory_id:
                raise HTTPException(400, "A theory cannot be its own parent")
            _fetch_theory(conn, data.parent_id)
            # Cycle prevention: walk ancestors of the new parent.
            node, visited = data.parent_id, set()
            while node is not None:
                if node == theory_id:
                    raise HTTPException(400, "Moving this theory here would create a cycle")
                if node in visited:
                    break
                visited.add(node)
                parent = conn.execute("SELECT parent_id FROM theories WHERE id = ?", [node]).fetchone()
                node = parent["parent_id"] if parent else None
            updates["parent_id"] = data.parent_id
        if data.status is not None:
            updates["status"] = data.status
        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(
                f"UPDATE theories SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
                [*updates.values(), theory_id],
            )
            conn.commit()
        return _fetch_theory(conn, theory_id)
    finally:
        conn.close()


@router.delete("/theories/{theory_id}", status_code=204)
def delete_theory(theory_id: int):
    conn = get_db()
    try:
        _fetch_theory(conn, theory_id)
        # Children keep existing as roots (ON DELETE SET NULL); evidence cascades.
        conn.execute("DELETE FROM theories WHERE id = ?", [theory_id])
        conn.commit()
    finally:
        conn.close()


# ── Evidence ──

@router.get("/theories/{theory_id}/evidence", response_model=list[TheoryEvidence])
def list_evidence(theory_id: int):
    conn = get_db()
    try:
        _fetch_theory(conn, theory_id)
        rows = conn.execute(
            """SELECT te.id, te.theory_id, te.entry_id, e.title AS entry_title,
                      te.stance, te.note, te.weight, te.created_at
               FROM theory_evidence te
               JOIN entries e ON e.id = te.entry_id
               WHERE te.theory_id = ?
               ORDER BY te.stance, e.entry_date IS NULL, e.entry_date""",
            [theory_id],
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/theories/{theory_id}/evidence", response_model=TheoryEvidence, status_code=201)
def add_evidence(theory_id: int, data: TheoryEvidenceIn):
    conn = get_db()
    try:
        _fetch_theory(conn, theory_id)
        entry = conn.execute("SELECT id, title FROM entries WHERE id = ?", [data.entry_id]).fetchone()
        if not entry:
            raise HTTPException(404, "Entry not found")
        conn.execute(
            """INSERT OR IGNORE INTO theory_evidence (theory_id, entry_id, stance, note, weight)
               VALUES (?, ?, ?, ?, ?)""",
            [theory_id, data.entry_id, data.stance, data.note, data.weight],
        )
        conn.commit()
        row = conn.execute(
            """SELECT te.id, te.theory_id, te.entry_id, e.title AS entry_title,
                      te.stance, te.note, te.weight, te.created_at
               FROM theory_evidence te
               JOIN entries e ON e.id = te.entry_id
               WHERE te.theory_id = ? AND te.entry_id = ? AND te.stance = ?""",
            [theory_id, data.entry_id, data.stance],
        ).fetchone()
        if not row:
            raise HTTPException(409, "Evidence already exists with that stance")
        return dict(row)
    finally:
        conn.close()


@router.delete("/theories/{theory_id}/evidence/{evidence_id}", status_code=204)
def delete_evidence(theory_id: int, evidence_id: int):
    conn = get_db()
    try:
        cur = conn.execute(
            "DELETE FROM theory_evidence WHERE id = ? AND theory_id = ?",
            [evidence_id, theory_id],
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "Evidence not found")
    finally:
        conn.close()
