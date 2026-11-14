"""Verified note dossiers (backend foundation lane): capture, verify, isolation."""
import pytest
from fastapi.testclient import TestClient

from app.main import app


SENTENCE = (
    "Procesele proximale sunt motoarele principale ale dezvoltării "
    "și depind de contextul familial al copilului."
)
ABSENT = (
    "Ipoteza câmpului cuantic determină scorurile de anxietate "
    "la adolescenții din mediul urban."
)
FABRICATED = (
    "Rezultatele analizei factoriale confirmă ipoteza câmpului cuantic "
    "în totalitate și fără excepție."
)


def _study(client, title="Dossier study"):
    res = client.post("/api/entries", json={"title": title, "study_type": "study"})
    assert res.status_code == 201, res.text
    return res.json()


def _insert_page(conn, entry_id, page, text, printed="", source="native"):
    conn.execute(
        """INSERT OR REPLACE INTO page_texts
           (entry_id, page_number, text, source, status, char_count, printed_page)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [entry_id, page, text, source, source, len(text), printed],
    )
    conn.commit()


def _capture(client, entry_id, note, page=None):
    res = client.post(
        "/api/notes/capture",
        json={"entry_id": entry_id, "raw_note": note, "page": page},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _claims_by_code(payload):
    return {c["code"]: c for c in payload["claims"]}


def _fail_on_extract(entry_id, path):
    raise AssertionError(f"extraction must not run (entry {entry_id}, {path})")


def _make_text_pdf(path, text) -> None:
    """Build a one-page native PDF carrying *text* (test tmp dir)."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()


# ── 1. absent statement → neverificat, empty quote, no fabrication ──


def test_capture_absent_statement_is_neverificat_without_quote():
    from app.db import get_db

    with TestClient(app) as client:
        study = _study(client)
        conn = get_db()
        try:
            _insert_page(
                conn,
                study["id"],
                1,
                "Eșantionul a cuprins două sute de profesori din școli rurale.",
                printed="3",
            )
        finally:
            conn.close()
        payload = _capture(client, study["id"], ABSENT)
    assert payload["summary"]["total"] == 1
    claim = payload["claims"][0]
    assert claim["status"] == "neverificat"
    assert claim["quote"] == ""
    assert claim["code"] == "A1"
    assert "Nu am găsit pasajul" in claim["correction"]


# ── 2. verbatim statement → verificat, exact method ──


def test_capture_verbatim_statement_is_verificat_exact():
    from app.db import get_db

    with TestClient(app) as client:
        study = _study(client)
        conn = get_db()
        try:
            _insert_page(
                conn,
                study["id"],
                2,
                f"Introducere. {SENTENCE} Text suplimentar care continuă paragraful.",
                printed="7",
            )
        finally:
            conn.close()
        payload = _capture(client, study["id"], SENTENCE)
    assert payload["summary"] == {"verificat": 1, "neverificat": 0, "total": 1}
    claim = payload["claims"][0]
    assert claim["status"] == "verificat"
    assert claim["method"] == "exact"
    assert claim["page_pdf"] == 2
    assert claim["page_printed"] == "7"
    assert claim["quote"] == SENTENCE
    assert payload["dossier"]["status"] == "verificat"


# ── 3. fabricated LLM quote is rejected ──


def test_llm_fabricated_quote_is_rejected(monkeypatch):
    from app.db import get_db

    async def fake_judge(statement, candidates, note=""):
        return [{
            "page_pdf": 1,
            "quote": FABRICATED,
            "status": "verificat",
            "correction": "",
            "reason": "invented",
        }]

    monkeypatch.setattr("app.notes.engine._llm_judge", fake_judge)
    with TestClient(app) as client:
        study = _study(client)
        conn = get_db()
        try:
            _insert_page(
                conn,
                study["id"],
                1,
                "Eșantionul a cuprins două sute de profesori din școli rurale.",
                printed="3",
            )
        finally:
            conn.close()
        payload = _capture(client, study["id"], ABSENT)
    claim = payload["claims"][0]
    assert claim["status"] == "neverificat"
    assert claim["quote"] == ""
    assert "nu există pe pagina declarată" in claim["correction"]


# ── 4. real LLM quote is accepted ──


def test_llm_real_quote_is_accepted(monkeypatch):
    from app.db import get_db

    page_text = (
        "Memoria de lucru corelează pozitiv cu performanța școlară "
        "la copiii de vârstă mică și la adolescenți."
    )
    statement = (
        "Analiza arată o legătură între memoria de lucru și rezultatele "
        "școlare la elevi, cu implicații practice clare."
    )

    async def fake_judge(_statement, _candidates, _note=""):
        return [{
            "page_pdf": 1,
            "quote": page_text,
            "status": "verificat",
            "correction": "",
            "reason": "parafrazare fidelă",
        }]

    monkeypatch.setattr("app.notes.engine._llm_judge", fake_judge)
    with TestClient(app) as client:
        study = _study(client)
        conn = get_db()
        try:
            _insert_page(conn, study["id"], 1, page_text, printed="9")
        finally:
            conn.close()
        payload = _capture(client, study["id"], statement)
    claim = payload["claims"][0]
    assert claim["status"] == "verificat"
    assert claim["method"] == "llm"
    assert claim["page_pdf"] == 1
    assert claim["page_printed"] == "9"
    assert claim["quote"] == page_text


# ── 5. project isolation ──


def test_dossiers_are_isolated_per_project():
    from app.db import get_db

    with TestClient(app) as client:
        study = _study(client)
        conn = get_db()
        try:
            _insert_page(
                conn,
                study["id"],
                1,
                "Eșantionul a cuprins două sute de profesori din școli rurale.",
                printed="3",
            )
        finally:
            conn.close()

        proj_a = client.get("/api/projects").json()["active_id"]
        payload_a = _capture(client, study["id"], ABSENT)
        dossier_a = payload_a["dossier"]["id"]
        assert payload_a["dossier"]["project_id"] == proj_a

        created = client.post("/api/projects", json={"title": "Proiect B"})
        assert created.status_code == 201, created.text
        proj_b = created.json()["id"]
        assert proj_b != proj_a

        payload_b = _capture(client, study["id"], ABSENT)
        dossier_b = payload_b["dossier"]["id"]
        assert dossier_b != dossier_a
        assert payload_b["dossier"]["project_id"] == proj_b

        listed = client.get("/api/notes/dossiers")
        assert listed.status_code == 200
        assert [d["id"] for d in listed.json()["dossiers"]] == [dossier_b]

        # Dossier A is invisible while B is active.
        assert client.get(f"/api/notes/dossiers/{dossier_a}").status_code == 404

        assert client.post(f"/api/projects/{proj_a}/activate").status_code == 200
        assert client.get(f"/api/notes/dossiers/{dossier_b}").status_code == 404
        again = client.get("/api/notes/dossiers").json()["dossiers"]
        assert [d["id"] for d in again] == [dossier_a]


# ── 6. code stability ──


def test_claim_codes_are_stable_and_never_reused():
    from app.db import get_db

    with TestClient(app) as client:
        study = _study(client)
        conn = get_db()
        try:
            _insert_page(
                conn,
                study["id"],
                1,
                f"Introducere. {SENTENCE} Continuare fără legătură directă.",
                printed="5",
            )
        finally:
            conn.close()

        payload = _capture(client, study["id"], f"{SENTENCE} {ABSENT}")
        dossier_id = payload["dossier"]["id"]
        assert sorted(_claims_by_code(payload)) == ["A1", "A2"]

        verified = client.post(
            f"/api/notes/dossiers/{dossier_id}/verify", json={"recheck": True}
        )
        assert verified.status_code == 200, verified.text
        assert sorted(_claims_by_code(verified.json())) == ["A1", "A2"]

        conn = get_db()
        try:
            conn.execute(
                "DELETE FROM note_claims WHERE dossier_id = ? AND code = 'A2'",
                [dossier_id],
            )
            conn.commit()
        finally:
            conn.close()

        third = (
            "Climatul clasei influențează motivația elevilor pe termen lung "
            "și merită investigat suplimentar."
        )
        payload2 = _capture(client, study["id"], third)
        assert sorted(_claims_by_code(payload2)) == ["A1", "A3"]


# ── 7. OCR page without visual check cannot be verificat ──


def test_ocr_page_requires_visual_check():
    from app.db import get_db

    with TestClient(app) as client:
        study = _study(client)
        conn = get_db()
        try:
            _insert_page(
                conn, study["id"], 4, f"Scan. {SENTENCE}", printed="33", source="ocr"
            )
        finally:
            conn.close()

        payload = _capture(client, study["id"], SENTENCE)
        dossier_id = payload["dossier"]["id"]
        claim = payload["claims"][0]
        assert claim["status"] == "neverificat"
        assert "neverificat vizual" in claim["correction"]
        # The evidence is kept for the human visual check: nothing to re-pay.
        assert claim["quote"] == SENTENCE
        assert claim["page_pdf"] == 4
        assert claim["page_printed"] == "33"
        assert payload["dossier"]["status"] == "draft"

        patched = client.patch(
            f"/api/notes/claims/{claim['id']}", json={"visual_check": "da"}
        )
        assert patched.status_code == 200, patched.text

        verified = client.post(
            f"/api/notes/dossiers/{dossier_id}/verify", json={}
        )
        assert verified.status_code == 200, verified.text
        claim2 = _claims_by_code(verified.json())["A1"]
        assert claim2["status"] == "verificat"
        assert claim2["method"] == "exact"


# ── 8. PATCH to verificat with a fabricated quote → 400 ──


def test_patch_verificat_with_fabricated_quote_is_rejected():
    from app.db import get_db

    with TestClient(app) as client:
        study = _study(client)
        conn = get_db()
        try:
            _insert_page(
                conn,
                study["id"],
                1,
                "Eșantionul a cuprins două sute de profesori din școli rurale.",
                printed="3",
            )
        finally:
            conn.close()

        payload = _capture(client, study["id"], ABSENT)
        claim = payload["claims"][0]
        assert claim["status"] == "neverificat"

        bad = client.patch(
            f"/api/notes/claims/{claim['id']}",
            json={"status": "verificat", "quote": FABRICATED, "page_pdf": 1},
        )
        assert bad.status_code == 400, bad.text
        assert "Citatul nu a fost găsit" in bad.json()["detail"]

        dossier = client.get(f"/api/notes/dossiers/{payload['dossier']['id']}")
        assert dossier.status_code == 200
        assert _claims_by_code(dossier.json())["A1"]["status"] == "neverificat"


def test_delete_dossier_is_project_scoped():
    with TestClient(app) as client:
        study = _study(client)
        payload = _capture(client, study["id"], ABSENT)
        dossier_id = payload["dossier"]["id"]

        deleted = client.delete(f"/api/notes/dossiers/{dossier_id}")
        assert deleted.status_code == 200
        assert client.get(f"/api/notes/dossiers/{dossier_id}").status_code == 404


# ── 10. duplicate capture adds nothing ──


def test_capture_same_note_twice_keeps_single_claim():
    from app.db import get_db

    with TestClient(app) as client:
        study = _study(client)
        conn = get_db()
        try:
            _insert_page(
                conn, study["id"], 1, f"Intro. {SENTENCE} Continuare.", printed="5"
            )
        finally:
            conn.close()

        first = _capture(client, study["id"], SENTENCE)
        assert first["summary"]["total"] == 1
        second = _capture(client, study["id"], SENTENCE)
        assert second["summary"]["total"] == 1
        assert [c["code"] for c in second["claims"]] == ["A1"]
        assert second["claims"][0]["status"] == "verificat"


# ── 12. entry without extracted pages reads as "not extracted" ──


def test_capture_without_page_texts_reports_missing_extraction():
    with TestClient(app) as client:
        study = _study(client)
        payload = _capture(client, study["id"], ABSENT)
        assert payload["summary"]["total"] == 1
        claim = payload["claims"][0]
        assert claim["status"] == "neverificat"
        assert claim["quote"] == ""
        assert "nu a fost încă extras" in claim["correction"]


# ── 13. scanned entry (pages without text) asks for OCR, not "not found" ──


def test_capture_scanned_pages_report_ocr_without_quote():
    from app.db import get_db

    with TestClient(app) as client:
        study = _study(client)
        conn = get_db()
        try:
            _insert_page(conn, study["id"], 1, "")
            _insert_page(conn, study["id"], 2, "   ")
        finally:
            conn.close()
        payload = _capture(client, study["id"], ABSENT)
        assert payload["summary"]["total"] == 1
        claim = payload["claims"][0]
        assert claim["status"] == "neverificat"
        assert claim["quote"] == ""
        assert claim["page_pdf"] is None
        assert "OCR" in claim["correction"]


# ── 14. missing pages trigger one off-loop extraction when a PDF exists ──


def test_capture_extracts_missing_pages_once(monkeypatch):
    import os
    from pathlib import Path

    import app.notes.service as notes_service

    calls = []

    def fake_extract(entry_id, path):
        from app.db import get_db as _get_db

        calls.append((entry_id, path))
        conn = _get_db()
        try:
            _insert_page(
                conn, entry_id, 1, f"Intro. {SENTENCE} Final.", printed="5"
            )
        finally:
            conn.close()
        return 1

    monkeypatch.setattr(notes_service, "_extract_in_thread", fake_extract)
    with TestClient(app) as client:
        study = _study(client)
        studies_dir = Path(os.environ["CHRONOKG_STUDIES_DIR"])
        studies_dir.mkdir(parents=True, exist_ok=True)
        dest = studies_dir / f"{study['id']}-scan.pdf"
        dest.write_bytes(b"%PDF-1.4 fake")
        from app.db import get_db

        conn = get_db()
        try:
            conn.execute(
                "UPDATE entries SET pdf_path = ? WHERE id = ?",
                [dest.name, study["id"]],
            )
            conn.commit()
        finally:
            conn.close()
        payload = _capture(client, study["id"], SENTENCE)
    assert len(calls) == 1
    assert calls[0][0] == study["id"]
    claim = payload["claims"][0]
    assert claim["status"] == "verificat"
    assert claim["page_pdf"] == 1


# ── 15. no re-extraction when page rows already exist ──


def test_capture_skips_extraction_when_pages_exist(monkeypatch):
    import os
    from pathlib import Path

    from app.db import get_db
    import app.notes.service as notes_service

    monkeypatch.setattr(
        notes_service, "_extract_in_thread", _fail_on_extract
    )
    with TestClient(app) as client:
        study = _study(client)
        studies_dir = Path(os.environ["CHRONOKG_STUDIES_DIR"])
        studies_dir.mkdir(parents=True, exist_ok=True)
        dest = studies_dir / f"{study['id']}-paper.pdf"
        dest.write_bytes(b"%PDF-1.4 fake")
        conn = get_db()
        try:
            conn.execute(
                "UPDATE entries SET pdf_path = ? WHERE id = ?",
                [dest.name, study["id"]],
            )
            conn.commit()
            _insert_page(
                conn, study["id"], 1, f"Intro. {SENTENCE} Final.", printed="5"
            )
        finally:
            conn.close()
        payload = _capture(client, study["id"], SENTENCE)
    assert payload["claims"][0]["status"] == "verificat"


# ── 16. a real unindexed PDF is extracted on the spot ──


def test_capture_extracts_real_unindexed_pdf():
    import os
    from pathlib import Path

    from app.db import get_db

    statement = (
        "Atasamentul sigur prezice reglarea emotionala "
        "si adaptarea scolara a copilului."
    )
    with TestClient(app) as client:
        study = _study(client)
        pdf = Path(os.environ.get("PYTEST_TMP", "/tmp")) / f"map-{study['id']}.pdf"
        _make_text_pdf(pdf, statement)
        studies_dir = Path(os.environ["CHRONOKG_STUDIES_DIR"])
        studies_dir.mkdir(parents=True, exist_ok=True)
        dest = studies_dir / f"{study['id']}-paper.pdf"
        dest.write_bytes(pdf.read_bytes())
        conn = get_db()
        try:
            conn.execute(
                "UPDATE entries SET pdf_path = ? WHERE id = ?",
                [dest.name, study["id"]],
            )
            conn.commit()
        finally:
            conn.close()
        payload = _capture(client, study["id"], statement)
    assert payload["summary"]["total"] == 1
    claim = payload["claims"][0]
    assert claim["status"] == "verificat"
    assert claim["page_pdf"] == 1
    assert "nu a fost încă extras" not in claim["correction"]


# ── 17. re-verifying a verified dossier does not rewrite evidence (REQ-NOTE-09) ──


def test_reverify_keeps_verified_claim_byte_identical():
    from app.db import get_db

    with TestClient(app) as client:
        study = _study(client)
        conn = get_db()
        try:
            _insert_page(conn, study["id"], 1, f"Intro. {SENTENCE} Final.", printed="5")
        finally:
            conn.close()

        payload = _capture(client, study["id"], SENTENCE)
        dossier_id = payload["dossier"]["id"]
        claim = payload["claims"][0]
        assert claim["status"] == "verificat"
        before = (claim["status"], claim["verified_at"], claim["quote"])

        conn = get_db()
        try:
            claims_before = conn.execute(
                "SELECT COUNT(*) AS n FROM note_claims WHERE dossier_id = ?",
                [dossier_id],
            ).fetchone()["n"]
            rounds_before = conn.execute(
                "SELECT COUNT(*) AS n FROM note_rounds WHERE dossier_id = ?",
                [dossier_id],
            ).fetchone()["n"]
        finally:
            conn.close()

        again = client.post(f"/api/notes/dossiers/{dossier_id}/verify", json={})
        assert again.status_code == 200, again.text
        refreshed = _claims_by_code(again.json())["A1"]
        assert (refreshed["status"], refreshed["verified_at"], refreshed["quote"]) == before

        conn = get_db()
        try:
            claims_after = conn.execute(
                "SELECT COUNT(*) AS n FROM note_claims WHERE dossier_id = ?",
                [dossier_id],
            ).fetchone()["n"]
            rounds_after = conn.execute(
                "SELECT COUNT(*) AS n FROM note_rounds WHERE dossier_id = ?",
                [dossier_id],
            ).fetchone()["n"]
        finally:
            conn.close()
        assert claims_after == claims_before  # no duplicate claim rows
        assert rounds_after == rounds_before + 1  # exactly one new round
