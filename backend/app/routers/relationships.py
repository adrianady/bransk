"""
Relationships (graph edges) router.

The graph endpoint is where every entry, every edge and their label
normalisation come together, so the normalisation rules live here too:

* A relationship label is free text in the database (``"precursor to"``,
  ``precedes | Tulving's episodic/semantic -> Squire's systems``, ...). The graph
  must not show that raw noise: each label maps to a canonical code plus a short
  Romanian display text and a detail, and the UI reasons on the code.
* Only edges between two non-archived entries are visible, so the graph and the
  degree counts always agree with what is actually drawn.
* ``degree`` and ``isolated`` travel with each node: most entries here have no
  relationship at all, and the user needs to *see* which ones, not guess.
"""
import re
from collections import Counter

from fastapi import APIRouter, HTTPException
from app.db import get_db
from app.models import RelationshipCreate, RelationshipResponse

router = APIRouter()

# Canonical relation vocabulary. The code is stored/compared; the display text
# is what a Romanian reader sees next to the edge.
RELATION_KINDS: dict[str, str] = {
    "causes": "cauzează",
    "modulates": "modulează",
    "inhibits": "inhibă",
    "precedes": "precede",
    "includes": "include",
    "extends": "extinde",
    "supports": "susține",
    "contradicts": "contrazice",
    "influences": "influențează",
    "derives_from": "derivă din",
    "correlates_with": "corelează cu",
    "alternative_to": "alternativă la",
    "part_of": "face parte din",
    "criticizes": "critică",
    "applied_in": "aplicat în",
    "defended_by": "apărat de",
    "popularized_by": "popularizat de",
    "reviewed_in": "recenzat în",
    "competes_with": "concurează cu",
    "compared_with": "comparat cu",
    "relates_to": "se leagă de",
}

# Every spelling/synonym that must collapse onto a canonical code. Keys are
# compared after ``_normalise_token`` (lowercase, quotes stripped, spaces to _).
RELATION_SYNONYMS: dict[str, str] = {
    "relates_to": "relates_to",
    "relates": "relates_to",
    "relate": "relates_to",
    "related": "relates_to",
    "se_leaga": "relates_to",
    "includes": "includes",
    "include": "includes",
    "contains": "includes",
    "extends": "extends",
    "extend": "extends",
    "builds_on": "extends",
    "draws_on": "extends",
    "supports": "supports",
    "support": "supports",
    "supports_evidence": "supports",
    "confirms": "supports",
    "precedes": "precedes",
    "precede": "precedes",
    "preceded": "precedes",
    "precursor": "precedes",
    "precursor_to": "precedes",
    "precursory": "precedes",
    "comes_before": "precedes",
    "antecedes": "precedes",
    "before": "precedes",
    "influenced": "influences",
    "influences": "influences",
    "influence": "influences",
    "inspires": "influences",
    "inspired": "influences",
    "paves_way_for": "influences",
    "alternative_to": "alternative_to",
    "alternative": "alternative_to",
    "contrasts_with": "alternative_to",
    "contradicts": "contradicts",
    "contradict": "contradicts",
    "contradicted_by": "contradicts",
    "opposes": "contradicts",
    "refutes": "contradicts",
    "causes": "causes",
    "cause": "causes",
    "leads_to": "causes",
    "results_in": "causes",
    "modulates": "modulates",
    "modulate": "modulates",
    "modulating": "modulates",
    "regulates": "modulates",
    "inhibits": "inhibits",
    "inhibit": "inhibits",
    "inhibiting": "inhibits",
    "correlates_with": "correlates_with",
    "correlate": "correlates_with",
    "correlates": "correlates_with",
    "associated_with": "correlates_with",
    "part_of": "part_of",
    "belongs_to": "part_of",
    "criticizes": "criticizes",
    "criticises": "criticizes",
    "critiques": "criticizes",
    "derives_from": "derives_from",
    "formalized_by": "derives_from",
    "formalised_by": "derives_from",
    "formalized": "derives_from",
    "derive_from": "derives_from",
    "derived_from": "derives_from",
    "derives": "derives_from",
    "emerges_from": "derives_from",
    "applied_in": "applied_in",
    "applied_to": "applied_in",
    "applies_to": "applied_in",
    "applied_to_ai_in": "applied_in",
    "used_in": "applied_in",
    "defended_by": "defended_by",
    "defends": "defended_by",
    "championed_by": "defended_by",
    "popularized_by": "popularized_by",
    "popularised_by": "popularized_by",
    "popularized": "popularized_by",
    "reviewed_in": "reviewed_in",
    "reviewed_by": "reviewed_in",
    "reviewed": "reviewed_in",
    "competes_with": "competes_with",
    "competes": "competes_with",
    "rivals": "competes_with",
    "compared_with": "compared_with",
    "compared_in": "compared_with",
    "compares_with": "compared_with",
}

# Edge palette so relation types are visually distinct instead of 128 identical
# arrows. Dashed ones mark tension/association, solid ones mark lineage.
# One semantic palette for the whole graph: madder = causal/negative,
# brass = modulation/influence, verdigris = empirical support, lapis =
# conceptual derivation, mineral gray = generic or unknown. These are the same
# roles the UI uses, so the legend and the canvas agree.
EDGE_STYLES: dict[str, dict] = {
    "causes": {"color": "#a2403f", "dashes": False},
    "modulates": {"color": "#9a742f", "dashes": False},
    "inhibits": {"color": "#7f2f2f", "dashes": False},
    "precedes": {"color": "#2f5fa8", "dashes": False},
    "includes": {"color": "#2c6f63", "dashes": False},
    "extends": {"color": "#46688f", "dashes": False},
    "supports": {"color": "#3d8477", "dashes": False},
    "contradicts": {"color": "#a2403f", "dashes": True},
    "influences": {"color": "#9a742f", "dashes": True},
    "alternative_to": {"color": "#7a5a8a", "dashes": True},
    "part_of": {"color": "#5a8f86", "dashes": False},
    "criticizes": {"color": "#a2403f", "dashes": True},
    "derives_from": {"color": "#2f5fa8", "dashes": False},
    "applied_in": {"color": "#4f79c8", "dashes": False},
    "defended_by": {"color": "#5c6f8f", "dashes": False},
    "popularized_by": {"color": "#a8843c", "dashes": False},
    "reviewed_in": {"color": "#677385", "dashes": True},
    "competes_with": {"color": "#8c3d3d", "dashes": True},
    "compared_with": {"color": "#3d8477", "dashes": True},
    "correlates_with": {"color": "#8d99a9", "dashes": [4, 4]},
    "relates_to": {"color": "#8d99a9", "dashes": [4, 6]},
}


def _normalise_token(text: str) -> str:
    """Lowercase, strip wrapping quotes/punctuation, spaces to underscores."""
    t = (text or "").strip().strip("\"'`„”“”).,;:")
    t = re.sub(r"\s+", " ", t)
    # DEAD END: an underscore pair such as "precursor_to" must fold to
    # "precursor to" first, otherwise it never matches the synonym keys.
    t = t.replace("_", " ")
    t = re.sub(r"\s+", " ", t).strip()
    return t.lower().replace(" ", "_")


def parse_relation_label(raw: str | None) -> tuple[str, str]:
    """Split a stored label into ``(canonical_code, detail)``.

    ``"precursor to"``            -> ("precedes", "")
    ``precedes | Tulving -> ...`` -> ("precedes", "Tulving -> ...")
    ``something unknown``         -> ("relates_to", "something unknown")

    The detail keeps the human sentence so nothing the author wrote is lost,
    while the code makes the edge filterable and colourable.
    """
    text = (raw or "").strip()
    if not text:
        return "relates_to", ""

    head, sep, tail = text.partition("|")
    head, tail = head.strip(), tail.strip()
    token = _normalise_token(head)

    code = RELATION_SYNONYMS.get(token)
    if code is None:
        # No known head phrase: recognise a synonym as a leading word
        # ("precursor to X", "supports the claim that ...").
        for word in re.split(r"[\W_]+", token):
            if word and word in RELATION_SYNONYMS:
                code = RELATION_SYNONYMS[word]
                break
    known = code is not None
    if not known:
        code = "relates_to"

    detail = tail
    if not detail and token != code and token not in RELATION_SYNONYMS:
        # The head was not itself a canonical name: it was either free text
        # that merely *contained* a known word ("formalized by"), or a label
        # nobody recognised. Keep the original words so no meaning is lost
        # ("precursor to" -> code precedes, detail "precursor to" is redundant
        # only when the head IS the synonym, which the check above excludes).
        raw_head = head.strip("\"'`„”“").strip()
        if raw_head:
            detail = raw_head
    return code, detail


def normalise_relation_label(raw: str | None) -> str:
    """Canonical code for a stored label (used on write and by the model)."""
    return parse_relation_label(raw)[0]


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


def _visible_edges(conn) -> list:
    """Edges whose BOTH ends are live entries; self-loops dropped."""
    return conn.execute(
        """SELECT r.id, r.from_id, r.to_id, r.label
           FROM relationships r
           JOIN entries e1 ON e1.id = r.from_id AND e1.archived = 0
           JOIN entries e2 ON e2.id = r.to_id AND e2.archived = 0
           WHERE r.from_id != r.to_id"""
    ).fetchall()


def _edge_payload(row) -> dict:
    d = dict(row)
    code, detail = parse_relation_label(d.get("label"))
    style = EDGE_STYLES.get(code, EDGE_STYLES["relates_to"])
    return {
        "id": d["id"],
        "from": d["from_id"],
        "to": d["to_id"],
        # `label` stays the canonical code: existing consumers keep working and
        # new ones can filter on it.
        "label": code,
        "label_display": RELATION_KINDS.get(code, RELATION_KINDS["relates_to"]),
        "detail": detail,
        "color": style["color"],
        "dashes": style["dashes"],
        "arrows": "to",
    }


@router.get("/relationships/graph")
def get_graph_data():
    """Full graph (nodes + edges) for vis-network, with normalised labels."""
    conn = get_db()
    try:
        entries = conn.execute(
            "SELECT id, title, tags, importance, entry_date, content FROM entries WHERE archived = 0"
        ).fetchall()

        degree: Counter = Counter()
        edges = []
        for r in _visible_edges(conn):
            payload = _edge_payload(r)
            edges.append(payload)
            degree[payload["from"]] += 1
            degree[payload["to"]] += 1

        nodes = []
        for e in entries:
            d = dict(e)
            # Color by importance
            colors = {1: "#8d99a9", 2: "#2f5fa8", 3: "#a8843c", 4: "#a2403f", 5: "#2c6f63"}
            deg = degree.get(d["id"], 0)
            nodes.append({
                "id": d["id"],
                # Full title — the frontend wraps it inside the node instead of
                # truncating, so studies and theories keep readable names.
                "label": d["title"],
                "title": d["title"],
                "value": d["importance"],
                "color": colors.get(d["importance"], "#6366f1"),
                "group": d["tags"].split(",")[0] if d["tags"] else "default",
                "entry_date": d["entry_date"],
                "tags": d["tags"],
                "content_preview": (d["content"] or "")[:300],
                "degree": deg,
                "isolated": deg == 0,
            })

        return {"nodes": nodes, "edges": edges}
    finally:
        conn.close()


@router.get("/relationships/labels")
def relationship_labels():
    """Canonical label counts, for the graph legend and the relation filter.

    Computed over visible edges only, so the numbers match the drawn graph.
    """
    conn = get_db()
    try:
        counts: Counter = Counter()
        for r in _visible_edges(conn):
            counts[parse_relation_label(r["label"])[0]] += 1
        return {
            "total": sum(counts.values()),
            "labels": [
                {
                    "code": code,
                    "display": RELATION_KINDS.get(code, RELATION_KINDS["relates_to"]),
                    "count": counts[code],
                    **EDGE_STYLES.get(code, EDGE_STYLES["relates_to"]),
                }
                for code in sorted(counts, key=lambda c: (-counts[c], c))
            ],
        }
    finally:
        conn.close()


@router.post("/relationships", response_model=RelationshipResponse, status_code=201)
def create_relationship(data: RelationshipCreate):
    if data.from_id == data.to_id:
        raise HTTPException(400, "Nu poți lega o intrare de ea însăși")
    conn = get_db()
    try:
        # Verify both entries exist
        e1 = conn.execute("SELECT id FROM entries WHERE id = ?", [data.from_id]).fetchone()
        e2 = conn.execute("SELECT id FROM entries WHERE id = ?", [data.to_id]).fetchone()
        if not e1 or not e2:
            raise HTTPException(404, "One or both entries not found")

        # Store the canonical code so the graph, the filters and the AI all
        # speak the same vocabulary; a free-text label is normalised once.
        code, detail = parse_relation_label(data.label)

        # Check if relationship already exists
        existing = conn.execute(
            "SELECT id FROM relationships WHERE from_id = ? AND to_id = ? AND label = ?",
            [data.from_id, data.to_id, code]
        ).fetchone()
        if existing:
            raise HTTPException(409, "Relationship already exists")

        stored = code if not detail else f"{code} | {detail}"
        cur = conn.execute(
            "INSERT INTO relationships (from_id, to_id, label) VALUES (?, ?, ?)",
            [data.from_id, data.to_id, stored]
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
