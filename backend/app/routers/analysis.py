"""Stance analysis panel — layer 1 signals + layer 3 aggregation (dry run).

**No LLM calls and no writes.** The endpoint scores the study's span-anchored
evidence with the local NLI cross-encoder (or the deterministic pre-filter when
the model is absent), reports which layer-1 capabilities exist, and shows the
aggregate computed from the *already accepted* stance edges. Proposed edges are never created here: the human-first rule from the
design doc means the reviewer writes the verdict, then compares.

Layer 1 uses the local ONNX NLI cross-encoder when its files are present, and
falls back to the deterministic pre-filter otherwise. The capability reply
reports which one actually ran, so a missing model is visible rather than
silently producing nulls.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_db
from app.stance.aggregate import claim_rollup, explain, study_verdict
from app.stance.passages import entry_passages
from app.stance.signals import (
    DeterministicScorer,
    OnnxNliScorer,
    is_strong_signal,
    nli_availability,
    score_passage,
)

router = APIRouter()


def _active_scorer():
    """Real model when installed, deterministic pre-filter otherwise.

    Construction is cheap and the ONNX session is cached at module level, so a
    per-request instance never reloads the 233 MB file.
    """
    if nli_availability()["available"]:
        return OnnxNliScorer()
    return DeterministicScorer()


class AnalyzeIn(BaseModel):
    entry_id: int


def _claim_node(conn, claim_id: int) -> dict:
    row = conn.execute("SELECT * FROM theories WHERE id = ?", [claim_id]).fetchone()
    if not row:
        raise HTTPException(404, "Claim-ul nu a fost găsit.")
    return dict(row)


def _claim_text(claim: dict) -> str:
    return (claim.get("statement") or claim.get("name") or "").strip()


def _accepted_rollup(conn, claim_id: int) -> dict:
    """Claim aggregate from accepted edges only (never the dry-run signals)."""
    rows = conn.execute(
        """SELECT entry_id, direction FROM claim_stance
            WHERE claim_id = ? AND status = 'accepted'
            ORDER BY entry_id, id""",
        [claim_id],
    ).fetchall()
    by_entry: dict[int, list[str]] = {}
    for row in rows:
        record = dict(row)
        by_entry.setdefault(record["entry_id"], []).append(record["direction"])
    per_study = [
        {
            "entry_id": entry_id,
            "verdict": study_verdict(directions),
            "sides": directions,
        }
        for entry_id, directions in by_entry.items()
    ]
    rollup = claim_rollup(per_study)
    return {
        "verdict": rollup["verdict"],
        "studies_accepted": rollup["studies_accepted"],
        "confident": rollup["confident"],
        "distribution": rollup["distribution"],
        "per_study": per_study,
        "explanation": explain(rollup["verdict"], per_study),
    }


@router.post("/claims/{claim_id}/analyze")
def analyze_claim(claim_id: int, data: AnalyzeIn):
    conn = get_db()
    try:
        claim = _claim_node(conn, claim_id)
        if (claim.get("node_type") or "theory") != "claim":
            raise HTTPException(
                422, "Analiza de stance se aplică doar pe un claim (node_type='claim')."
            )
        entry = conn.execute(
            "SELECT id FROM entries WHERE id = ?", [data.entry_id]
        ).fetchone()
        if not entry:
            raise HTTPException(404, "Studiul nu a fost găsit.")

        evidence_set = entry_passages(conn, data.entry_id)
        if not evidence_set["passages"]:
            raise HTTPException(
                404,
                "Studiul nu are dovezi ancorate pe offset-uri; "
                "adaugă un highlight cu start_offset și end_offset.",
            )

        claim_text = _claim_text(claim)
        scorer = _active_scorer()
        evidence = []
        strong_count = 0
        for passage in evidence_set["passages"]:
            signal = score_passage(claim_text, passage["text"], scorer)
            strong = is_strong_signal(signal)
            strong_count += 1 if strong else 0
            evidence.append(
                {
                    "highlight_id": passage["highlight_id"],
                    "page": passage["page"],
                    "start": passage["start"],
                    "end": passage["end"],
                    "quote": passage["quote"],
                    "windows_scored": len(signal["windows"]),
                    "any_assertive": signal["any_assertive"],
                    "strong": strong,
                }
            )

        return {
            "claim_id": claim_id,
            "entry_id": data.entry_id,
            "evidence": evidence,
            "skipped": evidence_set["skipped"],
            "nli": nli_availability(),
            "aggregate": {
                "study": {
                    "verdict": study_verdict([]),
                    "directions": [],
                    "strong_windows": strong_count,
                    "source": scorer.source,
                },
                "claim": _accepted_rollup(conn, claim_id),
            },
        }
    finally:
        conn.close()


@router.get("/claims/{claim_id}/analyze/capabilities")
def analyze_capabilities(claim_id: int):
    conn = get_db()
    try:
        _claim_node(conn, claim_id)
    finally:
        conn.close()
    return {
        **nli_availability(),
        "llm_rationale": {"available": False, "reason": "not_implemented"},
    }
