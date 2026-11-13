"""
Thesis project centre — the working core of the app.

Holds the one licence/dissertation the user is writing (editable title, type,
field, research question, outline) and answers three questions about it:

  * which of the studies already in the library fit this topic (ranked, with
    reasons — grounded in the real entries, never invented references)
  * how the topic stands (analysis of the library's evidence, gaps, risks)
  * what to fix next (feedback on the outline and on the writing so far)

Everything degrades honestly: when no LLM is reachable the endpoints still
return the real ranked study list and deterministic, rule-based advice, marked
as such, instead of silently making things up.
"""
from typing import Optional
import hashlib
import io
import json
import logging
import re
import datetime
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Response

from app.db import get_db
from app.llm import LLM_PROVIDER, complete as llm_complete
from app.models import ProjectFeedback
from app.nlp import RO_STOPWORDS, fold

logger = logging.getLogger("chronokg.project")
router = APIRouter()

# Backward compatibility alias
_fold = fold

WORK_TYPES = {
    "licenta": "lucrare de licență",
    "disertatie": "lucrare de disertație",
    "doctorat": "teză de doctorat",
    "referat": "referat",
}

# Romanian stop words that would otherwise dominate the keyword match.
_RO_STOP = RO_STOPWORDS

_PLACEHOLDER_STRUCTURE = [
    {
        "id": "s1",
        "title": "Introducere",
        "target_words": 1200,
        "description": "Contextul temei, actualitatea și motivația alegerii, scopul și obiectivele lucrării.",
        "content": "",
    },
    {
        "id": "s2",
        "title": "Cadrul teoretic (stadiul cunoașterii)",
        "target_words": 3500,
        "description": "Definirea conceptelor, teorii relevante, sinteza literaturii de specialitate.",
        "content": "",
    },
    {
        "id": "s3",
        "title": "Metodologia cercetării",
        "target_words": 2000,
        "description": "Designul, participanții, instrumentele, procedura și analiza statistică.",
        "content": "",
    },
    {
        "id": "s4",
        "title": "Rezultate",
        "target_words": 2500,
        "description": "Prezentarea rezultatelor, tabele și figuri, fără interpretare.",
        "content": "",
    },
    {
        "id": "s5",
        "title": "Discuții",
        "target_words": 2500,
        "description": "Interpretarea rezultatelor, comparația cu literatura, limitele studiului.",
        "content": "",
    },
    {
        "id": "s6",
        "title": "Concluzii și contribuții",
        "target_words": 1000,
        "description": "Sinteza contribuțiilor, implicații practice și direcții viitoare.",
        "content": "",
    },
    {
        "id": "s7",
        "title": "Bibliografie",
        "target_words": 0,
        "description": "Referințe în stilul cerut de facultate (APA, IEEE etc.).",
        "content": "",
    },
]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# The research plan lives beside the outline, not inside it, so the two used to
# read as unrelated documents. Each plan field has a natural chapter; this map
# is what lets the structure tab and the exported thesis show the plan where it
# is actually used instead of in a separate block nobody reads.
PLAN_FIELD_KEYS = (
    "participants", "instruments", "hypotheses", "variables",
    "analysis", "ethics", "schedule", "risks",
)

_PLAN_SECTION_HINTS: dict[str, tuple[str, ...]] = {
    "participants": ("metodolog", "metod", "particip", "esantion", "sample", "procedur"),
    "instruments": ("metodolog", "metod", "instrument", "masur", "measure", "chestionar", "scal"),
    "hypotheses": ("ipotez", "cadru conceptual", "conceptual", "model cauzal"),
    "variables": ("variabil", "cadru conceptual", "conceptual", "design"),
    "analysis": ("analiz", "rezultat", "statistic", "metodolog", "metod"),
    "ethics": ("etic", "consim", "procedur", "metodolog", "metod"),
    "schedule": ("calendar", "cronolog", "etap", "planific", "termen"),
    "risks": ("discut", "limit", "concluz", "risc", "amenint", "validitate"),
}


def _plan_map(project: dict) -> dict:
    """Which chapter carries each research-plan field.

    Deterministic and explainable: the chapter whose title/description shares
    the most hint words wins, earliest chapter on a tie. No LLM call — the map
    must be the same every time the screen opens.
    """
    plan = project.get("plan") if isinstance(project.get("plan"), dict) else {}
    sections = project.get("structure") or []
    prepared = [
        (section.get("id"), _fold(f"{section.get('title') or ''} {section.get('description') or ''}"))
        for section in sections
        if isinstance(section, dict)
    ]

    by_key: dict[str, str] = {}
    per_section: dict[str, list[str]] = {sid: [] for sid, _ in prepared if sid}
    for key in PLAN_FIELD_KEYS:
        best_id, best_score = None, 0
        for section_id, haystack in prepared:
            if not section_id:
                continue
            score = sum(1 for hint in _PLAN_SECTION_HINTS[key] if hint in haystack)
            if score > best_score:
                best_id, best_score = section_id, score
        if best_id:
            by_key[key] = best_id
            per_section[best_id].append(key)

    filled = sum(1 for key in PLAN_FIELD_KEYS if str(plan.get(key) or "").strip())
    return {
        "by_key": by_key,
        "sections": per_section,
        "unmapped": [key for key in PLAN_FIELD_KEYS if key not in by_key],
        "filled": filled,
        "total": len(PLAN_FIELD_KEYS),
    }


# ── Storage helpers ────────────────────────────────────────────────────────

def _row_to_project(row) -> dict:
    data = dict(row)
    try:
        structure = json.loads(data.get("structure_json") or "[]")
    except (json.JSONDecodeError, TypeError):
        structure = []
    data["structure"] = structure if isinstance(structure, list) else []
    data.pop("structure_json", None)
    # The research plan (participants, instruments, hypotheses, …) is stored as
    # JSON text so a new field needs no migration; expose it parsed.
    try:
        plan = json.loads(data.get("plan_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        plan = {}
    data["plan"] = plan if isinstance(plan, dict) else {}
    data.pop("plan_json", None)
    # The plan is stored next to the outline; this tells the UI (and the export)
    # which chapter each field belongs to, so the two stop looking unrelated.
    data["plan_map"] = _plan_map(data)
    return data


def _get_active_project_id(conn) -> int:
    row = conn.execute("SELECT value FROM app_settings WHERE key = 'active_project_id'").fetchone()
    if row and row["value"].isdigit():
        pid = int(row["value"])
        if conn.execute("SELECT id FROM project WHERE id = ?", (pid,)).fetchone():
            return pid
    first = conn.execute("SELECT id FROM project ORDER BY id ASC LIMIT 1").fetchone()
    if first:
        return first["id"]
    return 1


def _set_active_project_id(conn, pid: int) -> None:
    conn.execute(
        "INSERT INTO app_settings (key, value, updated_at) VALUES ('active_project_id', ?, datetime('now')) ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = datetime('now')",
        [str(pid), str(pid)]
    )
    conn.commit()


def _ensure_project(conn, project_id: Optional[int] = None) -> dict:
    pid = project_id if project_id is not None else _get_active_project_id(conn)
    row = conn.execute("SELECT * FROM project WHERE id = ?", (pid,)).fetchone()
    if row is None:
        row = conn.execute("SELECT * FROM project ORDER BY id ASC LIMIT 1").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO project (title, work_type, structure_json, plan_json) VALUES ('', 'licenta', ?, '{}')",
                [json.dumps(_PLACEHOLDER_STRUCTURE, ensure_ascii=False)],
            )
            conn.commit()
            row = conn.execute("SELECT * FROM project ORDER BY id DESC LIMIT 1").fetchone()
        if row:
            pid = row["id"]
            _set_active_project_id(conn, pid)
    return _row_to_project(row)


# ── Keyword matching against the real study library ────────────────────────


def _keywords(text: str) -> list[str]:
    """Lowercase content words, accents folded, stop words removed."""
    folded = fold(text)
    words = re.findall(r"[a-z0-9][a-z0-9\-]{2,}", folded)
    out: list[str] = []
    for w in words:
        if w in _RO_STOP or w.isdigit():
            continue
        if w not in out:
            out.append(w)
    return out


def _study_rows(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT id, title, content, tags, study_type, authors, entry_date,
                  quality, design, effect_direction, pdf_path, source, doi
             FROM entries
            WHERE archived = 0
              AND (study_type IS NOT NULL AND study_type != ''
                   OR tags LIKE '%study%'
                   OR tags LIKE '%pdf%'
                   OR pdf_path IS NOT NULL AND pdf_path != '')"""
    ).fetchall()
    return [dict(r) for r in rows]


def _score_study(study: dict, terms: list[str]) -> tuple[float, list[str]]:
    """Relevance of one study to the project terms: title counts double."""
    title = (study.get("title") or "").lower()
    body = " ".join(
        str(study.get(k) or "") for k in ("tags", "content", "design", "authors")
    ).lower()
    hits: list[str] = []
    score = 0.0
    for term in terms:
        in_title = term in title
        in_body = term in body
        if in_title or in_body:
            hits.append(term)
            score += 3.0 if in_title else 1.0
    if study.get("quality"):
        score += 0.4 * float(study["quality"])
    return score, hits


def _rank_studies(conn, project: dict, limit: int = 12) -> list[dict]:
    terms = _keywords(
        " ".join(
            str(project.get(k) or "")
            for k in ("title", "field", "description", "research_q")
        )
    )
    studies = _study_rows(conn)
    if not terms:
        # No project text yet: show the best-rated studies so the page is useful.
        studies.sort(key=lambda s: (-(s.get("quality") or 0), s.get("title") or ""))
        return [
            {
                "entry_id": s["id"],
                "title": s.get("title") or "",
                "year": (s.get("entry_date") or "")[:4],
                "study_type": s.get("study_type") or "",
                "design": s.get("design") or "",
                "quality": s.get("quality"),
                "effect_direction": s.get("effect_direction") or "",
                "authors": s.get("authors") or "",
                "has_pdf": bool(s.get("pdf_path")),
                "score": 0.0,
                "matched_terms": [],
                "reason": "Fără titlu/descriere de proiect încă — apar studiile cu cea mai bună calitate.",
            }
            for s in studies[:limit]
        ]

    scored = []
    for s in studies:
        score, hits = _score_study(s, terms)
        if score > 0:
            scored.append((score, hits, s))
    scored.sort(key=lambda x: (-x[0], -(x[2].get("quality") or 0)))

    out = []
    for score, hits, s in scored[:limit]:
        reason = "Se potrivește pe: " + ", ".join(hits[:5])
        if s.get("design"):
            reason += f" · design: {s['design']}"
        out.append({
            "entry_id": s["id"],
            "title": s.get("title") or "",
            "year": (s.get("entry_date") or "")[:4],
            "study_type": s.get("study_type") or "",
            "design": s.get("design") or "",
            "quality": s.get("quality"),
            "effect_direction": s.get("effect_direction") or "",
            "authors": s.get("authors") or "",
            "has_pdf": bool(s.get("pdf_path")),
            "score": round(score, 2),
            "matched_terms": hits[:8],
            "reason": reason,
        })
    return out


# ── Rule-based advice (always available, no LLM needed) ────────────────────

def _structural_feedback(project: dict) -> list[dict]:
    """Deterministic checks on the project and its outline."""
    items: list[dict] = []
    structure = project.get("structure") or []

    def add(level: str, message: str, action: str = "") -> None:
        items.append({"level": level, "message": message, "action": action})

    if not (project.get("title") or "").strip():
        add("critical", "Proiectul nu are încă un titlu.", "Scrie titlul lucrării sus, ca recomandările să se potrivească temei.")
    elif len(project["title"].split()) < 4:
        add("warning", "Titlul e foarte scurt — de obicei un titlu academic are 6–15 cuvinte.", "Adaugă obiectul și populația studiului în titlu.")

    if not (project.get("research_q") or "").strip():
        add("critical", "Lipsește întrebarea de cercetare.", "Formulează o întrebare la care lucrarea răspunde concret.")

    if not (project.get("methodology") or "").strip():
        add("warning", "Metodologia nu e descrisă.", "Notează designul (experimental, corelațional, studiu de caz) și instrumentele.")

    if not structure:
        add("critical", "Structura lucrării e goală.", "Apasă „Exemplu de structură” ca punct de plecare.")

    total_words = 0
    empty_sections = []
    for section in structure:
        words = len((section.get("content") or "").split())
        total_words += words
        if words == 0 and section.get("target_words", 0) > 0:
            empty_sections.append(section.get("title") or "?")

    if empty_sections:
        add(
            "info",
            f"{len(empty_sections)} secțiuni sunt încă goale: {', '.join(empty_sections[:4])}"
            + ("…" if len(empty_sections) > 4 else ""),
            "Începe cu Introducerea — restul se sprijină pe ea.",
        )

    if total_words:
        add("info", f"Ai scris aproximativ {total_words} cuvinte până acum.", "")

    return items


# ── LLM helpers ────────────────────────────────────────────────────────────

async def _llm(messages: list, *, temperature: float = 0.3, max_tokens: int = 1600) -> str | None:
    """Return the model's text, or None when no provider is usable.

    `deepseek-flash` is a reasoning model: it can spend the whole token budget
    thinking and return an empty string. A review prompt that needs several
    sections therefore gets headroom well above its expected length — otherwise
    the call succeeds with 200 OK and nothing to show for it.
    """
    if LLM_PROVIDER == "mock":
        return None
    try:
        _, _, text = await llm_complete(messages, temperature=temperature, max_tokens=max_tokens)
        return (text or "").strip() or None
    except Exception as exc:  # noqa: BLE001 - provider errors are heterogeneous
        logger.warning("Project LLM call failed: %s", exc)
        return None


def _project_brief(project: dict) -> str:
    structure = project.get("structure") or []
    outline = "\n".join(
        f"  {i+1}. {s.get('title')} (~{s.get('target_words', 0)} cuvinte, "
        f"{len((s.get('content') or '').split())} scrise) — {s.get('description', '')}"
        for i, s in enumerate(structure)
    ) or "  (structura nu e definită)"
    return f"""Titlu: {project.get('title') or '(nesetat)'}
Tip lucrare: {WORK_TYPES.get(project.get('work_type') or '', project.get('work_type') or '')}
Domeniu: {project.get('field') or '(nesetat)'}
Descriere: {project.get('description') or '(nesetată)'}
Întrebare de cercetare: {project.get('research_q') or '(nesetată)'}
Metodologie: {project.get('methodology') or '(nesetată)'}
Termen: {project.get('deadline') or '(nesetat)'}

Structura curentă:
{outline}"""


# ── Models (request bodies) ────────────────────────────────────────────────

from pydantic import BaseModel, Field  # noqa: E402  (kept next to the models)


class ProjectUpdate(BaseModel):
    title: str | None = None
    work_type: str | None = None
    field: str | None = None
    description: str | None = None
    research_q: str | None = None
    methodology: str | None = None
    deadline: str | None = None
    structure: list[dict] | None = None
    # The research plan: participants, instruments, hypotheses, ethics, analysis,
    # schedule. Free-form JSON so a new field needs no migration.
    plan: dict | None = None


class OutlineFromText(BaseModel):
    content: str = Field(..., min_length=1)


class IdeaCreate(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)
    category: str = "general"
    section_id: str = ""


class ResearchQuestionBody(BaseModel):
    """Optional topic overrides, so the prep check can run before saving."""
    title: str | None = None
    research_q: str | None = None
    field: str | None = None


class ProjectClaimCreate(BaseModel):
    section_id: str = Field(..., min_length=1, max_length=120)
    text: str = Field(..., min_length=1, max_length=4000)


class ProjectEvidenceCreate(BaseModel):
    entry_id: int
    document_id: int | None = None
    note_id: int | None = None
    relation: str = "supports"


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.get("/project")
def get_project():
    conn = get_db()
    try:
        return _ensure_project(conn)
    finally:
        conn.close()


def _docx_add_markdown(document, content: str) -> None:
    """Write simple chapter Markdown into the DOCX: headings, lists, bold/italic.

    The editor stores plain Markdown; a thesis export that printed `**bold**`
    verbatim would go straight back to the university.
    """
    import re as _re

    for raw_line in content.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if line.startswith("### "):
            document.add_heading(line[4:].strip(), level=3)
            continue
        if line.startswith("## "):
            document.add_heading(line[3:].strip(), level=2)
            continue
        if line.startswith("# "):
            document.add_heading(line[2:].strip(), level=2)
            continue
        bullet = _re.match(r"^[-*]\s+(.*)$", line.strip())
        numbered = _re.match(r"^\d+[.)]\s+(.*)$", line.strip())
        if bullet:
            paragraph = document.add_paragraph(style="List Bullet")
            text = bullet.group(1)
        elif numbered:
            paragraph = document.add_paragraph(style="List Number")
            text = numbered.group(1)
        else:
            paragraph = document.add_paragraph()
            text = line.strip()
        # Inline **bold** / *italic* runs.
        for token in _re.split(r"(\*\*[^*]+\*\*|\*[^*]+\*)", text):
            if not token:
                continue
            if token.startswith("**") and token.endswith("**") and len(token) > 4:
                paragraph.add_run(token[2:-2]).bold = True
            elif token.startswith("*") and token.endswith("*") and len(token) > 2:
                paragraph.add_run(token[1:-1]).italic = True
            else:
                paragraph.add_run(token)


@router.get("/project/export/docx")
def export_thesis_docx():
    """The thesis as .docx — title, chapters, and the derived bibliography.

    The university usually demands Word, not Markdown. The export mirrors the
    chapters as they are on screen (nothing generated, nothing invented) and
    appends the bibliography computed from the study library: works the text
    actually cites first, then the consulted library.
    """
    from docx import Document
    from docx.shared import Pt

    from app.routers.citations import _bibliography_references

    conn = get_db()
    try:
        project = _ensure_project(conn)
        references = _bibliography_references(conn)
    finally:
        conn.close()

    document = Document()
    normal = document.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(12)

    title = (project.get("title") or "").strip() or "Lucrare de licență"
    document.add_heading(title, level=0)
    meta_bits = [str(project.get("work_type") or "").strip(),
                 str(project.get("field") or "").strip()]
    meta_line = " · ".join(b for b in meta_bits if b)
    if meta_line:
        paragraph = document.add_paragraph(meta_line)
        paragraph.alignment = 1  # centered
    if str(project.get("research_q") or "").strip():
        paragraph = document.add_paragraph()
        paragraph.add_run("Întrebarea de cercetare: ").bold = True
        paragraph.add_run(str(project["research_q"]))

    sections = project.get("structure") or []
    for section in sections:
        document.add_heading(str(section.get("title") or "Capitol"), level=1)
        content = str(section.get("content") or "").strip()
        if not content:
            draft = document.add_paragraph("[Capitol în lucru]")
            draft.runs[0].italic = True
            continue
        _docx_add_markdown(document, content)

    cited = [r for r in references if r["cited"]]
    consulted = [r for r in references if not r["cited"]]
    document.add_heading("Bibliografie", level=1)
    if cited:
        for ref in cited:
            document.add_paragraph(ref["apa"], style="List Paragraph")
    if consulted:
        document.add_heading("Lucrări consultate", level=2)
        for ref in consulted:
            document.add_paragraph(ref["apa"], style="List Paragraph")
    if not references:
        document.add_paragraph("(Biblioteca nu are încă studii salvate.)")

    buffer = io.BytesIO()
    document.save(buffer)
    buffer.seek(0)
    safe_title = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "teza"
    filename = f"{safe_title}-{datetime.now().date().isoformat()}.docx"
    return Response(
        content=buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.put("/project")
def update_project(body: ProjectUpdate):
    conn = get_db()
    try:
        project = _ensure_project(conn)
        pid = project["id"]
        fields, params = [], []
        for key in ("title", "work_type", "field", "description", "research_q", "methodology", "deadline"):
            value = getattr(body, key)
            if value is not None:
                fields.append(f"{key} = ?")
                params.append(value)
        if body.structure is not None:
            fields.append("structure_json = ?")
            params.append(json.dumps(body.structure, ensure_ascii=False))
        if body.plan is not None:
            fields.append("plan_json = ?")
            params.append(json.dumps(body.plan, ensure_ascii=False))
        if fields:
            # A changed title, question or outline makes the stored analysis
            # answer an older question. It is NOT deleted: the user paid for
            # that text with an LLM call, so it stays readable and is flagged
            # stale through analysis_key, with regeneration one click away.
            fields.append("updated_at = ?")
            params.append(_now())
            params.append(pid)
            conn.execute(f"UPDATE project SET {', '.join(fields)} WHERE id = ?", params)
            conn.commit()
        row = conn.execute("SELECT * FROM project WHERE id = ?", (pid,)).fetchone()
        return _row_to_project(row)
    finally:
        conn.close()


@router.get("/project/studies")
def project_studies(limit: int = 12):
    """Studies from the library that fit the project topic, ranked."""
    conn = get_db()
    try:
        project = _ensure_project(conn)
        ranked = _rank_studies(conn, project, limit=max(1, min(limit, 50)))
        return {
            "terms": _keywords(
                " ".join(
                    str(project.get(k) or "")
                    for k in ("title", "field", "description", "research_q")
                )
            )[:15],
            "studies": ranked,
            "total_library": len(_study_rows(conn)),
        }
    finally:
        conn.close()


def _project_evidence_map(conn, project: dict) -> dict:
    """Build the thesis map from persisted sections, claims, notes and studies."""
    sections = project.get("structure") or []
    section_by_id = {str(section.get("id")): section for section in sections if section.get("id")}
    claim_rows = conn.execute(
        "SELECT * FROM project_claims ORDER BY id"
    ).fetchall()

    # Two batched reads instead of one query per claim plus one per note:
    # the whole evidence table, then the sources of every involved note.
    all_evidence: dict[int, list] = {}
    for r in conn.execute(
        """SELECT ce.id, ce.claim_id, ce.entry_id, ce.document_id, ce.note_id, ce.relation, ce.created_at,
                  e.title AS study_title, e.authors AS study_authors,
                  d.chunk_index, d.page_number AS document_page, d.content AS document_content,
                  n.content AS note_content, n.page_number AS note_page
             FROM project_claim_evidence ce
             JOIN entries e ON e.id = ce.entry_id AND e.archived = 0
             LEFT JOIN documents d ON d.id = ce.document_id
             LEFT JOIN study_notes n ON n.id = ce.note_id
            ORDER BY ce.id"""
    ).fetchall():
        all_evidence.setdefault(r["claim_id"], []).append(r)

    note_ids = {r["note_id"] for rows in all_evidence.values() for r in rows if r["note_id"] is not None}
    sources_by_note: dict[int, list] = {}
    if note_ids:
        placeholders = ",".join("?" * len(note_ids))
        for r in conn.execute(
            f"""SELECT ns.note_id, d.id AS document_id, d.entry_id, d.chunk_index, d.page_number, d.content
                  FROM study_note_sources ns JOIN documents d ON d.id = ns.document_id
                 WHERE ns.note_id IN ({placeholders}) ORDER BY d.chunk_index""",
            list(note_ids),
        ).fetchall():
            sources_by_note.setdefault(r["note_id"], []).append(r)

    claims: list[dict] = []
    for row in claim_rows:
        claim = dict(row)
        claim["section_id"] = str(claim["section_id"])
        claim["section_title"] = (
            section_by_id.get(claim["section_id"], {}).get("title") or "Capitol fără structură"
        )
        evidence_rows = all_evidence.get(claim["id"], [])
        evidence = []
        for evidence_row in evidence_rows:
            item = dict(evidence_row)
            document = None
            if item.get("document_id") is not None and item.get("document_content") is not None:
                document = {
                    "document_id": item["document_id"],
                    "entry_id": item["entry_id"],
                    "chunk_index": item["chunk_index"],
                    "page_number": item["document_page"],
                    "page_verified": item["document_page"] is not None,
                    "preview": item["document_content"][:400].replace("\n", " "),
                }
            note = None
            if item.get("note_id") is not None and item.get("note_content") is not None:
                source_rows = sources_by_note.get(item["note_id"], [])
                note = {
                    "note_id": item["note_id"],
                    "page_number": item["note_page"],
                    "content": item["note_content"][:800],
                    "sources": [
                        {
                            "document_id": source["document_id"],
                            "entry_id": source["entry_id"],
                            "chunk_index": source["chunk_index"],
                            "page_number": source["page_number"],
                            "page_verified": source["page_number"] is not None,
                            "preview": (source["content"] or "")[:300].replace("\n", " "),
                        }
                        for source in source_rows
                    ],
                }
            evidence.append({
                "id": item["id"],
                "entry_id": item["entry_id"],
                "study_title": item["study_title"],
                "study_authors": item["study_authors"],
                "relation": item["relation"],
                "document": document,
                "note": note,
                "created_at": item["created_at"],
            })
        claim["evidence"] = evidence
        claims.append(claim)

    by_section: dict[str, list[dict]] = {}
    orphan_claims = []
    for claim in claims:
        if claim["section_id"] in section_by_id:
            by_section.setdefault(claim["section_id"], []).append(claim)
        else:
            orphan_claims.append(claim)

    mapped_sections = []
    written_words = 0
    target_words = 0
    sections_with_claims = 0
    sections_with_evidence = 0
    for section in sections:
        section_id = str(section.get("id") or "")
        text = str(section.get("content") or "")
        word_count = len(re.findall(r"\S+", text))
        target = max(0, int(section.get("target_words") or 0))
        section_claims = by_section.get(section_id, [])
        if section_claims:
            sections_with_claims += 1
        if any(claim["evidence"] for claim in section_claims):
            sections_with_evidence += 1
        written_words += word_count
        target_words += target
        mapped_sections.append({
            "id": section_id,
            "title": str(section.get("title") or "Capitol fără titlu"),
            "description": str(section.get("description") or ""),
            "target_words": target,
            "written_words": word_count,
            "claims": section_claims,
        })

    claims_with_evidence = sum(1 for claim in claims if claim["evidence"])
    claims_with_pdf_fragment = sum(
        1 for claim in claims
        if any(ev["document"] or (ev["note"] and ev["note"]["sources"]) for ev in claim["evidence"])
    )
    return {
        "sections": mapped_sections,
        "orphan_claims": orphan_claims,
        "progress": {
            "written_words": written_words,
            "target_words": target_words,
            "writing_percent": min(100, round(written_words * 100 / target_words)) if target_words else 0,
            "sections_with_claims": sections_with_claims,
            "sections_with_evidence": sections_with_evidence,
            "total_sections": len(sections),
            "claims_with_evidence": claims_with_evidence,
            "claims_with_pdf_fragment": claims_with_pdf_fragment,
            "total_claims": len(claims),
            "evidence_percent": round(claims_with_evidence * 100 / len(claims)) if claims else None,
        },
    }


@router.get("/project/map")
def project_evidence_map():
    """Persisted chapters → claims → real study fragments map and actual progress."""
    conn = get_db()
    try:
        project = _ensure_project(conn)
        return _project_evidence_map(conn, project)
    finally:
        conn.close()


@router.post("/project/claims", status_code=201)
def create_project_claim(body: ProjectClaimCreate):
    section_id = body.section_id.strip()
    claim_text = body.text.strip()
    if not claim_text:
        raise HTTPException(400, "Afirmația nu poate fi goală")
    conn = get_db()
    try:
        project = _ensure_project(conn)
        section_ids = {str(section.get("id")) for section in project.get("structure") or []}
        if section_id not in section_ids:
            raise HTTPException(400, "Capitolul nu există în structura curentă")
        cur = conn.execute(
            "INSERT INTO project_claims (section_id, text) VALUES (?, ?)",
            [section_id, claim_text],
        )
        conn.commit()
        row = conn.execute("SELECT * FROM project_claims WHERE id = ?", [cur.lastrowid]).fetchone()
        return {**dict(row), "evidence": []}
    finally:
        conn.close()


@router.delete("/project/claims/{claim_id}", status_code=204)
def delete_project_claim(claim_id: int):
    conn = get_db()
    try:
        cur = conn.execute("DELETE FROM project_claims WHERE id = ?", [claim_id])
        conn.commit()
        if not cur.rowcount:
            raise HTTPException(404, "Afirmația nu există")
    finally:
        conn.close()


@router.post("/project/claims/{claim_id}/evidence", status_code=201)
def add_project_evidence(claim_id: int, body: ProjectEvidenceCreate):
    relation = body.relation.strip().lower()
    if relation not in {"supports", "contradicts", "contextualizes"}:
        raise HTTPException(400, "Relația trebuie să fie supports, contradicts sau contextualizes")
    conn = get_db()
    try:
        claim = conn.execute("SELECT id FROM project_claims WHERE id = ?", [claim_id]).fetchone()
        if not claim:
            raise HTTPException(404, "Afirmația nu există")
        study = conn.execute(
            "SELECT id FROM entries WHERE id = ? AND archived = 0", [body.entry_id]
        ).fetchone()
        if not study:
            raise HTTPException(404, "Studiul nu există sau este arhivat")
        if body.document_id is not None:
            doc = conn.execute(
                "SELECT id FROM documents WHERE id = ? AND entry_id = ?",
                [body.document_id, body.entry_id],
            ).fetchone()
            if not doc:
                raise HTTPException(400, "Fragmentul PDF nu aparține studiului selectat")
        if body.note_id is not None:
            note = conn.execute(
                "SELECT id FROM study_notes WHERE id = ? AND entry_id = ?",
                [body.note_id, body.entry_id],
            ).fetchone()
            if not note:
                raise HTTPException(400, "Notița nu aparține studiului selectat")
        existing = conn.execute(
            """SELECT id FROM project_claim_evidence
                 WHERE claim_id = ? AND entry_id = ?
                   AND document_id IS ? AND note_id IS ? AND relation = ?""",
            [claim_id, body.entry_id, body.document_id, body.note_id, relation],
        ).fetchone()
        if existing:
            raise HTTPException(409, "Această dovadă este deja legată de afirmație")
        cur = conn.execute(
            """INSERT INTO project_claim_evidence
               (claim_id, entry_id, document_id, note_id, relation)
               VALUES (?, ?, ?, ?, ?)""",
            [claim_id, body.entry_id, body.document_id, body.note_id, relation],
        )
        conn.commit()
        return {"id": cur.lastrowid, "claim_id": claim_id, "entry_id": body.entry_id,
                "document_id": body.document_id, "note_id": body.note_id, "relation": relation}
    finally:
        conn.close()


@router.delete("/project/claims/{claim_id}/evidence/{evidence_id}", status_code=204)
def delete_project_evidence(claim_id: int, evidence_id: int):
    conn = get_db()
    try:
        cur = conn.execute(
            "DELETE FROM project_claim_evidence WHERE id = ? AND claim_id = ?",
            [evidence_id, claim_id],
        )
        conn.commit()
        if not cur.rowcount:
            raise HTTPException(404, "Legătura de dovadă nu există")
    finally:
        conn.close()


@router.get("/project/evidence")
def project_evidence_for_study(entry_id: int):
    """Reverse lookup: find the claims and chapters a study supports."""
    conn = get_db()
    try:
        project = _ensure_project(conn)
        sections = {str(s.get("id")): str(s.get("title") or "") for s in project.get("structure") or []}
        rows = conn.execute(
            """SELECT ce.id AS evidence_id, ce.relation, ce.document_id, ce.note_id,
                      c.id AS claim_id, c.text AS claim, c.section_id,
                      d.chunk_index, d.page_number, d.content AS document_content,
                      n.content AS note_content
                 FROM project_claim_evidence ce
                 JOIN project_claims c ON c.id = ce.claim_id
                 JOIN entries e ON e.id = ce.entry_id AND e.archived = 0
                 LEFT JOIN documents d ON d.id = ce.document_id
                 LEFT JOIN study_notes n ON n.id = ce.note_id
                WHERE ce.entry_id = ? ORDER BY ce.id""",
            [entry_id],
        ).fetchall()
        return [
            {
                "evidence_id": row["evidence_id"],
                "relation": row["relation"],
                "claim_id": row["claim_id"],
                "claim": row["claim"],
                "section_id": row["section_id"],
                "section_title": sections.get(str(row["section_id"]), "Capitol fără structură"),
                "document": (
                    {
                        "document_id": row["document_id"],
                        "entry_id": entry_id,
                        "chunk_index": row["chunk_index"],
                        "page_number": row["page_number"],
                        "page_verified": row["page_number"] is not None,
                        "preview": (row["document_content"] or "")[:300].replace("\n", " "),
                    }
                    if row["document_id"] is not None and row["document_content"] is not None else None
                ),
                "note_id": row["note_id"],
                "note_preview": (row["note_content"] or "")[:300] if row["note_id"] is not None else None,
            }
            for row in rows
        ]
    finally:
        conn.close()


def _analysis_key(project: dict, ranked: list[dict], stats: dict, questionnaires: list = None) -> str:
    """Fingerprint of everything the analysis depends on.

    Two runs with the same title, question, outline and study set must produce
    the same key, so the stored text is reused instead of paying for another
    LLM call. Anything that would change the answer changes the key.
    """
    q_sig = "~".join(str(q["id"]) for q in (questionnaires or []))
    material = "|".join([
        str(project.get("title") or ""),
        str(project.get("research_q") or ""),
        str(project.get("field") or ""),
        str(project.get("work_type") or ""),
        "~".join(str(s.get("entry_id")) for s in ranked),
        str(stats.get("library_studies")),
        str(stats.get("studies_with_doi")),
        q_sig,
    ])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _analysis_stats(studies: list[dict], ranked: list[dict]) -> dict:
    """The numbers behind the analysis, computed without any model."""
    directions: dict[str, int] = {}
    for s in studies:
        d = (s.get("effect_direction") or "").strip().lower()
        if d:
            directions[d] = directions.get(d, 0) + 1
    designs: dict[str, int] = {}
    for s in studies:
        d = (s.get("design") or "").strip().lower()
        if d:
            designs[d] = designs.get(d, 0) + 1
    return {
        "library_studies": len(studies),
        "matching_studies": len(ranked),
        "studies_with_pdf": sum(1 for s in studies if s.get("pdf_path")),
        "studies_with_doi": sum(1 for s in studies if (s.get("doi") or "").strip()),
        "effect_directions": directions,
        "designs": dict(sorted(designs.items(), key=lambda kv: -kv[1])[:6]),
    }


def _analysis_prompt(project: dict, ranked: list[dict], questionnaires: list = None) -> str:
    corpus = "\n".join(
        f"- [{s['entry_id']}] {s['title']} ({s['year'] or 'n/d'}) · {s['design'] or 'design n/d'} · "
        f"efect: {s['effect_direction'] or 'n/d'} · calitate: {s['quality'] or 'n/d'}/5 — {s['reason']}"
        for s in ranked[:12]
    ) or "(biblioteca nu are studii potrivite)"
    plan = project.get("plan") or {}
    plan_bits = "; ".join(
        f"{k}: {v}" for k, v in plan.items() if isinstance(v, str) and v.strip()
    )
    plan_line = f"\nPLANUL DE CERCETARE AL STUDENTULUI:\n{plan_bits}\n" if plan_bits else ""

    q_lines = ""
    if questionnaires:
        q_list = []
        for q in questionnaires:
            items_count = 0
            try:
                items_count = len(json.loads(q["items_json"] or "[]"))
            except Exception:
                pass
            q_list.append(f"- Chestionar: \"{q['title']}\" (Construct: {q['construct'] or 'n/d'}, Itemi: {items_count})")
        q_lines = "\nCHESTIONARE ȘI INSTRUMENTE DE CERCETARE CONSTRUITE:\n" + "\n".join(q_list) + "\n"

    return f"""Ești un îndrumător academic exigent și membru în comisia de evaluare pentru o lucrare de {WORK_TYPES.get(project.get('work_type') or '', 'licență')}.
Evaluează stadiul cercetării pe baza standardelor epistemologice moderne (paradigma IMRaD, distincția strictă între corelație și determinare cauzală, eșantionare, control metodologic și rigoare statistică/analitică).

{_project_brief(project)}
{plan_line}
{q_lines}
STUDII RELEVANTE DIN BIBLIOTECĂ:
{corpus}

Scrie în română o sinteză critică de 250–400 de cuvinte, structurată strict cu aceste secțiuni:
**Unde ești acum** — 2-3 propoziții despre maturitatea epistemologică a temei și acoperirea ei în literatura empirică existentă.
**Ce sprijină tema** — ce studii din bibliotecă (citează titlul scurt și anul) aduc dovezi empirice solide sau mecanisme cauzale directe în favoarea ipotezei.
**Ce lipsește** — lacune metodologice și **analitice** concrete: unde trebuie efectuate analize statistice sau calitative (ex. testarea ipotezelor pe variabile, consistența internă / Alpha Cronbach pentru chestionare, grupuri de control absente, moderatori neexplorați).
**Riscuri** — 2 riscuri metodologice majore (ex. confuzie cauzală vs corelațională, erori de eșantionare sau măsurare) și protocolul precis de prevenire.
**Următorul pas** — o singură acțiune imediată, concretă și măsurabilă pentru avansarea cercetării.

Folosește DOAR studiile din listă și chestionarele existente. Nu inventa referințe, autori sau cifre fictive."""


def _no_llm_note(project: dict) -> str:
    """What to say when no analysis text exists yet.

    It must not claim the model is missing when the model is there: an empty
    cache means "not generated yet", which is a different fact from "no LLM
    configured", and the screen shows the difference to the user.
    """
    if not (project.get("title") or "").strip():
        return ("Analiza e doar pe potriviri de cuvinte — "
                "adaugă un titlu pentru o analiză pe temă.")
    if LLM_PROVIDER == "mock":
        return ("Analiza e doar pe potriviri de cuvinte — "
                "fără LLM configurat nu pot scrie o interpretare.")
    return ("Analiza încă nu a fost scrisă. Cifrele de mai sus sunt reale și calculate pe "
            "bibliotecă; apasă „Generează” pentru interpretarea scrisă de model.")


def _right_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


async def _build_analysis(project: dict, ranked: list[dict], stats: dict, questionnaires: list = None) -> dict:
    """Generate the analysis with the model — the expensive path."""
    if LLM_PROVIDER == "mock" or not (project.get("title") or "").strip():
        return {"analysis": _no_llm_note(project), "stats": stats, "from_llm": False,
                "top_studies": ranked[:6], "cached": False, "stale": False,
                "generated_at": "", "needs_generation": True}
    text = await _llm([{"role": "system", "content": _analysis_prompt(project, ranked, questionnaires)}],
                      temperature=0.35, max_tokens=3000)
    return {
        "analysis": text or "Analiza nu a putut fi generată (LLM indisponibil).",
        "stats": stats,
        "from_llm": text is not None,
        "top_studies": ranked[:6],
        "cached": False,
        "stale": False,
        "generated_at": "",
        "needs_generation": text is None,
    }


@router.get("/project/analysis")
def project_analysis():
    """The analysis as it stands — read from storage, never a model call.

    This endpoint used to await the LLM on every open (4–7 s, and real money per
    visit). Now it returns what was generated last and says how old it is and
    whether the project changed since. `POST /project/analysis` regenerates.
    """
    conn = get_db()
    try:
        project = _ensure_project(conn)
        ranked = _rank_studies(conn, project, limit=20)
        studies = _study_rows(conn)
        questionnaires = conn.execute("SELECT id, title, construct, target_population, items_json FROM questionnaires").fetchall()
    finally:
        conn.close()

    stats = _analysis_stats(studies, ranked)
    stored = (project.get("analysis_json") or "").strip()

    if not stored:
        # Nothing generated yet: give the deterministic part at once and let the
        # user decide when to spend an LLM call.
        return {
            "analysis": _no_llm_note(project),
            "stats": stats,
            "from_llm": False,
            "top_studies": ranked[:6],
            "cached": False,
            "stale": False,
            "generated_at": "",
            "needs_generation": bool((project.get("title") or "").strip()) and LLM_PROVIDER != "mock",
        }

    stale = (project.get("analysis_key") or "") != _analysis_key(project, ranked, stats, questionnaires)
    return {
        "analysis": stored,
        "stats": stats,
        "from_llm": True,
        "top_studies": ranked[:6],
        "cached": True,
        "stale": stale,
        "generated_at": project.get("analysis_at") or "",
        "needs_generation": stale,
    }


@router.post("/project/analysis")
async def project_analysis_refresh():
    """Generate the analysis now and keep it for the next opens."""
    conn = get_db()
    try:
        project = _ensure_project(conn)
        pid = project["id"]
        ranked = _rank_studies(conn, project, limit=20)
        studies = _study_rows(conn)
        questionnaires = conn.execute("SELECT id, title, construct, target_population, items_json FROM questionnaires").fetchall()
    finally:
        conn.close()

    stats = _analysis_stats(studies, ranked)
    result = await _build_analysis(project, ranked, stats, questionnaires)

    if result["from_llm"]:
        stamp = _now()
        conn = get_db()
        try:
            conn.execute(
                "UPDATE project SET analysis_json = ?, analysis_at = ?, analysis_key = ? WHERE id = ?",
                [result["analysis"], stamp, _analysis_key(project, ranked, stats, questionnaires), pid],
            )
            conn.commit()
        finally:
            conn.close()
        result["generated_at"] = stamp
        result["cached"] = False
    return result


def _feedback_key(project: dict) -> str:
    """Fingerprint of everything the reviewer reads.

    Same idea as `_analysis_key`: the prompt is the project brief plus the text
    already written into the outline. Editing the title, the question or any
    section changes the key, so the next POST regenerates instead of serving a
    review of a paper that no longer exists.
    """
    sections = project.get("structure") or []
    material = "|".join([
        str(project.get("title") or ""),
        str(project.get("work_type") or ""),
        str(project.get("field") or ""),
        str(project.get("description") or ""),
        str(project.get("research_q") or ""),
        str(project.get("methodology") or ""),
        str(project.get("deadline") or ""),
        "~".join(
            f"{s.get('title') or ''}::{s.get('content') or ''}" for s in sections
        ),
    ])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _feedback_prompt(project: dict) -> str:
    """The reviewer prompt: the project brief plus whatever text exists."""
    written = "\n\n".join(
        f"## {s.get('title')}\n{(s.get('content') or '')[:1200]}"
        for s in (project.get("structure") or [])
        if (s.get("content") or "").strip()
    )
    return f"""Ești un recenzor academic de prestigiu pentru o lucrare de {WORK_TYPES.get(project.get('work_type') or '', 'licență')}.
Evaluează critic textul redactat și planul lucrării, urmărind claritatea argumentației științifice, rigoarea metodologică și coerența narativă.

{_project_brief(project)}

TEXT SCRIS PÂNĂ ACUM:
{written or '(încă nimic scris)'}

Răspunde în română, concis (maximum 300 de cuvinte), organizat strict în aceste secțiuni:
**Puncte tari** — 2 aspecte metodologice sau conceptuale bine articulate și fundamentate.
**De corectat** — 3 vulnerabilități concrete (ex. salturi logice nejustificate, confuzii cauzale, formulări vagi sau absența surselor empirice), însoțite de soluția exactă pe care trebuie să o aplice studentul.
**Structură** — 1 observație privind logica secvențială a capitolelor conform standardului IMRaD (teorie, metodă, rezultate, discuții critice).
Nu inventa citate sau studii. Fii direct, analitic și riguros."""


@router.get("/project/feedback", response_model=ProjectFeedback)
def project_feedback():
    """What to fix next — the rule-based checks, without any model call.

    The reviewer's notes used to be generated here, on every open: 11 s and a
    billed call per visit, triggered by the client on its own. A read now
    answers from `_structural_feedback` alone; only
    `POST /project/feedback/review` spends an LLM call.
    """
    conn = get_db()
    try:
        project = _ensure_project(conn)
    finally:
        conn.close()

    return {
        "items": _structural_feedback(project),
        "reviewer": None,
        "from_llm": False,
        "generated_at": None,
        "cached": False,
        "needs_generation": True,
    }


@router.post("/project/feedback/review", response_model=ProjectFeedback)
async def project_feedback_review():
    """Run the reviewer once and keep the text until the project changes.

    The stored review is keyed on its own inputs (`_feedback_key`), so a second
    click with nothing edited answers from the database in milliseconds instead
    of paying for the same text again.
    """
    conn = get_db()
    try:
        project = _ensure_project(conn)
        pid = project["id"]
        stored = (project.get("feedback_json") or "").strip()
        stored_key = project.get("feedback_key") or ""
        stored_at = project.get("feedback_at") or ""
    finally:
        conn.close()

    items = _structural_feedback(project)
    key = _feedback_key(project)

    if stored and stored_key == key:
        try:
            payload = json.loads(stored)
        except (json.JSONDecodeError, TypeError):
            payload = None
        reviewer = payload.get("reviewer") if isinstance(payload, dict) else None
        if reviewer:
            return {
                "items": items,
                "reviewer": reviewer,
                "from_llm": True,
                "generated_at": stored_at,
                "cached": True,
                "needs_generation": False,
            }

    written = any(
        (s.get("content") or "").strip() for s in (project.get("structure") or [])
    )
    reviewer = None
    if LLM_PROVIDER != "mock" and (written or (project.get("title") or "").strip()):
        reviewer = await _llm(
            [{"role": "system", "content": _feedback_prompt(project)}],
            temperature=0.3,
            max_tokens=3000,
        )

    if reviewer is None:
        # Nothing generated: leave any older text untouched and say so, rather
        # than caching a failure as if it were a review.
        return {
            "items": items,
            "reviewer": None,
            "from_llm": False,
            "generated_at": None,
            "cached": False,
            "needs_generation": True,
        }

    stamp = _now()
    conn = get_db()
    try:
        conn.execute(
            "UPDATE project SET feedback_json = ?, feedback_at = ?, feedback_key = ? WHERE id = ?",
            [json.dumps({"reviewer": reviewer}, ensure_ascii=False), stamp, key, pid],
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "items": items,
        "reviewer": reviewer,
        "from_llm": True,
        "generated_at": stamp,
        "cached": False,
        "needs_generation": False,
    }


@router.post("/project/structure/example")
async def structure_example(body: ProjectUpdate | None = None):
    """A ready-to-use outline for this kind of paper, tailored to the title."""
    conn = get_db()
    try:
        project = _ensure_project(conn)
    finally:
        conn.close()
    merged = dict(project)
    if body:
        for key in ("title", "work_type", "field", "description", "research_q", "methodology"):
            value = getattr(body, key)
            if value:
                merged[key] = value

    prompt = f"""Ești un metodolog universitar expert. Proiectează structura academică canonică pentru o {WORK_TYPES.get(merged.get('work_type') or '', 'lucrare de licență')} pe tema de mai jos, urmând paradigma IMRaD (Introducere, Metodologie, Rezultate empirice, Discuții și Concluzii).

{_project_brief(merged)}

Răspunde DOAR cu JSON valid, fără text introductiv sau concluziv, în formatul exact:
{{"sections": [{{"title": "numele capitolului", "target_words": 1500, "description": "ce conține capitolul: obiective concrete, teorii/variabile vizate și contribuție empirică pe această temă"}}]}}

Reguli academice:
- Generează între 6 și 9 capitole/secțiuni structurate logic:
  1. Introducere, motivația cercetării și formularea problemei.
  2. Fundamentare teoretică și evoluție cronologică a conceptelor (stadiul cunoașterii).
  3. Cadrul conceptual, modelul cauzal și ipotezele operaționalizate.
  4. Metodologia de cercetare: design empiric, eșantion, instrumente și controlul variabilelor.
  5. Analiza datelor și rezultate empirice (statistici / sinteză).
  6. Discuții critice: interpretare, comparație cu literatura, limitări și amenințări la validitate.
  7. Concluzii, implicații teoretico-aplicative și direcții viitoare.
- Totalul cuvintelor țintă trebuie să fie cuprins între 8000 și 12000 de cuvinte, echilibrat proporțional.
- Descrierile fiecărui capitol trebuie să numească elemente concrete din tema dată (nu generalități)."""
    text = await _llm([{"role": "system", "content": prompt}], temperature=0.4, max_tokens=3000)

    sections = None
    if text:
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            try:
                data = json.loads(match.group(0))
                raw = data.get("sections") or []
                sections = [
                    {
                        "id": f"s{i+1}",
                        "title": str(s.get("title") or f"Capitolul {i+1}"),
                        "target_words": int(s.get("target_words") or 1200),
                        "description": str(s.get("description") or ""),
                        "content": "",
                    }
                    for i, s in enumerate(raw)
                    if isinstance(s, dict)
                ]
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                logger.warning("Structure JSON unusable: %s", exc)

    if not sections:
        sections = [dict(s) for s in _PLACEHOLDER_STRUCTURE]
        return {
            "structure": sections,
            "from_llm": False,
            "note": "Structura standard (fără LLM): poți edita capitolele oricând.",
        }
    return {"structure": sections, "from_llm": True, "note": ""}


# ── Readiness: does the thesis have what it needs? ─────────────────────────
#
# The checks are deterministic and grounded in the real library, so the answer
# holds with or without an LLM. Each one names what exists, what is missing and
# the single next action — the difference between "you may write" and "here is
# what to do before you write".

_PLAN_KEYS = [
    ("participants", "Participanți", "Cine intră în studiu: criterii, număr, grupe."),
    ("instruments", "Instrumente", "Ce măsori și cu ce: chestionare, scale, aparatură."),
    ("hypotheses", "Ipoteze", "Ce te aștepți să găsești, formulat verificabil."),
    ("variables", "Variabile", "Independente, dependente, de control."),
    ("analysis", "Analiză", "Ce test statistic sau metodă calitativă aplici."),
    ("ethics", "Etică", "Consimțământ, aprobare, confidențialitate."),
    ("schedule", "Calendar", "Etape și termene până la predare."),
    ("risks", "Riscuri", "Ce poate merge prost și ce faci atunci."),
]


def _plan_of(project: dict) -> dict:
    plan = project.get("plan")
    return plan if isinstance(plan, dict) else {}


def _prep_items(project: dict, studies: list[dict], ranked: list[dict],
                theories: int, evidence: int, ideas: int, written: int) -> list[dict]:
    """One item per thing the thesis needs, with its state and next action."""
    items: list[dict] = []

    def add(area: str, state: str, have: str, missing: str, action: str,
            do_first: bool = False) -> None:
        items.append({
            "area": area, "state": state, "have": have,
            "missing": missing, "action": action, "do_first": do_first,
        })

    title = (project.get("title") or "").strip()
    rq = (project.get("research_q") or "").strip()
    plan = _plan_of(project)
    structure = project.get("structure") or []
    with_doi = [s for s in studies if (s.get("doi") or "").strip()]
    with_pdf = [s for s in studies if s.get("pdf_path")]
    with_both = [s for s in studies if (s.get("doi") or "").strip() and s.get("pdf_path")]
    real_content = [s for s in studies if len(s.get("content") or "") >= 200]

    # 1. Title
    words = len(title.split())
    if not title:
        add("Titlu", "missing", "niciun titlu",
            "titlul lucrării", "Scrie titlul sus: obiect + populație + context.", True)
    elif words < 5:
        add("Titlu", "partial", f"{words} cuvinte",
            "un titlu academic are de obicei 6–15 cuvinte",
            "Adaugă obiectul și populația studiului în titlu.")
    else:
        add("Titlu", "ready", f"{words} cuvinte", "", "")

    # 2. Research question
    if not rq:
        add("Întrebare de cercetare", "missing", "nimic formulat",
            "o întrebare la care lucrarea răspunde",
            "Formulează: „În ce măsură X influențează Y la Z?”", True)
    elif len(rq.split()) < 6:
        add("Întrebare de cercetare", "partial", f"{rq[:60]}…",
            "întrebarea e prea scurtă ca să ghideze un studiu",
            "Spune populația, variabila și relația studiată.")
    else:
        add("Întrebare de cercetare", "ready", rq[:80], "", "")

    # 3. Studies that match the topic
    if not ranked:
        add("Studii pe temă", "missing", "niciun studiu nu se potrivește",
            "cel puțin 5 studii pe temă",
            "Cere o cercetare pe temă din chat, apoi verifică ce intră în bibliotecă.", True)
    elif len(ranked) < 5:
        add("Studii pe temă", "partial", f"{len(ranked)} studii potrivite",
            "sub 5 studii e prea puțin pentru un cadru teoretic",
            "Caută încă 2-3 studii pe cele mai apropiate subteme.")
    else:
        add("Studii pe temă", "ready", f"{len(ranked)} studii potrivite", "", "")

    # 4. Verifiable sources: DOI or PDF
    if not studies:
        add("Surse verificabile", "missing", "biblioteca e goală", "studii cu DOI sau PDF",
            "Adaugă studii înainte de a scrie.", True)
    elif len(with_both) >= max(5, len(studies) // 3):
        add("Surse verificabile", "ready",
            f"{len(with_both)} studii au și DOI și PDF (din {len(studies)})", "", "")
    elif with_doi or with_pdf:
        add("Surse verificabile", "partial",
            f"{len(with_doi)} cu DOI, {len(with_pdf)} cu PDF, {len(with_both)} cu amândouă "
            f"(din {len(studies)})",
            f"{len(studies) - len(with_both)} studii fără sursă verificabilă",
            "Rezolvă DOI-uri pentru studiile fără ele, sau descarcă PDF-urile.")
    else:
        add("Surse verificabile", "missing", "niciun DOI, niciun PDF",
            "orice sursă verificabilă", "Rulează rezolvarea de DOI pe bibliotecă.", True)

    # 5. Content deep enough to cite
    thin = len(studies) - len(real_content)
    if not studies:
        add("Conținut citibil", "missing", "nimic", "studii cu text real",
            "Adaugă studii cu conținut, nu doar titluri.", True)
    elif thin > len(studies) // 2:
        add("Conținut citibil", "partial", f"{len(real_content)} din {len(studies)} au text peste 200 de caractere",
            f"{thin} studii sunt schelete — nu poți cita din ele",
            "Descarcă PDF-urile lipsă: Studies → Download all PDFs.")
    else:
        add("Conținut citibil", "ready",
            f"{len(real_content)} din {len(studies)} cu text real", "", "")

    # 6. Outline
    if not structure:
        add("Structură", "missing", "niciun capitol", "un cuprins",
            "Generează structura sau scrie capitolele manual.", True)
    else:
        targets = sum(int(s.get("target_words") or 0) for s in structure)
        add("Structură", "ready", f"{len(structure)} capitole, țintă ~{targets} cuvinte", "", "")

    # 7-14. The research plan, field by field
    for key, label, hint in _PLAN_KEYS:
        value = str(plan.get(key) or "").strip()
        if not value:
            add(label, "missing", "necompletat", hint,
                f"Completează „{label}” în schema temei.")
        elif len(value) < 15:
            add(label, "partial", value[:60], "prea scurt pentru a fi util",
                f"Detaliază „{label}”: cine, ce, cum.")
        else:
            add(label, "ready", value[:80], "", "")

    # Written text
    if written == 0:
        add("Text scris", "missing", "0 capitole cu text",
            "primul paragraf", "Scrie introducerea, oricât de provizorie.", True)
    else:
        add("Text scris", "ready" if written >= 2 else "partial",
            f"{written} capitole cu text", "", "")

    # Theories and ideas as support, not requirements
    add("Teorii legate", "ready" if theories else "partial",
        f"{theories} teorii, {evidence} dovezi legate",
        "" if theories else "nicio teorie în agregator",
        "" if theories else "Leagă 2-3 teorii de studii în fila Theories.")
    add("Idei notate", "ready" if ideas else "partial",
        f"{ideas} idei", "" if ideas else "nicio idee notată",
        "" if ideas else "Notează în fila Idei ce vrei să verifici.")

    return items


@router.get("/project/readiness")
def project_readiness():
    """Everything the thesis needs, checked against the real data."""
    conn = get_db()
    try:
        project = _ensure_project(conn)
        ranked = _rank_studies(conn, project, limit=50)
        studies = _study_rows(conn)
        theories = conn.execute("SELECT COUNT(*) FROM theories").fetchone()[0]
        evidence = conn.execute("SELECT COUNT(*) FROM theory_evidence").fetchone()[0]
        ideas = conn.execute("SELECT COUNT(*) FROM project_ideas").fetchone()[0]
    finally:
        conn.close()

    written = sum(1 for s in (project.get("structure") or [])
                  if (s.get("content") or "").strip())
    items = _prep_items(project, studies, ranked, theories, evidence, ideas, written)

    ready = sum(1 for i in items if i["state"] == "ready")
    partial = sum(1 for i in items if i["state"] == "partial")
    missing = sum(1 for i in items if i["state"] == "missing")
    total = len(items) or 1
    score = round(100 * (ready + 0.5 * partial) / total)

    obligations = _structural_feedback(project)

    return {
        "items": items,
        "summary": {
            "ready": ready, "partial": partial, "missing": missing,
            "total": len(items), "score": score,
            "verdict": ("poți începe să scrii" if score >= 75
                        else "aproape — mai lipsesc câteva piese" if score >= 45
                        else "fundația încă lipsește"),
            "next": next((i for i in items if i["state"] != "ready"), None),
        },
        "obligations": obligations,
    }


@router.get("/project/schema")
def project_schema():
    """The visual map of the thesis: question → theories → studies → variables.

    Built from the real rows, so the diagram shows what exists today, with the
    missing pieces named rather than drawn as if they were there.
    """
    conn = get_db()
    try:
        project = _ensure_project(conn)
        ranked = _rank_studies(conn, project, limit=12)
        studies = _study_rows(conn)
        theory_rows = conn.execute(
            "SELECT id, name, status FROM theories ORDER BY name LIMIT 12"
        ).fetchall()
        linked = conn.execute(
            """SELECT t.name AS theory, te.stance AS stance, COUNT(*) AS n
                 FROM theory_evidence te JOIN theories t ON t.id = te.theory_id
                GROUP BY t.name, te.stance
                ORDER BY n DESC LIMIT 12"""
        ).fetchall()
        pillars = conn.execute(
            """SELECT tags, COUNT(*) AS n FROM entries
                WHERE archived = 0 AND tags != ''
                GROUP BY tags ORDER BY n DESC LIMIT 40"""
        ).fetchall()
    finally:
        conn.close()

    # Topic pillars: the subject tags that carry the most entries.
    counts: dict[str, int] = {}
    for row in pillars:
        for tag in str(row["tags"] or "").split(","):
            tag = tag.strip()
            if tag and tag not in ("study", "pdf"):
                counts[tag] = counts.get(tag, 0) + int(row["n"])
    top_pillars = sorted(counts.items(), key=lambda kv: -kv[1])[:6]

    plan = _plan_of(project)
    design_counts: dict[str, int] = {}
    for s in studies:
        d = (s.get("design") or "").strip().lower()
        if d:
            design_counts[d] = design_counts.get(d, 0) + 1

    return {
        "question": (project.get("research_q") or "").strip(),
        "title": (project.get("title") or "").strip(),
        "work_type": project.get("work_type") or "",
        "pillars": [{"tag": t, "count": n} for t, n in top_pillars],
        "theories": [dict(r) for r in theory_rows],
        "evidence_links": [dict(r) for r in linked],
        "designs": [{"design": d, "count": n}
                    for d, n in sorted(design_counts.items(), key=lambda kv: -kv[1])[:6]],
        "top_studies": ranked[:8],
        "variables": {
            "independent": str(plan.get("variables") or ""),
            "instruments": str(plan.get("instruments") or ""),
            "analysis": str(plan.get("analysis") or ""),
        },
        "gaps": [i for i in _prep_items(
            project, studies, ranked, len(theory_rows), sum(r["n"] for r in linked), 0,
            sum(1 for s in (project.get("structure") or []) if (s.get("content") or "").strip()),
        ) if i["state"] == "missing"],
    }


# ── Ideas: the brainstorming pad ───────────────────────────────────────────

_IDEA_CATEGORIES = {
    "general", "teorie", "metoda", "rezultat", "limita", "citire", "intrebare",
}


@router.get("/project/ideas")
def list_ideas(category: str = ""):
    conn = get_db()
    try:
        if category:
            rows = conn.execute(
                "SELECT * FROM project_ideas WHERE category = ? ORDER BY id DESC", [category]
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM project_ideas ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/project/ideas", status_code=201)
def create_idea(body: IdeaCreate):
    category = body.category if body.category in _IDEA_CATEGORIES else "general"
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO project_ideas (text, category, section_id) VALUES (?, ?, ?)",
            [body.text.strip(), category, body.section_id or ""],
        )
        conn.commit()
        row = conn.execute("SELECT * FROM project_ideas WHERE id = ?", [cur.lastrowid]).fetchone()
        return dict(row)
    finally:
        conn.close()


@router.delete("/project/ideas/{idea_id}", status_code=204)
def delete_idea(idea_id: int):
    conn = get_db()
    try:
        cur = conn.execute("DELETE FROM project_ideas WHERE id = ?", [idea_id])
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Idea not found")
    finally:
        conn.close()


class IdeaUpdate(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    category: str | None = None


@router.put("/project/ideas/{idea_id}")
def update_idea(idea_id: int, body: IdeaUpdate):
    """Edit an idea in place — the list previously allowed only add/delete."""
    category = body.category if body.category in _IDEA_CATEGORIES else None
    conn = get_db()
    try:
        if category:
            cur = conn.execute(
                "UPDATE project_ideas SET text = ?, category = ? WHERE id = ?",
                [body.text.strip(), category, idea_id],
            )
        else:
            cur = conn.execute(
                "UPDATE project_ideas SET text = ? WHERE id = ?",
                [body.text.strip(), idea_id],
            )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Idea not found")
        row = conn.execute("SELECT * FROM project_ideas WHERE id = ?", [idea_id]).fetchone()
        return dict(row)
    finally:
        conn.close()


@router.post("/project/ideas/suggest")
async def suggest_ideas():
    """Optional: ask the model for ideas grounded in the real library."""
    conn = get_db()
    try:
        project = _ensure_project(conn)
        ranked = _rank_studies(conn, project, limit=10)
    finally:
        conn.close()

    if LLM_PROVIDER == "mock" or not (project.get("title") or "").strip():
        return {"ideas": [], "from_llm": False,
                "note": "Fără titlu sau fără LLM nu pot propune idei pe temă."}

    corpus = "\n".join(f"- {s['title']} ({s['year'] or 'n/d'})" for s in ranked) or "(gol)"
    prompt = f"""Ești un cercetător și conducător științific universitar. Studentul dezvoltă tema de cercetare descrisă mai jos.

{_project_brief(project)}

Studii din biblioteca lui:
{corpus}

Propune exact 6 idei de cercetare originale, falsificabile și riguros fundamentate empiric, fiecare formulată într-o singură frază clară.
Fiecare idee trebuie să fie ancorată direct în studiile de mai sus, indicând relații cauzale, moderatori metodologici sau comparații empirice concrete.
Răspunde DOAR cu JSON:
{{"ideas": [{{"text": "ideea de cercetare formulată ca ipoteză sau întrebare analitică", "category": "teorie|metoda|rezultat|limita|citire|intrebare"}}]}}
Nu inventa studii, autori sau date fictive."""
    text = await _llm([{"role": "system", "content": prompt}], temperature=0.5, max_tokens=800)
    ideas: list[dict] = []
    if text:
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            try:
                data = json.loads(match.group(0))
                for raw in (data.get("ideas") or [])[:8]:
                    if not isinstance(raw, dict):
                        continue
                    idea_text = str(raw.get("text") or "").strip()
                    if not idea_text:
                        continue
                    cat = str(raw.get("category") or "general").strip().lower()
                    ideas.append({"text": idea_text,
                                  "category": cat if cat in _IDEA_CATEGORIES else "general"})
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                logger.warning("Ideas JSON unusable: %s", exc)
    return {"ideas": ideas, "from_llm": bool(ideas)}


# ── Title analysis ─────────────────────────────────────────────────────────
#
# A thesis title is judged by what it lets the reader predict about the study.
# The checks below are the ones an advisor makes first, and they run without a
# model: length, the variables named, the population named, vague words, a
# question mark in a declarative title, and how far the title is from the
# research question actually written in the project.

_TITLE_VAGUE = {
    "aspecte", "aspect", "considerații", "consideratii", "probleme", "problematica",
    "cercetări", "cercetari", "studiu", "studii", "analiză", "analiza", "contribuții",
    "contributii", "unele", "diverse", "diferite", "abordări", "abordari",
    "perspective", "implicații", "implicatii", "elemente", "noi", "abordare",
}
# Words that name who is studied. A title without one leaves the reader guessing.
_POPULATION_WORDS = {
    "adulți", "adulti", "adult", "adulților", "adultilor", "adulții", "adultii", "adulte", "adultă", "adulta",
    "adolescenți", "adolescenti", "adolescent", "adolescenților", "adolescentilor", "adolescenții", "adolescentii",
    "copii", "copil", "copiilor", "copiii",
    "studenți", "studenti", "student", "studenților", "studentilor", "studenții", "studentii",
    "pacienți", "pacienti", "pacient", "pacienților", "pacientilor", "pacienții", "pacientii",
    "elevi", "elev", "elevilor", "elevii",
    "tineri", "tânăr", "tanar", "tinerilor", "tinerii", "tinere", "tineret",
    "vârstnici", "varstnici", "vârstnic", "varstnic", "vârstnicilor", "varstnicilor",
    "seniori", "seniorilor",
    "părinți", "parinti", "părinte", "parinte", "părinților", "parintilor",
    "profesori", "profesor", "profesorilor", "cadre", "cadrelor",
    "angajați", "angajati", "angajat", "angajaților", "angajatilor",
    "femei", "femeilor", "femeie", "bărbați", "barbati", "bărbat", "barbat", "bărbaților", "barbatilor",
    "mame", "mamelor", "mamă", "mama", "tați", "tati", "taților", "tatilor", "tată", "tata",
    "persoane", "persoanelor", "persoană", "persoana",
    "subiecți", "subiecti", "subiect", "subiecților", "subiectilor",
    "populație", "populatie", "populația", "populatia", "populației", "populatiei",
    "eșantion", "esantion", "eșantionului", "esantionului",
    "utilizatori", "utilizatorilor", "voluntari", "voluntarilor",
    "adults", "children", "adolescents", "students", "patients", "participants", "workers", "teachers",
    "women", "men", "elderly", "parents", "mothers", "fathers", "youth",
    "mice", "rats", "șoareci", "soareci", "animale",
}
_ROMANIAN_DIACRITICS = "ăâîșțĂÂÎȘȚ"


def _detect_population(lowered_words: list[str], full_lowered: str) -> list[str]:
    found = [w for w in lowered_words if w in _POPULATION_WORDS]
    if not found:
        stems = {
            "adult": "adulți",
            "adolescent": "adolescenți",
            "copi": "copii",
            "student": "studenți",
            "pacient": "pacienți",
            "tiner": "tineri",
            "varstnic": "vârstnici",
            "vârstnic": "vârstnici",
            "senior": "seniori",
            "parint": "părinți",
            "părint": "părinți",
            "profesor": "profesori",
            "angajat": "angajați",
            "femei": "femei",
            "barbat": "bărbați",
            "bărbat": "bărbați",
            "mama": "mame",
            "mamă": "mame",
            "tata": "tați",
            "tată": "tați",
            "persoan": "persoane",
            "subiect": "subiecți",
            "populat": "populație",
            "populaț": "populație",
            "esantion": "eșantion",
            "eșantion": "eșantion",
        }
        for w in lowered_words:
            for stem, canonical in stems.items():
                if stem in w:
                    found.append(canonical)
        if not found:
            for stem, canonical in stems.items():
                if stem in full_lowered:
                    found.append(canonical)
    return list(dict.fromkeys(found))


def _title_stats(title: str) -> dict:
    """The measurable shape of a title, computed without any model."""
    words = [w for w in re.split(r"\s+", (title or "").strip()) if w]
    lowered = [re.sub(r"[^\w\-]", "", w.lower()) for w in words]
    full_lowered = (title or "").lower()
    return {
        "words": len(words),
        "chars": len((title or "").strip()),
        "vague": [w for w in lowered if w in _TITLE_VAGUE],
        "population": _detect_population(lowered, full_lowered),
        "has_question_mark": "?" in (title or ""),
        "acronyms": [w for w in words if w.isupper() and 2 <= len(w) <= 6],
        "has_colon": ":" in (title or "") or "—" in (title or "") or " - " in (title or ""),
    }


def _title_score(checks: list[dict]) -> int:
    """0–100, where a missing title is 0 and each finding costs something.

    Computed in one place so the GET and the POST can never disagree. A
    critical finding costs heavily; a title that cannot be analysed at all
    scores nothing, instead of starting from 100 and losing one deduction.
    """
    critica = sum(1 for c in checks if c["level"] == "critical")
    avertismente = sum(1 for c in checks if c["level"] == "warning")
    if any("Nu există titlu" in c["message"] for c in checks):
        return 0
    return max(0, 100 - 30 * critica - 12 * avertismente)
def _title_checks(project: dict) -> list[dict]:
    """Deterministic findings about the title, each with what to do about it."""
    title = (project.get("title") or "").strip()
    found: list[dict] = []

    def add(level: str, message: str, action: str) -> None:
        found.append({"level": level, "message": message, "action": action})

    if not title:
        add("critical", "Nu există titlu de analizat.",
            "Scrie titlul sus, apoi revino: analiza îl citește pe el.")
        return found

    stats = _title_stats(title)

    if stats["words"] < 5:
        add("critical", f"Titlul are {stats['words']} cuvinte — prea puțin ca să spună ce studiezi.",
            "Adaugă obiectul și populația: „X la Y: o cercetare asupra Z”.")
    elif stats["words"] > 25:
        add("warning", f"Titlul are {stats['words']} cuvinte — un titlu academic stă de obicei sub 20.",
            "Mută nuanțele în subtitlu; păstrează în titlu doar obiectul și populația.")
    else:
        add("info", f"Lungimea e rezonabilă ({stats['words']} cuvinte).", "")

    if stats["vague"]:
        add("warning", "Cuvinte care nu spun nimic concret: " + ", ".join(sorted(set(stats["vague"]))) + ".",
            "Înlocuiește-le cu termenul real: nu „aspecte ale rezilienței”, ci „reziliența după abuz”.")

    if not stats["population"]:
        add("warning", "Titlul nu numește pe cine studiezi.",
            "Adaugă populația: adulți, adolescenți, studenți, pacienți cu PTSD…")
    else:
        add("info", "Populația e numită (" + ", ".join(sorted(set(stats["population"]))) + ").", "")

    rq = (project.get("research_q") or "").strip()
    if rq:
        # Overlap on content words: a title and a question about different
        # things is the most common mismatch an advisor circles in red.
        title_terms = set(_keywords(title))
        rq_terms = set(_keywords(rq))
        if title_terms and rq_terms:
            shared = title_terms & rq_terms
            ratio = len(shared) / max(1, min(len(title_terms), len(rq_terms)))
            if ratio < 0.34:
                add("warning",
                    "Titlul și întrebarea de cercetare vorbesc despre lucruri diferite "
                    f"(doar {len(shared)} termeni comuni).",
                    "Apropie-le: întrebarea ar trebui să fie versiunea întrebătoare a titlului.")
            else:
                add("info", f"Titlul se leagă de întrebarea de cercetare ({len(shared)} termeni comuni).", "")
    else:
        add("warning", "Nu există întrebare de cercetare, deci nu pot verifica dacă titlul o acoperă.",
            "Scrie întrebarea de cercetare, apoi reia analiza.")

    if stats["has_question_mark"] and not rq.endswith("?"):
        add("info", "Titlul e formulat ca întrebare — e o alegere legitimă, dar mai rară în lucrări.",
            "Dacă facultatea cere titlu declarativ, transformă-l în afirmație.")

    if len(stats["acronyms"]) >= 3:
        add("warning", "Titlul are " + str(len(stats["acronyms"])) + " acronime "
            + ", ".join(stats["acronyms"]) + ".",
            "Un titlu cu multe acronime nu se citește; desfă-le sau mută-le în subtitlu.")

    if not stats["has_colon"] and stats["words"] >= 8:
        add("info", "Fără subtitlu. Un „Temă: delimitare” ajută cititorul să vadă unghiul.",
            "")

    return found


@router.get("/project/title")
def project_title_analysis():
    """How the title stands: what it lets the reader predict, and what it hides.

    Deterministic part only — this endpoint never calls a model, so opening or
    refreshing it costs nothing. `POST /project/title` adds a reviewer's read.
    """
    conn = get_db()
    try:
        project = _ensure_project(conn)
    finally:
        conn.close()

    checks = _title_checks(project)
    score = _title_score(checks)
    return {
        "title": (project.get("title") or "").strip(),
        "research_q": (project.get("research_q") or "").strip(),
        "stats": _title_stats(project.get("title") or ""),
        "checks": checks,
        "score": score,
        "verdict": ("titlu solid" if score >= 85 else
                    "aproape — mai ajustează" if score >= 60 else
                    "titlul încă nu spune ce studiezi"),
        "review": None,
    }


@router.post("/project/title")
async def project_title_review():
    """Add a reviewer's reading of the title, grounded in the real project."""
    conn = get_db()
    try:
        project = _ensure_project(conn)
        ranked = _rank_studies(conn, project, limit=8)
    finally:
        conn.close()

    checks = _title_checks(project)
    score = _title_score(checks)

    title = (project.get("title") or "").strip()
    if LLM_PROVIDER == "mock" or not title:
        return {
            "title": title,
            "research_q": (project.get("research_q") or "").strip(),
            "stats": _title_stats(title),
            "checks": checks,
            "score": score,
            "verdict": ("titlu solid" if score >= 85 else
                        "aproape — mai ajustează" if score >= 60 else
                        "titlul încă nu spune ce studiezi"),
            "review": None,
            "from_llm": False,
            "note": ("Scrie un titlu mai întâi." if not title
                     else "Fără LLM configurat nu pot scrie o lectură critică."),
        }

    corpus = "\n".join(f"- {s['title']}" for s in ranked[:8]) or "(biblioteca nu are studii potrivite)"
    prompt = f"""Ești un membru al comisiei doctorale și îndrumător pentru o lucrare de {WORK_TYPES.get(project.get('work_type') or '', 'licență')}.
Analizează critic titlul propus de student din perspectiva clarității epistemologice, acurateței conceptuale și impactului științific.

TITLU: {title}
ÎNTREBARE DE CERCETARE: {project.get('research_q') or '(nesetată)'}
DOMENIU: {project.get('field') or '(nesetat)'}
STUDII DIN BIBLIOTECA LUI (pentru context, nu le cita ca dovezi):
{corpus}

Răspunde în română, concis (maximum 250 de cuvinte), respectând cu strictețe aceste secțiuni:
**Ce promite titlul** — ce așteptări metodologice și teoretice induce titlul unui comitet științific la prima lectură.
**Ce nu spune** — ce lipsește cu adevărat (mecanismul cauzal concret, delimitarea contextuală sau variabilele de control). IMPORTANT: dacă titlul menționează deja populația (ex: adulți, adolescenți, etc.), confirmă explicit acest lucru și NU susține că populația lipsește!
**Trei variante** — trei titluri alternative optimizate, numerotate clar:
1. Varianta centrată pe relația de cauzalitate/mecanism empiric.
2. Varianta cu subtitlu (cadru teoretic : aplicație/studiu empiric).
3. Varianta concisă, cu impact academic direct.
**Recomandarea mea** — care dintre variante oferă cel mai bun echilibru între specificitate și rigoare academică, într-o singură frază argumentată.

Nu inventa studii, autori sau date numerice fictive. Păstrează un ton academic direct și constructiv."""
    review = await _llm([{"role": "system", "content": prompt}], temperature=0.4, max_tokens=3000)

    return {
        "title": title,
        "research_q": (project.get("research_q") or "").strip(),
        "stats": _title_stats(title),
        "checks": checks,
        "score": score,
        "verdict": ("titlu solid" if score >= 85 else
                    "aproape — mai ajustează" if score >= 60 else
                    "titlul încă nu spune ce studiezi"),
        "review": review,
        "from_llm": review is not None,
        "note": "" if review else "LLM indisponibil — rămân verificările de mai sus.",
    }


# ── Choosing the topic ─────────────────────────────────────────────────────
#
# The hardest decision is not the wording of a title but which study to do.
# A topic is worth proposing only if the library can carry it: enough studies,
# some of them verifiable, spread over more than one design, with a real gap
# left to fill. Each candidate below is measured against the actual rows, so
# the screen can be honest about what is feasible today and what is not.

# Ready-made angles, phrased as research questions, for a library about adverse
# experience, context, theory of mind and resilience. They are seeds: the
# coverage of each is measured against the real library below, never assumed.
_TOPIC_SEEDS = [
    ("Experiențele adverse și reziliența la adolescenți",
     "Cum se leagă numărul experiențelor adverse de reziliența la adolescenți?"),
    ("Perceperea contextului și adaptarea după adversitate",
     "Cum mediază perceperea contextului relația dintre adversitate și adaptare?"),
    ("Theory of mind după trauma din copilărie",
     "Ce diferențe de theory of mind apar la copiii cu experiențe adverse?"),
    ("Modificări structurale cerebrale produse de meditație",
     "Ce modificări structurale cerebrale produce meditația mindfulness la adulți sănătoși?"),
    ("Reziliența și sănătatea mentală: factori protectivi",
     "Ce factori protectivi prezic reziliența în fața adversității?"),
    ("Memoria și somnul în consolidarea amintirilor emoționale",
     "Cum influențează somnul consolidarea amintirilor legate de evenimente adverse?"),
    ("Modularitatea minții și dezvoltarea conceptuală",
     "Cât de modulară este mintea în dezvoltarea conceptelor la copii?"),
    ("Conștiința și teoriile actuale despre ea",
     "Ce explică teoriile actuale ale conștiinței despre experiența subiectivă?"),
]


# The library is mostly English while the topics are written in Romanian, so a
# literal term match finds nothing. Fold the words a Romanian thesis about this
# field actually uses onto the English terms the studies are written in.
_RO_EN = {
    "adverse": "adverse", "experientele": "experience", "experiente": "experience",
    "experientelor": "experience", "rezilienta": "resilience", "rezilienței": "resilience",
    "adolescenti": "adolescen", "adolescenți": "adolescen", "copii": "child",
    "copiii": "child", "trauma": "trauma", "traumei": "trauma", "context": "context",
    "contextului": "context", "perceperea": "percept", "perceptia": "percept",
    "minte": "mind", "mintea": "mind", "minții": "mind", "teoria": "theory",
    "teorie": "theor", "modulare": "modul", "modularitatea": "modul",
    "modularitate": "modul", "dezvoltarea": "develop", "concepte": "concept",
    "conceptelor": "concept", "constiinta": "conscious", "conștiința": "conscious",
    "constiintei": "conscious", "memoria": "memory", "memorie": "memory",
    "somn": "sleep", "somnul": "sleep", "consolidarea": "consolidat",
    "emotionale": "emotion", "emotiilor": "emotion", "structurale": "structur",
    "cerebrale": "brain", "creierul": "brain", "creier": "brain",
    "meditatie": "meditat", "meditația": "meditat", "meditatia": "meditat",
    "mindfulness": "mindfulness", "adulti": "adult", "adulți": "adult",
    "sanatosi": "healthy", "sănătoși": "healthy", "sănătate": "health",
    "sanatate": "health", "mentala": "mental", "factori": "factor",
    "protectivi": "protective", "atentia": "attention", "inteligenta": "intellig",
    "personalitate": "personalit", "cognitie": "cognit", "limbaj": "language",
    "memoria": "memory", "neurobiologie": "neurobiolog", "plasticitate": "plastic",
    "adversitate": "advers", "adaptare": "adapt", "risc": "risk",
}


def _bilingual_keywords(text: str) -> list[str]:
    """Keywords from a Romanian phrase, as English fragments where known.

    Both the original word and its English stem are kept: a study may carry
    either ("reziliență" in a user tag, "resilience" in an English title).
    """
    out: list[str] = []
    for w in _keywords(text):
        if w not in out:
            out.append(w)
        mapped = _RO_EN.get(w)
        if mapped and mapped not in out:
            out.append(mapped)
    return out


# Words that appear in so many titles that matching one of them alone says
# nothing about a topic. A match needs a specific word, or two of any kind.
_GENERIC_TERMS = {
    "structur", "structurale", "structura", "structure", "brain", "cerebrale", "creier",
    "study", "studii", "analysis", "analiza", "review", "theory", "teorii", "teorie",
    "theor", "effect", "efect", "role", "rolul", "mind", "minte", "mintea",
    "develop", "dezvoltarea", "dezvoltare", "model", "models", "date", "data",
    "children", "child", "copii", "copiii", "adult", "adulti", "adulți",
}


def _coverage(conn, title: str, question: str) -> dict:
    """How far the real library can carry a topic — measured, not guessed.

    Matched against every non-archived entry, not only the ones the app calls
    "studies": a note, a theory summary or a reading can carry a chapter even
    when it has no study_type. Study-specific counts (DOI, design) are reported
    separately so the verdict never hides what kind of support exists.
    """
    terms = _bilingual_keywords(f"{title} {question}")
    rows = conn.execute(
        """SELECT id, title, content, tags, authors, design, doi, pdf_path,
                  entry_date, study_type
             FROM entries WHERE archived = 0"""
    ).fetchall()
    studies = [dict(r) for r in rows]
    matched = []
    for s in studies:
        # Match on words only. `_score_study` adds a quality bonus, which gave
        # every study a positive score and made unrelated topics look covered.
        # The text is folded the same way the keywords are (diacritics removed),
        # otherwise "conștiința" in a title never matches the term "constiinta".
        title_text = _fold(s.get("title") or "").lower()
        body = _fold(" ".join(
            str(s.get(k) or "") for k in ("tags", "content", "design", "authors")
        )).lower()
        hits = [t for t in terms if t in title_text or t in body]
        if not hits:
            continue
        # One generic word is not a match: "structur" appears in hundreds of
        # titles, so a study about the structure of intelligence would count as
        # covering a topic about brain structure. A match needs either two
        # words, or one word specific enough to carry meaning alone.
        specific = [t for t in hits if t not in _GENERIC_TERMS]
        if len(hits) < 2 and not specific:
            continue
        score = sum(3.0 if t in title_text else 1.0 for t in hits)
        matched.append((score, hits, s))
    matched.sort(key=lambda x: -x[0])

    with_doi = sum(1 for _, _, s in matched if (s.get("doi") or "").strip())
    with_pdf = sum(1 for _, _, s in matched if s.get("pdf_path"))
    bodies = sum(1 for _, _, s in matched if len(s.get("content") or "") >= 200)
    designs = {(s.get("design") or "").strip().lower() for _, _, s in matched}
    designs.discard("")
    years = sorted({(s.get("entry_date") or "")[:4] for _, _, s in matched if s.get("entry_date")})

    # A topic is workable when there is something to read and something left to
    # add. The bar counts entries with a citable body, since ten bare titles do
    # not make a theoretical framework.
    if len(matched) >= 20 and bodies >= 8:
        verdict, note = "solidă", "biblioteca poartă tema; rămâne de găsit unghiul original"
    elif len(matched) >= 8 and bodies >= 3:
        verdict, note = "posibilă", "destule intrări ca să începi, dar cadrul teoretic va fi subțire"
    elif matched:
        verdict, note = "fragilă", "prea puține studii — ai scrie din memorie, nu din bibliotecă"
    else:
        verdict, note = "nepotrivită", "biblioteca nu are nimic pe tema asta"

    return {
        "terms": terms[:8],
        "matched": len(matched),
        "with_doi": with_doi,
        "with_pdf": with_pdf,
        "with_body": bodies,
        "designs": sorted(designs),
        "years": [y for y in years if y][:8],
        "verdict": verdict,
        "note": note,
        "top_titles": [s.get("title") or "" for _, _, s in matched[:3]],
        "matched_terms": matched[0][1][:5] if matched else [],
    }


@router.get("/project/topics")
def project_topics():
    """Candidate topics, each measured against the real library."""
    conn = get_db()
    try:
        project = _ensure_project(conn)
        ideas = conn.execute("SELECT text, category FROM project_ideas ORDER BY id DESC LIMIT 20").fetchall()
        current = {
            "title": (project.get("title") or "").strip(),
            "research_q": (project.get("research_q") or "").strip(),
        }
        seeds = list(_TOPIC_SEEDS)
    finally:
        conn.close()

    # Each seed is measured against the real rows, through one connection.
    conn = get_db()
    try:
        candidates = [
            {"title": title, "question": question, "source": "seed",
             "coverage": _coverage(conn, title, question)}
            for title, question in seeds
        ]
        # The user's own current topic, measured the same way, for comparison.
        if current["title"]:
            candidates.insert(0, {
                "title": current["title"],
                "question": current["research_q"],
                "source": "current",
                "coverage": _coverage(conn, current["title"], current["research_q"]),
            })
    finally:
        conn.close()

    order = {"solidă": 0, "posibilă": 1, "fragilă": 2, "nepotrivită": 3}
    candidates.sort(key=lambda c: (order.get(c["coverage"]["verdict"], 9), -c["coverage"]["matched"]))

    return {
        "candidates": candidates,
        "current": current,
        "ideas": [dict(i) for i in ideas],
    }


class TopicChoice(BaseModel):
    title: str = Field(..., min_length=3, max_length=500)
    question: str = ""


@router.post("/project/topics/choose")
def project_topic_choose(body: TopicChoice):
    """Adopt a topic: it becomes the project title and research question."""
    conn = get_db()
    try:
        project = _ensure_project(conn)
        pid = project["id"]
        fields = ["title = ?", "research_q = ?", "updated_at = ?"]
        params: list = [body.title.strip(), body.question.strip(), _now(), ]
        # A new topic invalidates whatever was generated for the old one.
        fields += ["analysis_json = ''", "analysis_at = ''", "analysis_key = ''"]
        conn.execute(f"UPDATE project SET {', '.join(fields)} WHERE id = ?", [*params, pid])
        conn.commit()
        row = conn.execute("SELECT * FROM project WHERE id = ?", (pid,)).fetchone()
        return _row_to_project(row)
    finally:
        conn.close()


class TopicBrainstormRequest(BaseModel):
    focus: str = Field("", max_length=300, description="Unghi de focalizare sau direcție specifică")
    count: int = Field(5, ge=2, le=8)


@router.post("/project/topics/brainstorm")
async def project_topics_brainstorm(body: TopicBrainstormRequest):
    """Brainstorms grounded academic thesis topics using the LLM and the study library,
    computing live library coverage for each generated candidate."""
    conn = get_db()
    try:
        project = _ensure_project(conn)
        ranked = _rank_studies(conn, project, limit=12)
    finally:
        conn.close()

    corpus = "\n".join(
        f"- {s['title']} ({s['year'] or 'n/d'}) [{s['authors'] or ''}]"
        for s in ranked
    ) or "(fără studii)"

    focus_clause = f"Concentrează-te pe direcția: {body.focus.strip()}." if body.focus.strip() else ""

    prompt = f"""Ești un coordonator academic și conducător științific de doctorate și dizertații.
Ajută studentul să facă un brainstorming avansat pentru teme de cercetare originale și publicabile.

Contextul lucrării:
Titlu actual: {project.get('title') or 'Nespecificat'}
Domeniu: {project.get('field') or 'Neuroștiințe / Psihologie clinică'}
Tip lucrare: {project.get('work_type') or 'Disertație'}
{focus_clause}

Studii cheie din biblioteca studentului:
{corpus}

Generează exact {body.count} propuneri de teme de cercetare academice de nivel avansat.
Fiecare temă trebuie să fie ancorată empiric în literatura de mai sus, să aibă variabile clar definite și formulare științifică impecabilă în limba română.

Răspunde DOAR cu JSON valid:
{{
  "topics": [
    {{
      "title": "Titlu academic precis, specificând variabilele și populația",
      "question": "Întrebare de cercetare clară și verificabilă empiric",
      "rationale": "Justificare de 2-3 fraze a relevanței teoretice și empirice, ancorată în studiile din bibliotecă",
      "variables": "VI: [variabile independente] | VD: [variabile dependente] | Mediatori/Moderatori: [...]",
      "pico": "P: [Populație] | I: [Intervenție/Expunere] | C: [Comparație/Control] | O: [Rezultat]"
    }}
  ]
}}
Nu adăuga niciun text în afara JSON-ului."""

    reply = await _llm([{"role": "system", "content": prompt}], temperature=0.6, max_tokens=3500)
    topics_out: list[dict] = []
    if reply:
        match = re.search(r"\{.*\}", reply, re.S)
        if match:
            try:
                parsed = json.loads(match.group(0))
                raw_topics = parsed.get("topics", [])
                conn = get_db()
                try:
                    for t in raw_topics:
                        t_title = str(t.get("title") or "").strip()
                        t_q = str(t.get("question") or "").strip()
                        if not t_title:
                            continue
                        coverage = _coverage(conn, t_title, t_q)
                        topics_out.append({
                            "title": t_title,
                            "question": t_q,
                            "rationale": str(t.get("rationale") or "").strip(),
                            "variables": str(t.get("variables") or "").strip(),
                            "pico": str(t.get("pico") or "").strip(),
                            "source": "ai_brainstorm",
                            "coverage": coverage,
                        })
                finally:
                    conn.close()
            except Exception as e:
                logger.warning("Failed to parse brainstorm topics: %s", e)

    order = {"solidă": 0, "posibilă": 1, "fragilă": 2, "nepotrivită": 3}
    topics_out.sort(key=lambda c: (order.get(c["coverage"]["verdict"], 9), -c["coverage"]["matched"]))

    return {
        "topics": topics_out,
        "from_llm": bool(topics_out),
        "count": len(topics_out),
    }


@router.get("/projects")
def list_projects() -> dict:
    """List all isolated research projects/theses."""
    conn = get_db()
    try:
        _ensure_project(conn)
        active_id = _get_active_project_id(conn)
        rows = conn.execute("SELECT id, title, work_type, field, updated_at FROM project ORDER BY id ASC").fetchall()
        projects = [dict(r) for r in rows]
        return {"active_id": active_id, "projects": projects}
    finally:
        conn.close()


@router.post("/projects", status_code=201)
def create_project(body: dict) -> dict:
    """Create a new isolated research project."""
    title = (body.get("title") or "Proiect Nou").strip()
    work_type = (body.get("work_type") or "licenta").strip()
    field = (body.get("field") or "").strip()
    description = (body.get("description") or "").strip()
    research_q = (body.get("research_q") or "").strip()
    methodology = (body.get("methodology") or "").strip()

    conn = get_db()
    try:
        cur = conn.execute(
            """INSERT INTO project (title, work_type, field, description, research_q, methodology, structure_json, plan_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, '{}')""",
            (title, work_type, field, description, research_q, methodology, json.dumps(_PLACEHOLDER_STRUCTURE, ensure_ascii=False)),
        )
        conn.commit()
        pid = cur.lastrowid
        _set_active_project_id(conn, pid)
        row = conn.execute("SELECT * FROM project WHERE id = ?", (pid,)).fetchone()
        return _row_to_project(row)
    finally:
        conn.close()


@router.post("/projects/{pid}/activate")
def activate_project(pid: int) -> dict:
    """Switch the active research project."""
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM project WHERE id = ?", (pid,)).fetchone()
        if not row:
            raise HTTPException(404, "Proiectul nu a fost găsit")
        _set_active_project_id(conn, pid)
        return {"status": "ok", "active_id": pid, "project": _row_to_project(row)}
    finally:
        conn.close()


@router.delete("/projects/{pid}")
def delete_project(pid: int) -> dict:
    """Delete an isolated research project (if more than one exists)."""
    conn = get_db()
    try:
        count = conn.execute("SELECT COUNT(*) FROM project").fetchone()[0]
        if count <= 1:
            raise HTTPException(400, "Nu poți șterge singurul proiect existent.")
        row = conn.execute("SELECT id FROM project WHERE id = ?", (pid,)).fetchone()
        if not row:
            raise HTTPException(404, "Proiectul nu a fost găsit")
        conn.execute("DELETE FROM project WHERE id = ?", (pid,))
        conn.commit()

        remaining = conn.execute("SELECT id FROM project ORDER BY id ASC LIMIT 1").fetchone()
        if remaining:
            _set_active_project_id(conn, remaining["id"])
        return {"status": "ok", "deleted_id": pid}
    finally:
        conn.close()
