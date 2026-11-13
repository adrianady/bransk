"""AI concept & relation extraction — from evidence to a concept graph.

POST /api/ai/extract-concepts reads highlights, notes (or an explicit text
selection) for the given entries, asks the LLM for factors / subfactors /
variables and their relations (``part_of`` / ``includes`` / ``causes`` /
``modulates`` …) with grounding quotes, then persists them:

* new-style rows in ``extracted_concepts`` + ``concept_evidence``, AND
* library-visible rows: an ``entries`` row per concept (``study_type =
  'concept'``) plus ``relationships`` edges between concepts and, when the
  quote names a source entry, between concept and study.

GET /api/project/{project_id}/concepts returns both the table rows and the
entry/relationship view so the graph can draw them without a migration.
"""
import json
import logging
import re
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db import get_db
from app.tagging import normalize_tags, sync_tags

logger = logging.getLogger("chronokg.ai_concepts")
router = APIRouter()

CONCEPT_SYSTEM = """You extract research concepts and their relations from academic evidence.
The input mixes the reader's highlights and notes with the surrounding document
context ([context p.N] = text around a highlighted page, [doc p.N] = sampled
rest of the document). Use the context to understand WHY a passage was
highlighted, not just what it literally says.
Return ONLY a JSON object with this exact shape:
{
  "concepts": [
    {"name": "concept name", "type": "factor|subfactor|construct|mechanism|outcome",
     "description": "1-2 sentences", "parent": "parent concept name or null",
     "evidence_quote": "short grounding quote from the input", "source_entry_id": 12}
  ],
  "relations": [
    {"from": "concept A", "to": "concept B",
     "relation": "part_of|includes|causes|modulates|inhibits|supports|contradicts|correlates_with|relates_to",
     "note": "why, briefly"}
  ]
}
Rules:
- 3-12 concepts, names short and distinct; unify synonyms used by different studies under ONE concept name.
- Build the hierarchy explicitly: if study A calls X a broad factor and study B splits it into sub-components, make X a factor and the components subfactors with parent "X" and a part_of relation.
- Every concept needs a grounding quote copied from the input when possible.
- source_entry_id must be one of the entry ids given in the user message, or null.
- Relations only between concepts named in the same answer.
- No prose, no markdown fences."""


class ExtractConceptsRequest(BaseModel):
    entry_ids: list[int] = Field(default_factory=list, max_length=50)
    project_id: Optional[int] = None
    text_selection: Optional[str] = Field(None, max_length=20000)
    page_number: Optional[int] = Field(None, ge=1, le=10000)


def _evidence_text(conn, entry_ids: list[int], text_selection: str | None) -> tuple[str, dict[int, str]]:
    chunks: list[str] = []
    titles: dict[int, str] = {}
    if text_selection and text_selection.strip():
        chunks.append("SELECTION:\n" + text_selection.strip()[:15000])
    for eid in entry_ids[:50]:
        row = conn.execute("SELECT id, title FROM entries WHERE id = ?", [eid]).fetchone()
        if not row:
            continue
        titles[eid] = row["title"] or ""
        chunks.append(f"\n--- ENTRY {eid}: {row['title'] or ''} ---")
        pages: set[int] = set()
        for h in conn.execute(
            "SELECT page_number, quote, note FROM pdf_highlights WHERE entry_id = ? ORDER BY id LIMIT 30",
            [eid],
        ).fetchall():
            page = f" (p. {h['page_number']})" if h["page_number"] else ""
            chunks.append(f"[highlight{page}] {h['quote']}" + (f" — note: {h['note']}" if h["note"] else ""))
            if h["page_number"]:
                pages.add(int(h["page_number"]))
        for n in conn.execute(
            "SELECT page_number, content FROM study_notes WHERE entry_id = ? ORDER BY id LIMIT 20",
            [eid],
        ).fetchall():
            page = f" (p. {n['page_number']})" if n["page_number"] else ""
            chunks.append(f"[note{page}] {(n['content'] or '')[:1000]}")
            if n["page_number"]:
                pages.add(int(n["page_number"]))

        # Why was a passage highlighted? Show the document context around every
        # highlighted/noted page, then sample the rest of the document so the
        # model sees the whole argument, not just its opening pages.
        rows = conn.execute(
            "SELECT page_number, content FROM documents WHERE entry_id = ? ORDER BY chunk_index",
            [eid],
        ).fetchall()
        near = [r for r in rows if r["page_number"] and int(r["page_number"]) in pages]
        rest = [r for r in rows if not (r["page_number"] and int(r["page_number"]) in pages)]
        budget = 24
        span = max(0, budget - len(near))
        if span and len(rest) > span:
            step = max(1, len(rest) // span)
            rest = rest[::step][:span]
        elif not span:
            rest = []
        for is_near, d in [(True, r) for r in near] + [(False, r) for r in rest]:
            page = f" (p. {d['page_number']})" if d["page_number"] else ""
            kind = "context" if is_near else "doc"
            chunks.append(f"[{kind}{page}] {(d['content'] or '')[:800]}")
    return "\n".join(chunks)[:30000], titles


def _extract_json(reply: str) -> dict:
    text = reply.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("No JSON object in LLM reply")
    return json.loads(text[start:end + 1])


def _mock_result(entry_ids: list[int]) -> dict:
    concepts = [
        {"name": "Main factor", "type": "factor", "description": "Primary factor from the evidence.",
         "parent": None, "evidence_quote": "mock quote",
         "source_entry_id": entry_ids[0] if entry_ids else None},
        {"name": "Sub factor", "type": "subfactor", "description": "Component of the main factor.",
         "parent": "Main factor", "evidence_quote": "mock quote",
         "source_entry_id": entry_ids[0] if entry_ids else None},
    ]
    return {"concepts": concepts,
            "relations": [{"from": "Sub factor", "to": "Main factor",
                           "relation": "part_of", "note": "component"}]}


@router.post("/ai/extract-concepts")
async def extract_concepts(body: ExtractConceptsRequest):
    from app.llm import LLM_PROVIDER, complete as llm_complete

    entry_ids = [int(e) for e in (body.entry_ids or []) if int(e) > 0]
    if not entry_ids and not (body.text_selection or "").strip():
        raise HTTPException(400, "Provide entry_ids or a text_selection")
    conn = get_db()
    try:
        evidence, titles = _evidence_text(conn, entry_ids, body.text_selection)
    finally:
        conn.close()
    if not evidence.strip():
        raise HTTPException(404, "No evidence found for these entries (no highlights, notes or chunks)")

    if LLM_PROVIDER == "mock":
        parsed = _mock_result(entry_ids)
    else:
        user_msg = (f"Entry ids: {entry_ids}\nTitles: {titles}\n\nEVIDENCE:\n{evidence}")
        try:
            _, _, reply = await llm_complete(
                [{"role": "system", "content": CONCEPT_SYSTEM},
                 {"role": "user", "content": user_msg}],
                temperature=0.2, json_mode=True, max_tokens=4000,
            )
            parsed = _extract_json(reply)
        except Exception as exc:
            logger.warning("Concept extraction failed: %s", exc)
            raise HTTPException(503, "Concept extraction failed. Check the LLM provider.")

    concepts_in = parsed.get("concepts") or []
    relations_in = parsed.get("relations") or []
    allowed_types = {"factor", "subfactor", "construct", "mechanism", "outcome"}
    allowed_rels = {"part_of", "includes", "causes", "modulates", "inhibits",
                    "supports", "contradicts", "correlates_with", "relates_to",
                    "extends", "derives_from", "influences", "precedes"}

    conn = get_db()
    try:
        project_id = body.project_id
        if project_id is None:
            try:
                from app.routers.project import _ensure_project

                project_id = _ensure_project(conn)["id"]
            except Exception:
                project_id = None
        if project_id is not None and not conn.execute(
            "SELECT id FROM project WHERE id = ?", [project_id]
        ).fetchone():
            raise HTTPException(404, "Project not found")

        concept_ids: dict[str, int] = {}
        concept_entry_ids: dict[str, int] = {}
        saved_concepts: list[dict] = []
        for raw in concepts_in[:20]:
            name = str(raw.get("name") or "").strip()[:200]
            if not name or name.lower() in concept_ids:
                continue
            ctype = str(raw.get("type") or "factor").strip().lower()
            if ctype not in allowed_types:
                ctype = "factor"
            desc = str(raw.get("description") or "")[:2000]
            quote = str(raw.get("evidence_quote") or "")[:2000]
            src = raw.get("source_entry_id")
            try:
                src = int(src) if src is not None else None
            except (TypeError, ValueError):
                src = None
            if src is not None and not conn.execute(
                "SELECT id FROM entries WHERE id = ?", [src]
            ).fetchone():
                src = None

            # Reuse a concept already extracted for this project so repeated
            # syntheses strengthen one map instead of creating look-alikes.
            existing = conn.execute(
                """SELECT id, name FROM extracted_concepts
                   WHERE project_id IS ? AND lower(name) = lower(?) LIMIT 1""",
                [project_id, name],
            ).fetchone()
            if existing:
                cid = existing["id"]
                if ctype != "factor":
                    # A later, more specific reading may refine the type.
                    conn.execute(
                        "UPDATE extracted_concepts SET concept_type = ? WHERE id = ?",
                        [ctype, cid],
                    )
                if desc:
                    conn.execute(
                        """UPDATE extracted_concepts SET description = ?
                           WHERE id = ? AND (description IS NULL OR description = '')""",
                        [desc, cid],
                    )
                concept_ids[name.lower()] = cid
                concept_entry = conn.execute(
                    """SELECT id FROM entries
                       WHERE study_type = 'concept' AND lower(title) = lower(?)
                       ORDER BY id LIMIT 1""",
                    [name],
                ).fetchone()
                if concept_entry:
                    concept_entry_ids[name.lower()] = concept_entry["id"]
                    if src is not None and src != concept_entry["id"]:
                        conn.execute(
                            "INSERT OR IGNORE INTO relationships (from_id, to_id, label) VALUES (?, ?, ?)",
                            [concept_entry["id"], src, "derives_from"],
                        )
            else:
                cur = conn.execute(
                    """INSERT INTO extracted_concepts (project_id, name, concept_type, description)
                       VALUES (?, ?, ?, ?)""",
                    [project_id, name, ctype, desc],
                )
                cid = cur.lastrowid
                concept_ids[name.lower()] = cid
                # Library-visible entry so the graph/timeline can show the concept.
                tags = normalize_tags("concept,ai-extracted")
                cur_e = conn.execute(
                    """INSERT INTO entries (title, content, tags, study_type, importance)
                       VALUES (?, ?, ?, 'concept', 2)""",
                    [name, desc, tags],
                )
                concept_entry_ids[name.lower()] = cur_e.lastrowid
                # Link the concept back to the study/evidence source so the graph
                # can answer "where does this factor come from?".
                if src is not None and src != cur_e.lastrowid:
                    conn.execute(
                        "INSERT OR IGNORE INTO relationships (from_id, to_id, label) VALUES (?, ?, ?)",
                        [cur_e.lastrowid, src, "derives_from"],
                    )
                try:
                    sync_tags(conn, tags)
                except Exception:  # noqa: BLE001 - tags table is best-effort
                    pass
            if quote or src is not None:
                duplicate_quote = conn.execute(
                    """SELECT 1 FROM concept_evidence
                       WHERE concept_id = ? AND lower(quote) = lower(?) LIMIT 1""",
                    [cid, quote or name],
                ).fetchone()
                if not duplicate_quote:
                    conn.execute(
                        """INSERT INTO concept_evidence (concept_id, entry_id, quote, page_number, note)
                           VALUES (?, ?, ?, ?, ?)""",
                        [cid, src, quote or name, body.page_number, ""],
                    )
            saved_concepts.append({"id": cid, "project_id": project_id, "name": name,
                                   "concept_type": ctype, "description": desc,
                                   "entry_id": concept_entry_ids.get(name.lower())})

        # Second pass: parent links for concepts that named their parent.
        for raw in concepts_in[:20]:
            name = str(raw.get("name") or "").strip()
            parent = str(raw.get("parent") or "").strip()
            cid = concept_ids.get(name.lower())
            pid = concept_ids.get(parent.lower()) if parent else None
            if cid and pid and pid != cid:
                conn.execute(
                    "UPDATE extracted_concepts SET parent_concept_id = ? WHERE id = ?",
                    [pid, cid],
                )

        saved_relations: list[dict] = []
        for raw in relations_in[:40]:
            a = str(raw.get("from") or "").strip()
            b = str(raw.get("to") or "").strip()
            rel = str(raw.get("relation") or "relates_to").strip().lower()
            note = str(raw.get("note") or "")[:500]
            if rel not in allowed_rels:
                rel = "relates_to"
            fa = concept_entry_ids.get(a.lower())
            fb = concept_entry_ids.get(b.lower())
            if not fa or not fb or fa == fb:
                continue
            # One clean edge per concept pair keeps the map readable across
            # repeated syntheses; evidence for it accumulates in concept_evidence.
            if conn.execute(
                "SELECT 1 FROM relationships WHERE from_id = ? AND to_id = ? LIMIT 1",
                [fa, fb],
            ).fetchone():
                continue
            label = rel if not note else f"{rel} | {note}"
            cur = conn.execute(
                "INSERT OR IGNORE INTO relationships (from_id, to_id, label) VALUES (?, ?, ?)",
                [fa, fb, label],
            )
            if cur.rowcount:
                saved_relations.append({"from": a, "to": b, "relation": rel, "note": note})
        conn.commit()
        return {"concepts": saved_concepts, "relations": saved_relations,
                "project_id": project_id}
    finally:
        conn.close()


@router.get("/project/{project_id}/concepts")
def project_concepts(project_id: int):
    conn = get_db()
    try:
        if not conn.execute("SELECT id FROM project WHERE id = ?", [project_id]).fetchone():
            raise HTTPException(404, "Project not found")
        concepts = [dict(r) for r in conn.execute(
            "SELECT * FROM extracted_concepts WHERE project_id = ? ORDER BY id",
            [project_id],
        ).fetchall()]
        by_id = {c["id"]: c for c in concepts}
        evidence = [dict(r) for r in conn.execute(
            """SELECT ce.* FROM concept_evidence ce
               JOIN extracted_concepts c ON c.id = ce.concept_id
               WHERE c.project_id = ? ORDER BY ce.id""",
            [project_id],
        ).fetchall()]
        # Relations between the concept entries (label 'concept' tag) for the graph.
        names = [c["name"] for c in concepts]
        relations: list[dict] = []
        if names:
            ph = ",".join("?" for _ in names)
            entry_rows = conn.execute(
                f"SELECT id, title FROM entries WHERE title IN ({ph}) AND study_type = 'concept'",
                names,
            ).fetchall()
            entry_ids = [r["id"] for r in entry_rows]
            if entry_ids:
                ph2 = ",".join("?" for _ in entry_ids)
                relations = [dict(r) for r in conn.execute(
                    f"""SELECT * FROM relationships
                        WHERE from_id IN ({ph2}) AND to_id IN ({ph2})""",
                    [*entry_ids, *entry_ids],
                ).fetchall()]
        for c in concepts:
            c["evidence"] = [e for e in evidence if e["concept_id"] == c["id"]]
            parent = by_id.get(c.get("parent_concept_id") or -1)
            c["parent_name"] = parent["name"] if parent else None
        return {"project_id": project_id, "concepts": concepts, "relations": relations}
    finally:
        conn.close()
