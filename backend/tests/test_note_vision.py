"""REQ-NOTE-10: a claim on a page without native text reaches `verificat`
only after its quote is confirmed against the page image.

Render and vision are monkeypatched (`app.notes.vision.render_page_png`,
`app.notes.vision.parse_scanned_page`); no tesseract binary and no real
model are ever touched.
"""
import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

SENTENCE = (
    "Procesele proximale sunt motoarele principale ale dezvoltării "
    "și depind de contextul familial al copilului."
)
OTHER = (
    "Memoria de lucru corelează pozitiv cu performanța școlară "
    "la copiii de vârstă mică și la adolescenți."
)


def _study(client, title="Vision study"):
    res = client.post("/api/entries", json={"title": title, "study_type": "study"})
    assert res.status_code == 201, res.text
    return res.json()


def _active_project(client):
    return client.get("/api/projects").json()["active_id"]


def _insert_page(conn, entry_id, page, text, source="ocr",
                 status="vision-uncertain", printed="33"):
    conn.execute(
        """INSERT OR REPLACE INTO page_texts
           (entry_id, page_number, text, source, status, char_count, printed_page)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [entry_id, page, text, source, status, len(text), printed],
    )
    conn.commit()


def _link_pdf(conn, entry_id):
    """Give the entry a readable PDF file (render itself is monkeypatched)."""
    studies = Path(os.environ["CHRONOKG_STUDIES_DIR"])
    studies.mkdir(parents=True, exist_ok=True)
    dest = studies / f"{entry_id}.pdf"
    dest.write_bytes(b"%PDF-1.4 fake-bytes pentru test")
    conn.execute("UPDATE entries SET pdf_path = ? WHERE id = ?",
                 [dest.name, entry_id])
    conn.commit()


def _insert_claim(conn, project_id, entry_id, statement, quote, page_pdf=1,
                  status="neverificat", visual=""):
    cur = conn.execute(
        "INSERT INTO note_dossiers (project_id, entry_id, title, code_seq)"
        " VALUES (?, ?, ?, ?)",
        [project_id, entry_id, "Titlu", 1],
    )
    dossier_id = cur.lastrowid
    cur2 = conn.execute(
        """INSERT INTO note_claims
           (dossier_id, code, raw_note, statement, quote, page_pdf,
            page_printed, status, visual_check, correction, method, engine)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [dossier_id, "A1", statement, statement, quote, page_pdf,
         "33" if page_pdf is not None else "", status, visual, "", "exact", "ocr"],
    )
    conn.commit()
    return dossier_id, cur2.lastrowid


def _fake_vision(monkeypatch, vision_text, verdict="match",
                 status="vision-ok", calls=None):
    render_calls = [] if calls is None else calls

    def fake_render(path, page_number, dpi=150):
        render_calls.append((str(path), page_number, dpi))
        return b"fake-png-bytes"

    def fake_parse(png, provider=None, model=None, lang=None):
        assert png == b"fake-png-bytes"
        return {
            "text": vision_text,
            "status": status,
            "reasons": [],
            "ocr_text": vision_text,
            "vision_text": vision_text,
            "verdict": verdict,
            "lang": "eng",
            "provider": "test",
            "model": "test",
            "ocr_elapsed_ms": 1,
        }

    monkeypatch.setattr("app.notes.vision.render_page_png", fake_render)
    monkeypatch.setattr("app.notes.vision.parse_scanned_page", fake_parse)
    return render_calls


# ── 1. OCR page + vision reading containing the quote → verificat ──

def test_vision_match_confirms_ocr_claim(monkeypatch):
    from app.db import get_db

    calls = _fake_vision(monkeypatch, f"Intro. {SENTENCE} Final.")
    with TestClient(app) as client:
        study = _study(client)
        project_id = _active_project(client)
        conn = get_db()
        try:
            _insert_page(conn, study["id"], 1, f"Scan. {SENTENCE} Continuare.")
            _link_pdf(conn, study["id"])
            _, claim_id = _insert_claim(conn, project_id, study["id"],
                                        SENTENCE, SENTENCE)
        finally:
            conn.close()
        res = client.post(f"/api/notes/claims/{claim_id}/visual-check", json={})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["result"] == "confirmat"
    assert body["visual_check"] == "da"
    assert body["claim"]["status"] == "verificat"
    assert body["claim"]["visual_check"] == "da"
    assert body["claim"]["quote"] == SENTENCE
    assert calls, "renderer must run for an OCR page without a saved image"
    audit = json.loads(body["claim"]["vision_json"])
    assert audit["verdict"] == "match"
    assert audit["status"] == "vision-ok"
    assert audit["quote_found"] is True
    assert audit["checked_at"]


# ── 2. vision reading without the quote → necesar, neverificat, quote kept ──

def test_vision_mismatch_keeps_evidence_and_stays_neverificat(monkeypatch):
    from app.db import get_db

    calls = _fake_vision(monkeypatch, OTHER, verdict="mismatch",
                         status="vision-uncertain")
    with TestClient(app) as client:
        study = _study(client)
        project_id = _active_project(client)
        conn = get_db()
        try:
            _insert_page(conn, study["id"], 1, f"Scan. {SENTENCE} Continuare.")
            _link_pdf(conn, study["id"])
            _, claim_id = _insert_claim(conn, project_id, study["id"],
                                        SENTENCE, SENTENCE)
        finally:
            conn.close()
        res = client.post(f"/api/notes/claims/{claim_id}/visual-check", json={})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["result"] == "neconfirmat"
    assert body["visual_check"] == "necesar"
    assert body["claim"]["status"] == "neverificat"
    assert body["claim"]["visual_check"] == "necesar"
    # Evidence is never erased by a failed vision step.
    assert body["claim"]["quote"] == SENTENCE
    assert body["claim"]["page_pdf"] == 1
    assert calls
    audit = json.loads(body["claim"]["vision_json"])
    assert audit["quote_found"] is False


# ── 3. vision model unavailable → necesar, no exception, neverificat ──

def test_vision_failure_is_indisponibil_not_an_exception(monkeypatch):
    from app.db import get_db

    def fake_render(path, page_number, dpi=150):
        return b"fake-png-bytes"

    def boom(png, provider=None, model=None, lang=None):
        raise RuntimeError("Modelul cu imagini este indisponibil.")

    monkeypatch.setattr("app.notes.vision.render_page_png", fake_render)
    monkeypatch.setattr("app.notes.vision.parse_scanned_page", boom)
    with TestClient(app) as client:
        study = _study(client)
        project_id = _active_project(client)
        conn = get_db()
        try:
            _insert_page(conn, study["id"], 1, f"Scan. {SENTENCE} Continuare.")
            _link_pdf(conn, study["id"])
            _, claim_id = _insert_claim(conn, project_id, study["id"],
                                        SENTENCE, SENTENCE)
        finally:
            conn.close()
        res = client.post(f"/api/notes/claims/{claim_id}/visual-check", json={})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["result"] == "indisponibil"
    assert body["reason"]
    assert body["visual_check"] == "necesar"
    assert body["claim"]["status"] == "neverificat"
    assert body["claim"]["visual_check"] == "necesar"


# ── 4. native page → early return, renderer never called ──

def test_native_page_returns_early_without_render(monkeypatch):
    from app.db import get_db

    calls = _fake_vision(monkeypatch, f"Intro. {SENTENCE} Final.")
    with TestClient(app) as client:
        study = _study(client)
        project_id = _active_project(client)
        conn = get_db()
        try:
            _insert_page(conn, study["id"], 1, f"Intro. {SENTENCE} Final.",
                         source="native", status="native")
            _, claim_id = _insert_claim(conn, project_id, study["id"],
                                        SENTENCE, SENTENCE)
        finally:
            conn.close()
        res = client.post(f"/api/notes/claims/{claim_id}/visual-check", json={})
    assert res.status_code == 200, res.text
    assert res.json()["result"] == "text nativ"
    assert calls == []


# ── 5. claim without a quote → early return, no render ──

def test_claim_without_quote_returns_early(monkeypatch):
    from app.db import get_db

    calls = _fake_vision(monkeypatch, f"Intro. {SENTENCE} Final.")
    with TestClient(app) as client:
        study = _study(client)
        project_id = _active_project(client)
        conn = get_db()
        try:
            _insert_page(conn, study["id"], 1, f"Scan. {SENTENCE} Continuare.")
            _, claim_id = _insert_claim(conn, project_id, study["id"],
                                        SENTENCE, "")
        finally:
            conn.close()
        res = client.post(f"/api/notes/claims/{claim_id}/visual-check", json={})
    assert res.status_code == 200, res.text
    assert res.json()["result"] == "fără citat"
    assert calls == []


# ── 6. another project's claim → 404 ──

def test_visual_check_is_project_scoped():
    with TestClient(app) as client:
        study = _study(client)
        from app.db import get_db
        conn = get_db()
        try:
            project_a = _active_project(client)
            _insert_page(conn, study["id"], 1, f"Scan. {SENTENCE} Continuare.")
            _, claim_id = _insert_claim(conn, project_a, study["id"],
                                        SENTENCE, SENTENCE)
        finally:
            conn.close()
        created = client.post("/api/projects", json={"title": "Proiect B"})
        assert created.status_code == 201, created.text
        res = client.post(f"/api/notes/claims/{claim_id}/visual-check", json={})
        assert res.status_code == 404, res.text


# ── 7. force=False on a confirmed claim → no second render ──

def test_confirmed_claim_is_not_rerendered_without_force(monkeypatch):
    from app.db import get_db

    calls = _fake_vision(monkeypatch, f"Intro. {SENTENCE} Final.")
    with TestClient(app) as client:
        study = _study(client)
        project_id = _active_project(client)
        conn = get_db()
        try:
            _insert_page(conn, study["id"], 1, f"Scan. {SENTENCE} Continuare.")
            _link_pdf(conn, study["id"])
            _, claim_id = _insert_claim(conn, project_id, study["id"],
                                        SENTENCE, SENTENCE, visual="da")
        finally:
            conn.close()
        res = client.post(f"/api/notes/claims/{claim_id}/visual-check", json={})
    assert res.status_code == 200, res.text
    assert res.json()["visual_check"] == "da"
    assert calls == []


# ── 8. after a failed vision check the OCR gate still blocks verificat ──

def test_failed_vision_check_keeps_ocr_gate_closed(monkeypatch):
    from app.db import get_db

    _fake_vision(monkeypatch, OTHER, verdict="mismatch",
                 status="vision-uncertain")
    with TestClient(app) as client:
        study = _study(client)
        project_id = _active_project(client)
        conn = get_db()
        try:
            _insert_page(conn, study["id"], 1, f"Scan. {SENTENCE} Continuare.")
            _link_pdf(conn, study["id"])
            dossier_id, claim_id = _insert_claim(conn, project_id, study["id"],
                                                 SENTENCE, SENTENCE)
        finally:
            conn.close()
        first = client.post(f"/api/notes/claims/{claim_id}/visual-check", json={})
        assert first.status_code == 200, first.text
        assert first.json()["result"] == "neconfirmat"
        # A full dossier re-verification must not lift the claim either:
        # the OCR gate keeps quote + page but forces `neverificat`.
        verified = client.post(f"/api/notes/dossiers/{dossier_id}/verify",
                               json={"recheck": True})
        assert verified.status_code == 200, verified.text
        claim = {c["code"]: c for c in verified.json()["claims"]}["A1"]
        assert claim["status"] == "neverificat"
        assert claim["quote"] == SENTENCE
        assert claim["page_pdf"] == 1
        assert "neverificat vizual" in claim["correction"]
