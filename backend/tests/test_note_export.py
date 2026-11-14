"""EXPORT lane: offline Markdown/HTML downloads for verified note dossiers."""
import re

from fastapi.testclient import TestClient

from app.main import app
from app.notes.export import (
    dossier_html,
    dossier_markdown,
    project_html,
    slugify,
)

QUOTE_A1 = (
    "Procesele proximale sunt motoarele principale ale dezvoltării "
    "și depind de contextul familial al copilului."
)
QUOTE_A2 = (
    "Memoria de lucru corelează pozitiv cu performanța școlară "
    "la copiii de vârstă mică și la adolescenți."
)


def _claim(code, statement, quote="", status="neverificat", page_pdf=None,
           page_printed="", engine="", correction="", visual=""):
    return {"id": 1, "dossier_id": 1, "code": code, "raw_note": statement,
            "statement": statement, "quote": quote, "page_pdf": page_pdf,
            "page_printed": page_printed, "engine": engine,
            "visual_check": visual, "status": status,
            "correction": correction, "method": "exact", "coverage": 0.0,
            "created_at": "2026-11-14 10:00:00", "verified_at": None}


def _payload(n=3, verified=1, title="Studiu de test"):
    claims = []
    quotes = [QUOTE_A1, QUOTE_A2, QUOTE_A2]
    for i in range(1, n + 1):
        ok = i <= verified
        claims.append(_claim(
            f"A{i}", f"Afirmația numărul {i} despre dezvoltare.",
            quote=quotes[(i - 1) % len(quotes)] if ok else "",
            status="verificat" if ok else "neverificat",
            page_pdf=1 if ok else None, page_printed="7" if ok else "",
            engine="native" if ok else ""))
    # A non-native engine exercises the Motor line.
    if n >= 2:
        claims[1]["engine"] = "ocr"
        claims[1]["visual_check"] = "nu"
    return {
        "dossier": {"id": 1, "project_id": 1, "entry_id": 1, "status": "partial",
                    "title": title,
                    "source_sheet_json": '{"Autori": "Popescu, Ionescu", "An": "2021"}',
                    "summary": "", "code_seq": n + 1,
                    "created_at": "2026-11-14 09:00:00",
                    "updated_at": "2026-11-14 10:00:00"},
        "entry": {"title": title, "authors": "Popescu, Ionescu", "year": "2021"},
        "claims": claims,
        "sections": [
            {"id": 1, "dossier_id": 1, "kind": "fisa_sursei",
             "title": "Fișa sursei", "position": 0, "body_md": "",
             "updated_at": "2026-11-14 09:00:00"},
            {"id": 2, "dossier_id": 1, "kind": "ce_spune",
             "title": "Ce spune sursa", "position": 1,
             "body_md": "Rezumatul sursei pe scurt.",
             "updated_at": "2026-11-14 09:00:00"},
            {"id": 3, "dossier_id": 1, "kind": "continut",
             "title": "Conținut", "position": 2,
             "body_md": "Detalii de conținut.",
             "updated_at": "2026-11-14 09:00:00"},
        ],
        "edges": [{"id": 1, "dossier_id": 1, "from_code": "A1",
                   "to_code": "A2", "motive": "Ambele descriu același mecanism.",
                   "page": "7", "claim_code": "A2",
                   "created_at": "2026-11-14 10:00:00"}] if n >= 2 else [],
        "rounds": [{"id": 1, "dossier_id": 1,
                    "summary": "Verificare dosar 1: 1/3 verificat.",
                    "changed_json": '["A1"]', "open_json": '["A2", "A3"]',
                    "created_at": "2026-11-14 10:00:00"}],
        "summary": {"verificat": verified, "neverificat": n - verified,
                    "total": n},
        "by_status": {"verificat": verified, "neverificat": n - verified},
    }


# ── 1. markdown carries code, statement, quote and the printed page ──

def test_markdown_contains_claim_details():
    md = dossier_markdown(_payload())
    assert "### A1 — verificat" in md
    assert "Afirmația numărul 1 despre dezvoltare." in md
    assert QUOTE_A1 in md
    assert "p. 7" in md
    assert "## Fișa sursei" in md
    assert "Autori: Popescu, Ionescu" in md
    assert "| Cod | Ce notasem | Status | Cum este în text |" in md
    assert "## Hartă" in md
    assert "- A1 → A2 — Ambele descriu același mecanism. (p. 7)" in md
    assert "## Jurnal" in md
    assert "- 2026-11-14 — Verificare dosar 1: 1/3 verificat." in md


# ── 2. fully offline: zero external URLs, zero scripts ──

def test_html_is_offline_and_script_free():
    out = dossier_html(_payload())
    assert "http://" not in out
    assert "https://" not in out
    assert "<script" not in out
    assert "<style>" in out
    assert "@page" in out
    assert "prefers-color-scheme" in out


# ── 3. every user value is escaped ──

def test_html_escapes_user_values():
    payload = _payload(n=1, verified=0)
    payload["claims"][0]["statement"] = "<script>alert(1)</script>"
    payload["claims"][0]["raw_note"] = "<script>alert(1)</script>"
    out = dossier_html(payload)
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out


# ── 4. TOC integrity: every href resolves to a rendered id ──

def test_toc_anchors_resolve():
    out = dossier_html(_payload(n=3, verified=2))
    hrefs = set(re.findall(r'href="#([^"]+)"', out))
    ids = set(re.findall(r'id="([^"]+)"', out))
    assert hrefs
    assert hrefs == ids


# ── 5. determinism: same payload, byte-identical output ──

def test_rendering_is_deterministic():
    payload = _payload()
    assert dossier_markdown(payload) == dossier_markdown(_payload())
    assert dossier_html(payload) == dossier_html(_payload())
    project = {"id": 1, "title": "Proiect", "author": "A", "work_type": "licenta"}
    assert project_html(project, [payload]) == project_html(project, [_payload()])


# ── 6. project document covers both dossiers and every concept ──

def test_project_html_covers_dossiers_and_concepts():
    first, second = _payload(title="Primul studiu"), _payload(title="Al doilea studiu")
    second["dossier"] = dict(second["dossier"], id=2)
    project = {"id": 1, "title": "Proiect Test", "author": "Autor X",
               "work_type": "licenta",
               "concepts": [{"code": "C1", "label": "Reglarea emoțională",
                             "description": "Mecanism comun.",
                             "links": [
                                 {"dossier_id": 1, "claim_code": "A1",
                                  "note": "Sprijină la preșcolari.", "page": "7"},
                                 {"dossier_id": 2, "claim_code": "A1",
                                  "note": "Confirmă la școlari.", "page": "4"},
                             ]}]}
    out = project_html(project, [first, second])
    assert "Primul studiu" in out
    assert "Al doilea studiu" in out
    assert "Reglarea emoțională" in out
    assert "Sprijină la preșcolari." in out
    assert "Harta generală" in out
    assert "http://" not in out and "https://" not in out
    assert "<script" not in out


def _study(client, title="Export study"):
    res = client.post("/api/entries", json={"title": title, "study_type": "study"})
    assert res.status_code == 201, res.text
    return res.json()


def _insert_page(conn, entry_id, page, text, printed="7"):
    conn.execute(
        """INSERT OR REPLACE INTO page_texts
           (entry_id, page_number, text, source, status, char_count, printed_page)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [entry_id, page, text, "native", "native", len(text), printed],
    )
    conn.commit()


# ── 7. dossier export endpoint: markdown + attachment filename ──

def test_export_dossier_markdown_endpoint():
    from app.db import get_db

    sentence = ("Procesele proximale sunt motoarele principale ale dezvoltării "
                "și depind de contextul familial al copilului.")
    with TestClient(app) as client:
        created = client.post("/api/projects", json={"title": "Proiect A"})
        assert created.status_code == 201, created.text
        study = _study(client)
        conn = get_db()
        try:
            _insert_page(conn, study["id"], 1, f"Intro. {sentence} Final.")
        finally:
            conn.close()
        res = client.post("/api/notes/capture",
                          json={"entry_id": study["id"], "raw_note": sentence})
        assert res.status_code == 200, res.text
        dossier_id = res.json()["dossier"]["id"]
        assert res.json()["summary"]["total"] == 1

        exported = client.get(f"/api/notes/dossiers/{dossier_id}/export?format=md")
        assert exported.status_code == 200, exported.text
        assert "text/markdown" in exported.headers["content-type"]
        disposition = exported.headers["content-disposition"]
        assert "attachment" in disposition
        assert disposition.rstrip('"').endswith(".md")
        assert "A1" in exported.text

        html_res = client.get(
            f"/api/notes/dossiers/{dossier_id}/export?format=html")
        assert html_res.status_code == 200, html_res.text
        assert "text/html" in html_res.headers["content-type"]
        assert "<article" in html_res.text


# ── 8. isolation holds; unknown formats are rejected in Romanian ──

def test_export_isolation_and_bad_format():
    with TestClient(app) as client:
        created_a = client.post("/api/projects", json={"title": "Proiect A"})
        assert created_a.status_code == 201, created_a.text
        study = _study(client)
        res = client.post("/api/notes/capture",
                          json={"entry_id": study["id"], "raw_note": "O notiță izolată."})
        assert res.status_code == 200, res.text
        dossier_id = res.json()["dossier"]["id"]

        created = client.post("/api/projects", json={"title": "Proiect B"})
        assert created.status_code == 201, created.text
        assert created.json()["id"] != created_a.json()["id"]

        missing = client.get(f"/api/notes/dossiers/{dossier_id}/export?format=md")
        assert missing.status_code == 404, missing.text

        bad = client.get("/api/notes/export?format=xml")
        assert bad.status_code == 400, bad.text
        assert "Format necunoscut" in bad.json()["detail"]

        bad_one = client.get(
            f"/api/notes/dossiers/{dossier_id}/export?format=xml")
        assert bad_one.status_code == 400, bad_one.text


def test_slugify_ascii_dashes_and_fallback():
    assert slugify("Dezvoltarea copilului în familie") == "dezvoltarea-copilului-in-familie"
    assert slugify("") == "dosar"
    assert len(slugify("a" * 100)) <= 60
