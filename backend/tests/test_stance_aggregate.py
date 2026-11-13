"""Slice 6 — hardcoded aggregation + the /analyze dry run (no LLM, no writes)."""
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app
from app.stance.aggregate import claim_rollup, explain, study_verdict
from app.stance.signals import nli_availability


def _theory(client, name="T"):
    resp = client.post("/api/theories", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _claim(client, parent_id, name="C", statement="X crește în condiția Z."):
    resp = client.post(
        "/api/claims",
        json={"parent_id": parent_id, "name": name, "statement": statement},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _entry(client, title="Study"):
    resp = client.post("/api/entries", json={"title": title, "content": "c"})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _page(entry_id, page, text):
    conn = get_db()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO page_texts
               (entry_id, page_number, text, source, status, char_count)
               VALUES (?, ?, ?, 'manual', 'manual', ?)""",
            [entry_id, page, text, len(text)],
        )
        conn.commit()
    finally:
        conn.close()


def _highlight(entry_id, page, quote, start=None, end=None):
    conn = get_db()
    try:
        cur = conn.execute(
            """INSERT INTO pdf_highlights
               (entry_id, page_number, quote, start_offset, end_offset)
               VALUES (?, ?, ?, ?, ?)""",
            [entry_id, page, quote, start, end],
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _stance_count():
    conn = get_db()
    try:
        return conn.execute("SELECT COUNT(*) FROM claim_stance").fetchone()[0]
    finally:
        conn.close()


# ── study_verdict ──

def test_study_verdict_all_branches():
    assert study_verdict([]) == "insufficient"
    assert study_verdict(["supports", "supports"]) == "supports"
    assert study_verdict(["contradicts"]) == "contradicts"
    assert study_verdict(["neutral", "neutral"]) == "neutral"
    assert study_verdict(["supports", "contradicts"]) == "mixed"
    # Neutral never dilutes an otherwise unanimous direction.
    assert study_verdict(["supports", "neutral"]) == "supports"


# ── claim_rollup ──

def _study(entry_id, verdict, sides=None):
    return {"entry_id": entry_id, "verdict": verdict, "sides": sides or []}


def test_claim_rollup_majority_tie_and_threshold():
    majority = claim_rollup(
        [_study(1, "supports"), _study(2, "supports"), _study(3, "contradicts")]
    )
    assert majority["verdict"] == "supports"
    assert majority["studies_accepted"] == 3
    assert majority["confident"] is True
    assert majority["distribution"] == {
        "supports": 2, "contradicts": 1, "mixed": 0, "neutral": 0, "insufficient": 0,
    }

    tie = claim_rollup([_study(1, "supports"), _study(2, "contradicts")])
    assert tie["verdict"] == "mixed"

    single = claim_rollup([_study(1, "supports")])
    assert single["verdict"] == "insufficient"
    assert single["confident"] is False
    assert single["studies_accepted"] == 1


def test_claim_rollup_insufficient_studies_do_not_vote():
    result = claim_rollup(
        [_study(1, "supports"), _study(2, "supports"), _study(3, "insufficient")]
    )
    assert result["verdict"] == "supports"
    assert result["studies_accepted"] == 2
    assert result["distribution"]["insufficient"] == 1


# ── explain ──

def test_explain_names_counts_in_romanian():
    sentence = explain(
        "mixed", [_study(1, "supports"), _study(2, "supports"), _study(3, "contradicts")]
    )
    assert sentence == "2 studii susțin, 1 studiu contrazice → mixt"
    assert explain("insufficient", []) == "niciun studiu acceptat → insuficient"


# ── POST /api/claims/{id}/analyze ──

def test_analyze_dry_run_returns_evidence_and_writes_nothing():
    with TestClient(app) as client:
        theory = _theory(client)
        claim = _claim(client, theory["id"])
        entry = _entry(client)
        text = (
            "Rezultatele arată clar că X crește semnificativ în condiția Z, "
            "spre deosebire de grupul de control, iar efectul este stabil."
        )
        _page(entry["id"], 1, text)
        highlight_id = _highlight(entry["id"], 1, text, 0, len(text))

        before = _stance_count()
        resp = client.post(
            f"/api/claims/{claim['id']}/analyze", json={"entry_id": entry["id"]}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["claim_id"] == claim["id"]
        assert body["entry_id"] == entry["id"]
        assert len(body["evidence"]) == 1
        item = body["evidence"][0]
        assert item["highlight_id"] == highlight_id
        assert item["page"] == 1
        assert item["start"] == 0 and item["end"] == len(text)
        assert item["quote"] == text
        assert item["windows_scored"] >= 1
        assert item["any_assertive"] is True
        assert isinstance(item["strong"], bool)
        assert body["nli"] == nli_availability()
        assert body["skipped"] == []
        assert body["aggregate"]["study"]["verdict"] == "insufficient"
        assert body["aggregate"]["claim"]["verdict"] == "insufficient"
        assert _stance_count() == before == 0

        capabilities = client.get(
            f"/api/claims/{claim['id']}/analyze/capabilities"
        ).json()
        assert capabilities["available"] == nli_availability()["available"]
        assert capabilities["llm_rationale"] == {
            "available": False,
            "reason": "not_implemented",
        }


def test_analyze_on_a_theory_is_422():
    with TestClient(app) as client:
        theory = _theory(client)
        entry = _entry(client)
        _page(entry["id"], 1, "Text suficient de lung pentru o fereastră de trei propoziții aici.")
        _highlight(entry["id"], 1, "Text", 0, 4)
        resp = client.post(
            f"/api/claims/{theory['id']}/analyze", json={"entry_id": entry["id"]}
        )
        assert resp.status_code == 422
        assert "claim" in resp.json()["detail"]


def test_analyze_without_evidence_is_404():
    with TestClient(app) as client:
        theory = _theory(client)
        claim = _claim(client, theory["id"])
        entry = _entry(client)
        resp = client.post(
            f"/api/claims/{claim['id']}/analyze", json={"entry_id": entry["id"]}
        )
        assert resp.status_code == 404
