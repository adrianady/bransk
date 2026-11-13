"""
Questionnaires and Research Instruments Router for ChronoKG.
Manages research questionnaires, Likert scales, survey items, subscales,
target populations, and provides AI generation & academic critique.
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.db import get_db
from app.llm import LLM_PROVIDER, complete as llm_complete

logger = logging.getLogger("chronokg.questionnaires")

router = APIRouter(prefix="/questionnaires", tags=["questionnaires"])


class QuestionnaireItem(BaseModel):
    id: str
    text: str
    type: str = "likert"  # likert, multiple_choice, open, numeric
    options: List[str] = Field(default_factory=lambda: ["Total dezacord", "Dezacord", "Neutru", "Acord", "Total acord"])
    subscale: str = ""


class QuestionnaireCreate(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    project_id: Optional[int] = None
    title: str = ""
    description: str = ""
    construct: str = Field(default="", alias="construct")
    target_population: str = ""
    reliability_notes: str = ""
    items: List[QuestionnaireItem] = Field(default_factory=list)

class QuestionnaireUpdate(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    title: Optional[str] = None
    description: Optional[str] = None
    construct: Optional[str] = Field(default=None, alias="construct")
    target_population: Optional[str] = None
    reliability_notes: Optional[str] = None
    items: Optional[List[QuestionnaireItem]] = None

def _row_to_dict(row) -> Dict[str, Any]:
    items = []
    try:
        items = json.loads(row["items_json"] or "[]")
    except Exception:
        items = []
    return {
        "id": row["id"],
        "project_id": row["project_id"] if "project_id" in row.keys() else 1,
        "title": row["title"],
        "description": row["description"],
        "construct": row["construct"],
        "target_population": row["target_population"],
        "reliability_notes": row["reliability_notes"],
        "items": items,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@router.get("")
def list_questionnaires(project_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """List all questionnaires for the research project."""
    conn = get_db()
    try:
        from app.routers.project import _get_active_project_id
        pid = project_id if project_id is not None else _get_active_project_id(conn)
        rows = conn.execute(
            "SELECT id, project_id, title, description, construct, target_population, reliability_notes, items_json, created_at, updated_at FROM questionnaires WHERE project_id = ? ORDER BY id DESC",
            (pid,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


@router.post("", status_code=201)
def create_questionnaire(body: QuestionnaireCreate, project_id: Optional[int] = None) -> Dict[str, Any]:
    """Create a new questionnaire or research instrument."""
    conn = get_db()
    try:
        from app.routers.project import _get_active_project_id
        pid = project_id if project_id is not None else (body.project_id if body.project_id is not None else _get_active_project_id(conn))
        items_data = [item.model_dump() for item in body.items]
        cur = conn.execute(
            """INSERT INTO questionnaires (project_id, title, description, construct, target_population, reliability_notes, items_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                pid,
                body.title.strip(),
                body.description.strip(),
                body.construct.strip(),
                body.target_population.strip(),
                body.reliability_notes.strip(),
                json.dumps(items_data, ensure_ascii=False),
            ),
        )
        conn.commit()
        qid = cur.lastrowid
        row = conn.execute("SELECT * FROM questionnaires WHERE id = ?", (qid,)).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


@router.get("/{qid}")
def get_questionnaire(qid: int) -> Dict[str, Any]:
    """Get questionnaire details by ID."""
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM questionnaires WHERE id = ?", (qid,)).fetchone()
        if not row:
            raise HTTPException(404, "Chestionarul nu a fost găsit")
        return _row_to_dict(row)
    finally:
        conn.close()


@router.put("/{qid}")
def update_questionnaire(qid: int, body: QuestionnaireUpdate) -> Dict[str, Any]:
    """Update a questionnaire."""
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM questionnaires WHERE id = ?", (qid,)).fetchone()
        if not row:
            raise HTTPException(404, "Chestionarul nu a fost găsit")

        current = _row_to_dict(row)
        title = body.title if body.title is not None else current["title"]
        description = body.description if body.description is not None else current["description"]
        construct = body.construct if body.construct is not None else current["construct"]
        target_population = body.target_population if body.target_population is not None else current["target_population"]
        reliability_notes = body.reliability_notes if body.reliability_notes is not None else current["reliability_notes"]
        
        items = body.items if body.items is not None else [QuestionnaireItem(**i) for i in current["items"]]
        items_data = [item.model_dump() for item in items]

        conn.execute(
            """UPDATE questionnaires
               SET title = ?, description = ?, construct = ?, target_population = ?, reliability_notes = ?, items_json = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (
                title.strip(),
                description.strip(),
                construct.strip(),
                target_population.strip(),
                reliability_notes.strip(),
                json.dumps(items_data, ensure_ascii=False),
                qid,
            ),
        )
        conn.commit()
        updated_row = conn.execute("SELECT * FROM questionnaires WHERE id = ?", (qid,)).fetchone()
        return _row_to_dict(updated_row)
    finally:
        conn.close()


@router.delete("/{qid}")
def delete_questionnaire(qid: int) -> Dict[str, Any]:
    """Delete a questionnaire."""
    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM questionnaires WHERE id = ?", (qid,)).fetchone()
        if not row:
            raise HTTPException(404, "Chestionarul nu a fost găsit")
        conn.execute("DELETE FROM questionnaires WHERE id = ?", (qid,))
        conn.commit()
        return {"status": "ok", "deleted_id": qid}
    finally:
        conn.close()


@router.post("/{qid}/ai-generate")
async def ai_generate_questionnaire_items(qid: int) -> Dict[str, Any]:
    """Ask AI to generate or expand questionnaire items based on project context and construct."""
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM questionnaires WHERE id = ?", (qid,)).fetchone()
        if not row:
            raise HTTPException(404, "Chestionarul nu a fost găsit")
        q = _row_to_dict(row)

        proj = conn.execute("SELECT title, research_q, description, plan_json FROM project LIMIT 1").fetchone()
        project_context = ""
        if proj:
            project_context = f"Titlu teză: {proj['title']}\nÎntrebare de cercetare: {proj['research_q']}\nDescriere: {proj['description']}"

    finally:
        conn.close()

    system_prompt = (
        "Ești un expert metodolog în cercetare academică și psihometrie. "
        "Sarcina ta este să generezi 5-8 itemi de chestionar riguroși (pe scală Likert 1-5 sau alt tip adecvat) "
        "pentru constructul și titlul specificat, corelați cu întrebarea de cercetare a tezei.\n"
        "Returnează DOAR un obiect JSON valid, fără markdown suplimentar, cu structura:\n"
        "{\n"
        '  "description": "Scurtă descriere metodologică a instrumentului și cum măsoară constructul",\n'
        '  "reliability_notes": "Note despre validitate, consistență internă așteptată (Alpha Cronbach) și mod de scorare",\n'
        '  "items": [\n'
        '    {"id": "q1", "text": "Textul itemului...", "type": "likert", "options": ["Total dezacord", "Dezacord", "Neutru", "Acord", "Total acord"], "subscale": "Dimensiunea 1"}\n'
        "  ]\n"
        "}"
    )

    user_prompt = (
        f"Context proiect:\n{project_context}\n\n"
        f"Chestionar vizat:\n"
        f"Titlu: {q['title']}\n"
        f"Construct vizat: {q['construct']}\n"
        f"Populație țintă: {q['target_population']}\n"
        f"Generează sau completează itemii de chestionar pentru acest instrument în limba română."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    if LLM_PROVIDER == "mock":
        generated = {
            "description": f"Instrument generat offline pentru {q['construct']}.",
            "reliability_notes": "Alpha Cronbach estimat > 0.80 pe eșantion țintă.",
            "items": [
                {"id": "g1", "text": "În ce măsură resimțiți impactul acestui factor în activitatea cotidiană?", "type": "likert", "options": ["Deloc", "În mică măsură", "Moderat", "În mare măsură", "În foarte mare măsură"], "subscale": "Evaluare generală"},
                {"id": "g2", "text": "Considerați că resursele disponibile sunt suficiente pentru a face față situației?", "type": "likert", "options": ["Total dezacord", "Dezacord", "Neutru", "Acord", "Total acord"], "subscale": "Resurse și adaptare"}
            ]
        }
    else:
        try:
            _, _, text = await llm_complete(messages, temperature=0.3, max_tokens=2000)
            clean = text.strip()
            if clean.startswith("```json"):
                clean = clean[7:]
            if clean.endswith("```"):
                clean = clean[:-3]
            clean = clean.strip()
            generated = json.loads(clean)
        except Exception as exc:
            logger.error("AI generate questionnaire failed: %s", exc)
            raise HTTPException(503, f"Eroare LLM generare chestionar: {exc}")

    # Update DB with generated content
    new_desc = generated.get("description", q["description"])
    new_rel = generated.get("reliability_notes", q["reliability_notes"])
    new_items = generated.get("items", q["items"])

    conn = get_db()
    try:
        conn.execute(
            """UPDATE questionnaires
               SET description = ?, reliability_notes = ?, items_json = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (new_desc, new_rel, json.dumps(new_items, ensure_ascii=False), qid),
        )
        conn.commit()
        updated_row = conn.execute("SELECT * FROM questionnaires WHERE id = ?", (qid,)).fetchone()
        return _row_to_dict(updated_row)
    finally:
        conn.close()


@router.post("/{qid}/ai-critique")
async def ai_critique_questionnaire(qid: int) -> Dict[str, Any]:
    """Ask AI to critique the questionnaire items for psychometric validity, bias, and academic rigor."""
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM questionnaires WHERE id = ?", (qid,)).fetchone()
        if not row:
            raise HTTPException(404, "Chestionarul nu a fost găsit")
        q = _row_to_dict(row)
    finally:
        conn.close()

    items_str = json.dumps(q["items"], ensure_ascii=False, indent=2)

    system_prompt = (
        "Ești un profesor universitar evaluator de metodologie și psihometrie. "
        "Analizează critic chestionarul de mai jos pentru validitate de conținut, claritate, riscuri de ambiguitate, "
        "întrebări tendențioase (leading questions) și coerență cu constructul măsurat.\n"
        "Returnează o analiză critică structurată în limba română cu:\n"
        "1. Puncte forte ale instrumentului\n"
        "2. Vulnerabilități sau riscuri metodologice\n"
        "3. Sugestii concrete de îmbunătățire sau adăugare a unor itemi\n"
        "Format Markdown clar și academic."
    )

    user_prompt = (
        f"Titlu chestionar: {q['title']}\n"
        f"Construct: {q['construct']}\n"
        f"Populație țintă: {q['target_population']}\n"
        f"Descriere: {q['description']}\n\n"
        f"Itemi actuali:\n{items_str}"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    if LLM_PROVIDER == "mock":
        critique = "### Analiză critică (Mod offline)\n- Chestionarul acoperă satisfăcător constructul de bază.\n- Se recomandă clarificarea opțiunilor de răspuns și testarea pe un eșantion pilot."
    else:
        try:
            _, _, text = await llm_complete(messages, temperature=0.3, max_tokens=1500)
            critique = text.strip()
        except Exception as exc:
            logger.error("AI critique questionnaire failed: %s", exc)
            raise HTTPException(503, f"Eroare LLM critică chestionar: {exc}")

    return {
        "questionnaire_id": qid,
        "title": q["title"],
        "critique": critique,
    }
