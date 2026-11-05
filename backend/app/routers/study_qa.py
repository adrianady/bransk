"""
Study QA + health check for a single study.

Ask a question about a downloaded study (semantic search over its indexed PDF
chunks, grounded answer with citations) and run a quick "what's missing /
what needs fixing" audit of the study entry.
"""
import json
import logging
import re
from typing import Optional

from fastapi import APIRouter, HTTPException

from app.db import get_db
from app.embeddings import cosine, embed
from app.llm import LLM_PROVIDER, complete as llm_complete
from app.pdf_rag import search_documents

logger = logging.getLogger("chronokg.study_qa")
router = APIRouter()

_QA_PROMPT = """You are an expert scientific research assistant answering a targeted query about a specific study.
Your epistemological duty is absolute fidelity to the source text: use ONLY the study's own content (its entry metadata and the indexed PDF chunks below).

Answer in rigorous, objective academic Romanian.
Rules:
1. Grounding: Cite the study by title and specify the section from which evidence is drawn. Give a PDF page only when the supplied chunk includes a verified page number; never infer it from the chunk number.
2. Empirical vs. Theoretical Distinction: Explicitly distinguish between verified empirical findings (sample size N, statistics, observed effect sizes, p-values) and speculative hypotheses or author interpretations.
3. Epistemic Modesty: If the question asks for details not documented in the provided excerpts (e.g., specific dosage, unlisted control group, omitted statistical test), state unequivocally: "Această informație nu este menționată în fragmentele disponibile din PDF." DO NOT hallucinate facts, numbers, or external literature.
4. Structure:
   - Provide a concise, clear, and direct academic answer first.
    - Include a final "### Surse" section enumerating supplied chunk identifiers and the exact fact or finding each supports. Do not invent identifiers or page numbers.

=== STUDY ENTRY ===
Title: {title}
Date: {date}
Authors: {authors}
Tags: {tags}
Content:
{content}

=== INDEXED PDF CHUNKS (most relevant first) ===
{chunks}

QUESTION: {question}
"""


class _StudyQARequest:
    def __init__(self, question: str):
        self.question = question


def _is_study(entry: dict) -> bool:
    return bool(
        entry.get("study_type")
        or "study" in (entry.get("tags") or "").lower()
        or "pdf" in (entry.get("tags") or "").lower()
        or entry.get("pdf_path")
        or "**PDF:**" in (entry.get("content") or "")
    )


def _study_chunks(entry_id: int, query: str, top_k: int = 6) -> list[dict]:
    """Return the most relevant PDF chunks for one study, ranked by similarity."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, chunk_index, page_number, content FROM documents WHERE entry_id = ? ORDER BY chunk_index",
            [entry_id],
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return []
    texts = [query] + [r["content"] for r in rows]
    vectors = embed(texts)
    if vectors is None:
        # No embedding backend: rank by keyword overlap so the chunks that
        # actually mention the question's words come first, instead of
        # blindly taking the opening pages of the paper.
        def _terms(s: str) -> set[str]:
            folded = (
                (s or "").lower()
                .replace("ă", "a").replace("â", "a").replace("î", "i")
                .replace("ș", "s").replace("ş", "s").replace("ț", "t").replace("ţ", "t")
            )
            return {w for w in re.findall(r"[a-z0-9]{3,}", folded)}

        q_terms = _terms(query)
        scored_fallback = []
        for r in rows:
            overlap = len(q_terms & _terms(r["content"]))
            scored_fallback.append((overlap, r))
        scored_fallback.sort(key=lambda item: item[0], reverse=True)
        return [
            {"document_id": r["id"], "chunk_index": r["chunk_index"],
             "page_number": r["page_number"], "content": r["content"],
             "score": round(hits / max(1, len(q_terms)), 2)}
            for hits, r in scored_fallback[:top_k]
        ]
    qv = vectors[0]
    scored = []
    for i, r in enumerate(rows):
        try:
            score = cosine(qv, vectors[i + 1])
        except Exception:
            score = 0.0
        scored.append((score, {
            "document_id": r["id"],
            "chunk_index": r["chunk_index"],
            "page_number": r["page_number"],
            "content": r["content"],
            "score": round(float(score), 4),
        }))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [c for _, c in scored[:top_k]]


def _audit_study(entry: dict, indexed_chunks: int) -> dict:
    issues = []
    suggestions = []

    if not _is_study(entry):
        issues.append("Intrarea nu este marcată ca studiu.")
        suggestions.append("Setează study_type='study' și adaugă tagul 'study'.")

    if not entry.get("entry_date"):
        issues.append("Lipsește data de publicare (entry_date).")
        suggestions.append("Adaugă o dată în format YYYY-MM-DD.")

    if not entry.get("authors"):
        issues.append("Lipsește autorul/studiu.")
        suggestions.append("Adaugă câmpul 'authors'.")

    if not entry.get("design"):
        issues.append("Lipsește designul studiului (RCT, cohort, review etc.).")
        suggestions.append("Adaugă câmpul 'design'.")

    if not entry.get("effect_direction"):
        issues.append("Lipsește direcția efectului (positive/negative/mixed/null).")
        suggestions.append("Adaugă câmpul 'effect_direction'.")

    if entry.get("quality") is None:
        issues.append("Nu există o apreciere de calitate (1-5 stele).")
        suggestions.append("Adaugă 'quality' între 1 și 5.")

    if not entry.get("source") and not entry.get("pdf_path"):
        issues.append("Lipsește sursa URL și fișierul PDF local.")
        suggestions.append("Adaugă sursa sau descarcă PDF-ul prin 'Download PDF'.")

    if not entry.get("pdf_path"):
        issues.append("PDF-ul nu este descărcat local, deci nu poate fi citit în aplicație.")
        suggestions.append("Folosește 'Download PDF' pentru a-l salva și indexa.")

    if indexed_chunks == 0:
        issues.append("Conținutul PDF-ului nu este indexat (RAG), astfel că nu se pot pune întrebări despre el.")
        suggestions.append("Indexează PDF-ul (redescarcare sau re-analizare din Review).")

    if not entry.get("url_validated"):
        issues.append("Sursa URL nu este validată.")
        suggestions.append("Validează sursa sau verifică URL-ul.")

    return {
        "issues": issues,
        "suggestions": suggestions,
        "indexed_chunks": indexed_chunks,
    }


@router.post("/studies/{entry_id}/qa")
async def study_qa(entry_id: int, body: dict):
    """Answer a question about a study using its indexed PDF content."""
    question = (body or {}).get("question", "").strip()
    if not question:
        raise HTTPException(400, "Question is required")

    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM entries WHERE id = ?", [entry_id]).fetchone()
        if not row:
            raise HTTPException(404, "Study not found")
        entry = dict(row)
    finally:
        conn.close()

    if not _is_study(entry) and not entry.get("content"):
        raise HTTPException(400, "This entry has neither study metadata nor content to analyze")

    chunks = _study_chunks(entry_id, question, top_k=6)
    chunks_md = ""
    for c in chunks:
        preview = c["content"][:600].replace("\n", " ")
        page = f", pagină PDF {c['page_number']}" if c.get("page_number") else ", pagină neverificată"
        chunks_md += (
            f"- Sursa [document_{c['document_id']}] / chunk_{c['chunk_index']}"
            f"{page} (relevanță {c.get('score', 0):.2f}): {preview}\n"
        )
    if not chunks_md:
        chunks_md = "(nu există chunks indexați pentru acest studiu — descarcă și indexează PDF-ul mai întâi)"

    entry_content = (entry.get("content") or "")[:2500]

    messages = [
        {
            "role": "system",
            "content": _QA_PROMPT.format(
                title=entry.get("title") or "",
                date=entry.get("entry_date") or "n/d",
                authors=entry.get("authors") or "n/d",
                tags=entry.get("tags") or "",
                content=entry_content,
                chunks=chunks_md,
                question=question,
            ),
        },
        {"role": "user", "content": question},
    ]

    if LLM_PROVIDER == "mock":
        answer = f"(mod offline) Am primit întrebarea: {question}. Pentru un răspuns real, configurează Ollama sau DeepSeek."
        sources = []
    else:
        try:
            _, _, text = await llm_complete(messages, temperature=0.2, max_tokens=1500)
            answer = text
        except Exception as exc:
            logger.error("Study QA LLM failed: %s", exc)
            raise HTTPException(503, "LLM unavailable for answering the question")
        sources = [
            {
                "document_id": c["document_id"],
                "entry_id": entry_id,
                "chunk_index": c["chunk_index"],
                "page_number": c.get("page_number"),
                "page_verified": c.get("page_number") is not None,
                "preview": c["content"][:300].replace("\n", " "),
                "score": c.get("score", 0.0),
            }
            for c in chunks
        ]

    return {
        "entry_id": entry_id,
        "title": entry.get("title"),
        "question": question,
        "answer": answer,
        "sources": sources,
    }


@router.get("/studies/{entry_id}/sources")
def study_sources(entry_id: int, limit: int = 200):
    """List indexed source fragments; page numbers are null unless PDF-derived."""
    limit = max(1, min(limit, 500))
    conn = get_db()
    try:
        entry = conn.execute(
            "SELECT id, title FROM entries WHERE id = ? AND archived = 0", [entry_id]
        ).fetchone()
        if not entry:
            raise HTTPException(404, "Study not found")
        rows = conn.execute(
            """SELECT id AS document_id, entry_id, chunk_index, page_number, content
                 FROM documents WHERE entry_id = ? ORDER BY chunk_index LIMIT ?""",
            [entry_id, limit],
        ).fetchall()
        sources = [
            {
                "document_id": row["document_id"],
                "entry_id": row["entry_id"],
                "chunk_index": row["chunk_index"],
                "page_number": row["page_number"],
                "page_verified": row["page_number"] is not None,
                "preview": (row["content"] or "")[:400].replace("\n", " "),
            }
            for row in rows
        ]
        return {"entry_id": entry_id, "title": entry["title"], "sources": sources}
    finally:
        conn.close()


@router.get("/studies/{entry_id}/check")
async def study_check(entry_id: int):
    """Quick audit: what's missing and what needs fixing for this study."""
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM entries WHERE id = ?", [entry_id]).fetchone()
        if not row:
            raise HTTPException(404, "Study not found")
        entry = dict(row)
        n_docs = conn.execute(
            "SELECT COUNT(*) AS n FROM documents WHERE entry_id = ?", [entry_id]
        ).fetchone()["n"]
    finally:
        conn.close()

    audit = _audit_study(entry, n_docs)
    return {
        "entry_id": entry_id,
        "title": entry.get("title"),
        "is_study": _is_study(entry),
        "issues": audit["issues"],
        "suggestions": audit["suggestions"],
        "indexed_chunks": audit["indexed_chunks"],
    }
