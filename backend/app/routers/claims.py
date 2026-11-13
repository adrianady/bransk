"""
Claim hierarchy + stance edges (slice 5).

Claims are leaves of the theories tree (``theories.node_type='claim'``).
Stance edges (``claim_stance``) pin one study's verdict on one claim to a
verbatim quote from the canonical page text — the anti-hallucination rule:
``evidence_quote`` must normalize to ``page_texts.text[start:end]`` for
``(entry_id, evidence_page)``, verified with ``locate_quote``/``get_page_text``
(the single normaliser lives in ``app.page_text``).

Only ``status='accepted'`` edges feed the aggregate; ``proposed`` edges are
visible only in the study analysis panel.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.db import get_db
from app.models import ClaimCreate, ClaimStanceIn, ClaimStancePatch
from app.page_text import _normalize as _canon_normalize
from app.page_text import get_page_text, locate_quote

router = APIRouter()

DIRECTIONS = ("supports", "contradicts", "mixed", "neutral")
STATUSES = ("proposed", "accepted", "rejected", "insufficient")
PROPOSERS = ("ai", "user")

_STANCE_COLS = (
    "s.id, s.claim_id, s.entry_id, s.direction, s.aspect, s.highlight_id, "
    "s.evidence_page, s.evidence_start, s.evidence_end, s.evidence_quote, "
    "s.reasoning, s.confidence, s.status, s.proposed_by, s.provider, s.model, "
    "s.created_at, s.decided_at"
)


# ── helpers ──

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _claim_or_422(conn, claim_id: int) -> dict:
    row = conn.execute("SELECT * FROM theories WHERE id = ?", [claim_id]).fetchone()
    if not row:
        raise HTTPException(422, "Claim-ul nu a fost găsit.")
    return dict(row)


def _claim_or_404(conn, claim_id: int) -> dict:
    row = conn.execute("SELECT * FROM theories WHERE id = ?", [claim_id]).fetchone()
    if not row:
        raise HTTPException(404, "Claim-ul nu a fost găsit.")
    return dict(row)


def _entry_or_422(conn, entry_id: int) -> dict:
    row = conn.execute("SELECT * FROM entries WHERE id = ?", [entry_id]).fetchone()
    if not row:
        raise HTTPException(422, "Studiul nu a fost găsit.")
    return dict(row)


def _check_confidence(confidence) -> None:
    if confidence is None:
        return
    try:
        value = float(confidence)
    except (TypeError, ValueError):
        raise HTTPException(422, "Încrederea (confidence) trebuie să fie între 0 și 1.")
    if not 0.0 <= value <= 1.0:
        raise HTTPException(422, "Încrederea (confidence) trebuie să fie între 0 și 1.")


def _check_highlight(conn, entry_id: int, highlight_id) -> None:
    if highlight_id is None:
        return
    row = conn.execute(
        "SELECT id FROM pdf_highlights WHERE id = ? AND entry_id = ?",
        [highlight_id, entry_id],
    ).fetchone()
    if not row:
        raise HTTPException(422, "Highlight-ul nu aparține acestui studiu.")


def _validate_evidence(
    conn,
    entry_id: int,
    direction: str,
    evidence_page,
    evidence_start,
    evidence_end,
    evidence_quote,
) -> tuple[int | None, int | None, str]:
    """Validate direction + quote against the canonical page text.

    Returns ``(start, end, quote)`` with located offsets filled in.
    Raises Romanian 422 on any rule violation.
    """
    if direction not in DIRECTIONS:
        raise HTTPException(
            422, "Direcție invalidă: folosește supports, contradicts, mixed sau neutral."
        )
    quote = (evidence_quote or "").strip()
    if not quote:
        if direction == "neutral":
            return None, None, ""
        raise HTTPException(
            422,
            "Citatul-dovadă (evidence_quote) este obligatoriu pentru direcția aleasă; "
            "doar 'neutral' poate fi salvat fără citat.",
        )
    if evidence_page is None:
        raise HTTPException(
            422, "Pagina lipsește: evidence_page este obligatoriu când oferi un citat."
        )
    page = get_page_text(conn, entry_id, evidence_page)
    if page is None or not (page.get("text") or ""):
        raise HTTPException(
            422, "Pagina nu are text canonic indexat pentru acest studiu."
        )
    text = page["text"]
    if evidence_start is not None or evidence_end is not None:
        if evidence_start is None or evidence_end is None:
            raise HTTPException(
                422, "Trebuie specificate ambele offset-uri: evidence_start și evidence_end."
            )
        if evidence_start < 0 or evidence_end <= evidence_start or evidence_end > len(text):
            raise HTTPException(
                422, "Intervalul de offset-uri este invalid pentru pagina indicată."
            )
        if _canon_normalize(text[evidence_start:evidence_end]) != _canon_normalize(quote):
            raise HTTPException(
                422, "Citatul nu corespunde textului canonic al paginii (după normalizare)."
            )
        return evidence_start, evidence_end, quote
    found = locate_quote(conn, entry_id, evidence_page, quote)
    if found is None:
        raise HTTPException(
            422, "Citatul nu corespunde textului canonic al paginii (după normalizare)."
        )
    return found[0], found[1], quote


def _accepted_conflict(conn, claim_id: int, entry_id: int, aspect: str, exclude_id=None) -> bool:
    params: list = [claim_id, entry_id, aspect or ""]
    query = (
        "SELECT id FROM claim_stance WHERE claim_id = ? AND entry_id = ? "
        "AND COALESCE(aspect, '') = ? AND status = 'accepted'"
    )
    if exclude_id is not None:
        query += " AND id != ?"
        params.append(exclude_id)
    return conn.execute(query, params).fetchone() is not None


def _stance_row(conn, stance_id: int) -> dict:
    row = conn.execute(
        f"""SELECT s.*, e.title AS entry_title FROM claim_stance s
            JOIN entries e ON e.id = s.entry_id WHERE s.id = ?""",
        [stance_id],
    ).fetchone()
    if not row:
        raise HTTPException(404, "Muchia nu a fost găsită.")
    return dict(row)


def _aggregate_directions(directions: list[str]) -> str:
    """Pure rule table from the design doc (neutral never dilutes a verdict)."""
    if not directions:
        return "insufficient"
    pool = [d for d in directions if d != "neutral"]
    if not pool:
        return "neutral"
    if len(set(pool)) == 1:
        return pool[0]
    return "mixed"


# ── claims ──

@router.post("/claims", status_code=201)
def create_claim(data: ClaimCreate):
    conn = get_db()
    try:
        parent = conn.execute(
            "SELECT * FROM theories WHERE id = ?", [data.parent_id]
        ).fetchone()
        if not parent:
            raise HTTPException(404, "Teoria părinte nu a fost găsită.")
        parent = dict(parent)
        if (parent.get("node_type") or "theory") == "claim":
            raise HTTPException(
                422, "Un claim nu poate avea copii: alege o teorie sau sub-teorie ca părinte."
            )
        statement = (data.statement or "").strip()
        if not statement:
            raise HTTPException(422, "Un claim are nevoie de un statement testabil.")
        cur = conn.execute(
            """INSERT INTO theories
               (name, description, parent_id, status, node_type, statement, test_variable, test_condition)
               VALUES (?, ?, ?, 'emerging', 'claim', ?, ?, ?)""",
            [
                data.name.strip(),
                statement,
                data.parent_id,
                statement,
                (data.test_variable or "").strip(),
                (data.test_condition or "").strip(),
            ],
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM theories WHERE id = ?", [cur.lastrowid]
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


# ── stance edges ──

@router.get("/claims/{claim_id}/stances")
def list_stances(claim_id: int, status: str | None = None):
    conn = get_db()
    try:
        _claim_or_404(conn, claim_id)
        if status is not None and status not in STATUSES:
            raise HTTPException(
                422, "Status invalid: proposed, accepted, rejected sau insufficient."
            )
        query = (
            f"""SELECT {_STANCE_COLS}, e.title AS entry_title FROM claim_stance s
                JOIN entries e ON e.id = s.entry_id WHERE s.claim_id = ?"""
        )
        params: list = [claim_id]
        if status is not None:
            query += " AND s.status = ?"
            params.append(status)
        query += " ORDER BY s.created_at, s.id"
        rows = [dict(r) for r in conn.execute(query, params).fetchall()]
        grouped = {key: [] for key in STATUSES}
        for row in rows:
            grouped.setdefault(row["status"], []).append(row)
        if status is not None:
            return {status: grouped.get(status, [])}
        return grouped
    finally:
        conn.close()


@router.post("/claims/{claim_id}/stances", status_code=201)
def propose_stance(claim_id: int, data: ClaimStanceIn):
    conn = get_db()
    try:
        _claim_or_422(conn, claim_id)
        _entry_or_422(conn, data.entry_id)
        if (data.proposed_by or "ai") not in PROPOSERS:
            raise HTTPException(422, "proposed_by invalid: ai sau user.")
        _check_confidence(data.confidence)
        _check_highlight(conn, data.entry_id, data.highlight_id)
        start, end, quote = _validate_evidence(
            conn,
            data.entry_id,
            (data.direction or "").strip().lower(),
            data.evidence_page,
            data.evidence_start,
            data.evidence_end,
            data.evidence_quote,
        )
        cur = conn.execute(
            """INSERT INTO claim_stance
               (claim_id, entry_id, direction, aspect, highlight_id,
                evidence_page, evidence_start, evidence_end, evidence_quote,
                reasoning, confidence, status, proposed_by, provider, model)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?, ?)""",
            [
                claim_id,
                data.entry_id,
                (data.direction or "").strip().lower(),
                (data.aspect or "").strip(),
                data.highlight_id,
                data.evidence_page,
                start,
                end,
                quote,
                (data.reasoning or "").strip(),
                data.confidence,
                (data.proposed_by or "ai"),
                (data.provider or "").strip(),
                (data.model or "").strip(),
            ],
        )
        conn.commit()
        return _stance_row(conn, cur.lastrowid)
    finally:
        conn.close()


@router.patch("/claims/stances/{stance_id}")
def decide_stance(stance_id: int, data: ClaimStancePatch):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM claim_stance WHERE id = ?", [stance_id]
        ).fetchone()
        if not row:
            raise HTTPException(404, "Muchia nu a fost găsită.")
        current = dict(row)

        if data.action is not None and data.action not in ("accept", "reject"):
            raise HTTPException(422, "Acțiune invalidă: accept sau reject.")

        direction = current["direction"]
        if data.direction is not None:
            direction = (data.direction or "").strip().lower()

        # Merge quote fields: explicit values override, omitted ones stay.
        page = current["evidence_page"] if data.evidence_page is None else data.evidence_page
        start = (
            current["evidence_start"] if data.evidence_start is None else data.evidence_start
        )
        end = current["evidence_end"] if data.evidence_end is None else data.evidence_end
        quote = (
            current["evidence_quote"] if data.evidence_quote is None else data.evidence_quote
        )
        direction_changed = direction != current["direction"]
        quote_touched = any(
            v is not None
            for v in (data.direction, data.evidence_quote, data.evidence_page,
                      data.evidence_start, data.evidence_end)
        )
        if quote_touched:
            start, end, quote = _validate_evidence(
                conn, current["entry_id"], direction, page, start, end, quote
            )

        aspect = current["aspect"] or ""
        if data.aspect is not None:
            aspect = (data.aspect or "").strip()
        highlight_id = current["highlight_id"]
        if data.highlight_id is not None:
            highlight_id = data.highlight_id
        _check_highlight(conn, current["entry_id"], highlight_id)

        confidence = current["confidence"]
        if data.confidence is not None:
            _check_confidence(data.confidence)
            confidence = data.confidence

        reasoning = current["reasoning"] or ""
        if data.reasoning is not None:
            reasoning = data.reasoning or ""
        if direction_changed and not reasoning.startswith("[model: "):
            reasoning = f"[model: {current['direction']}] " + reasoning

        status = current["status"]
        if data.action == "accept":
            if _accepted_conflict(
                conn, current["claim_id"], current["entry_id"], aspect, exclude_id=stance_id
            ):
                raise HTTPException(
                    409, "Există deja o muchie acceptată pentru (claim, studiu, aspect)."
                )
            status = "accepted"
        elif data.action == "reject":
            status = "rejected"

        conn.execute(
            """UPDATE claim_stance
               SET direction = ?, aspect = ?, highlight_id = ?,
                   evidence_page = ?, evidence_start = ?, evidence_end = ?,
                   evidence_quote = ?, reasoning = ?, confidence = ?,
                   status = ?, decided_at = ?
               WHERE id = ?""",
            [direction, aspect, highlight_id, page, start, end, quote,
             reasoning, confidence, status, _now(), stance_id],
        )
        conn.commit()
        return _stance_row(conn, stance_id)
    finally:
        conn.close()


# ── aggregate (computed, never stored) ──

@router.get("/claims/{claim_id}/aggregate")
def claim_aggregate(claim_id: int):
    conn = get_db()
    try:
        claim = _claim_or_404(conn, claim_id)
        rows = conn.execute(
            f"""SELECT {_STANCE_COLS}, e.title AS entry_title FROM claim_stance s
                JOIN entries e ON e.id = s.entry_id
                WHERE s.claim_id = ? AND s.status = 'accepted'
                ORDER BY s.entry_id, s.id""",
            [claim_id],
        ).fetchall()
        by_entry: dict[int, dict] = {}
        for r in rows:
            d = dict(r)
            bucket = by_entry.setdefault(
                d["entry_id"],
                {"entry_id": d["entry_id"], "entry_title": d["entry_title"],
                 "directions": [], "edges": []},
            )
            bucket["directions"].append(d["direction"])
            bucket["edges"].append(d)
        per_entry = []
        for bucket in by_entry.values():
            per_entry.append(
                {
                    "entry_id": bucket["entry_id"],
                    "entry_title": bucket["entry_title"],
                    "verdict": _aggregate_directions(bucket["directions"]),
                    "accepted_count": len(bucket["edges"]),
                    "directions": bucket["directions"],
                }
            )
        studies_accepted = len(per_entry)
        if studies_accepted < 2:
            claim_verdict = "insufficient"
        else:
            claim_verdict = _aggregate_directions([p["verdict"] for p in per_entry])
        return {
            "claim_id": claim_id,
            "claim_name": claim.get("name", ""),
            "per_entry": per_entry,
            "claim_verdict": claim_verdict,
            "studies_accepted": studies_accepted,
        }
    finally:
        conn.close()


# ── study analysis panel ──

@router.get("/entries/{entry_id}/analysis")
def entry_analysis(entry_id: int):
    conn = get_db()
    try:
        entry = conn.execute(
            "SELECT * FROM entries WHERE id = ?", [entry_id]
        ).fetchone()
        if not entry:
            raise HTTPException(404, "Studiul nu a fost găsit.")
        entry = dict(entry)
        rows = conn.execute(
            """SELECT s.*, t.name AS claim_name, t.statement AS statement,
                      t.test_variable AS test_variable, t.test_condition AS test_condition
               FROM claim_stance s
               JOIN theories t ON t.id = s.claim_id
               WHERE s.entry_id = ? AND s.status IN ('proposed', 'accepted')
               ORDER BY s.created_at, s.id""",
            [entry_id],
        ).fetchall()
        return {
            "entry_id": entry_id,
            "entry_title": entry.get("title", ""),
            "claims": [dict(r) for r in rows],
            "bias": {"available": False, "reason": "not_implemented"},
        }
    finally:
        conn.close()
