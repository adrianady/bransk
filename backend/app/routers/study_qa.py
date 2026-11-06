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

=== HIGHLIGHTS MARCATE DE UTILIZATOR ÎN PDF (prioritate maximă — arată ce a considerat el important) ===
{highlights}

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
            "SELECT id, chunk_index, page_number, content, embedding FROM documents WHERE entry_id = ? ORDER BY chunk_index",
            [entry_id],
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return []

    def _terms(s: str) -> set[str]:
        folded = (
            (s or "").lower()
            .replace("ă", "a").replace("â", "a").replace("î", "i")
            .replace("ș", "s").replace("ş", "s").replace("ț", "t").replace("ţ", "t")
        )
        return {w for w in re.findall(r"[a-z0-9]{3,}", folded)}

    def _keyword_rank() -> list[dict]:
        # No embedding backend: rank by keyword overlap so the chunks that
        # actually mention the question's words come first, instead of
        # blindly taking the opening pages of the paper.
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

    def _package(score: float, r) -> dict:
        return {"document_id": r["id"], "chunk_index": r["chunk_index"],
                "page_number": r["page_number"], "content": r["content"],
                "score": round(float(score), 4)}

    # Fast path: reuse the vectors stored at indexing time, so each question
    # embeds only the query instead of re-embedding every chunk (10-50x less
    # CPU on an 89-chunk paper).
    qvectors = embed([query])
    if qvectors:
        qv = qvectors[0]
        stored: list = []
        complete = True
        for r in rows:
            try:
                vec = json.loads(r["embedding"] or "[]")
            except Exception:
                vec = None
            if not vec or len(vec) != len(qv):
                # Missing vectors, or leftovers from another embedding backend:
                # cosine against them would be noise.
                complete = False
                break
            stored.append((r, vec))
        if complete:
            scored = []
            for r, vec in stored:
                try:
                    score = cosine(qv, vec)
                except Exception:
                    score = 0.0
                scored.append((score, r))
            scored.sort(key=lambda item: item[0], reverse=True)
            return [_package(s, r) for s, r in scored[:top_k]]

    # Legacy path: stored vectors unusable — re-embed the chunks (the old
    # behaviour), or fall back to keywords when there is no backend at all.
    texts = [query] + [r["content"] for r in rows]
    vectors = embed(texts)
    if vectors is None:
        return _keyword_rank()
    qv = vectors[0]
    scored = []
    for i, r in enumerate(rows):
        try:
            score = cosine(qv, vectors[i + 1])
        except Exception:
            score = 0.0
        scored.append((score, r))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [_package(s, r) for s, r in scored[:top_k]]


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
        # The user's own highlights outrank retrieved chunks: they mark what
        # the reader already judged important in this paper.
        try:
            hl_rows = conn.execute(
                """SELECT page_number, quote, note FROM pdf_highlights
                    WHERE entry_id = ? ORDER BY COALESCE(page_number, 0), id""",
                [entry_id],
            ).fetchall()
        except Exception:
            hl_rows = []
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

    if hl_rows:
        highlights_md = ""
        for h in hl_rows:
            pg = f"pagina {h['page_number']}" if h["page_number"] else "pagină necunoscută"
            note = f" — notiță: {h['note']}" if h["note"] else ""
            highlights_md += f"- [{pg}]: \"{h['quote']}\"{note}\n"
    else:
        highlights_md = "(utilizatorul nu a evidențiat încă pasaje din acest PDF)"

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
                highlights=highlights_md,
                chunks=chunks_md,
                question=question,
            ),
        },
        {"role": "user", "content": question},
    ]

    if LLM_PROVIDER == "mock":
        answer = f"(mod offline) Am primit întrebarea: {question}. Pentru un răspuns real, configurează Ollama sau DeepSeek."
    else:
        try:
            _, _, text = await llm_complete(messages, temperature=0.2, max_tokens=1500)
            answer = text
        except Exception as exc:
            logger.error("Study QA LLM failed: %s", exc)
            raise HTTPException(503, "LLM unavailable for answering the question")

    # Sources stay visible even offline: "0 surse" would read as "the PDF is
    # not indexed" when the chunks were actually found.
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


_APPRAISAL_PROMPT = """Estimezi metodologia unui studiu științific, pe baza textului de mai jos (metadate + fragmente din PDF).

Răspunde EXCLUSIV cu un obiect JSON, fără alte explicații:
{{"design": "<RCT|cohort|case-control|cross-sectional|systematic review|meta-analysis|qualitative|other>",
   "effect_direction": "<positive|negative|mixed|null>",
   "quality": <1-5>,
   "justification": "<1-3 propoziții, în română, cu motivele estimării>"}}

Convenții: „quality" apreciază rigurozitatea metodologică (5 = RCT amplu cu preregistrare și pierderi mici; 1 = opinie/fără metodă).
„effect_direction" descrie rezultatul principal raportat de autori, nu ceea ce sperau ei.

=== STUDIU ===
Title: {title}
Authors: {authors}
Content:
{content}

=== FRAGMENTE DIN PDF ===
{chunks}
"""


@router.post("/studies/{entry_id}/ai-appraisal")
async def ai_appraisal(entry_id: int):
    """Assisted creation: estimate design / effect direction / quality.

    The form asks the user for methodological fields nobody remembers how to
    fill; the model proposes values from the study's own text (never from
    general knowledge) and the user keeps the final say in the editor.
    """
    if LLM_PROVIDER == "mock":
        raise HTTPException(503, "Evaluarea AI necesită un provider configurat (DeepSeek / Ollama / OpenCode).")

    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM entries WHERE id = ? AND archived = 0", [entry_id]).fetchone()
        if not row:
            raise HTTPException(404, "Study not found")
        entry = dict(row)
        chunk_rows = conn.execute(
            "SELECT content FROM documents WHERE entry_id = ? ORDER BY chunk_index LIMIT 3",
            [entry_id],
        ).fetchall()
    finally:
        conn.close()

    chunks_md = "\n".join(
        f"- {(r['content'] or '')[:800]}" for r in chunk_rows
    ) or "(PDF neindexat — estimarea se bazează doar pe metadate și conținut)"

    messages = [{
        "role": "system",
        "content": _APPRAISAL_PROMPT.format(
            title=entry.get("title") or "n/d",
            authors=entry.get("authors") or "n/d",
            content=(entry.get("content") or "")[:2500],
            chunks=chunks_md,
        ),
    }]
    try:
        _, _, text = await llm_complete(messages, temperature=0.1, max_tokens=400)
    except Exception as exc:
        logger.error("AI appraisal failed for entry %s: %s", entry_id, exc)
        raise HTTPException(503, "LLM unavailable for the appraisal")

    # The model may wrap the JSON in prose or code fences; take the object.
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise HTTPException(502, "The model did not return a usable appraisal")
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        raise HTTPException(502, "The model returned malformed appraisal JSON")

    design = str(parsed.get("design") or "").strip()[:100]
    effect = str(parsed.get("effect_direction") or "").strip().lower()
    if effect not in ("positive", "negative", "mixed", "null"):
        effect = ""
    try:
        quality = max(1, min(5, int(parsed.get("quality"))))
    except (TypeError, ValueError):
        quality = None
    justification = str(parsed.get("justification") or "").strip()[:1000]

    if design or effect or quality:
        conn = get_db()
        try:
            conn.execute(
                "UPDATE entries SET design = COALESCE(NULLIF(?, ''), design),"
                " effect_direction = COALESCE(NULLIF(?, ''), effect_direction),"
                " quality = COALESCE(?, quality), updated_at = datetime('now') WHERE id = ?",
                [design, effect, quality, entry_id],
            )
            conn.commit()
        finally:
            conn.close()

    return {"entry_id": entry_id, "design": design, "effect_direction": effect,
            "quality": quality, "justification": justification}


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
