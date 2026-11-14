"""STRUCTURE layer: compose dossier prose sections from verified claims.

Never call a real model: ``app.notes.compose._llm_sections`` is patched in
every test that reaches the LLM path.
"""
from fastapi.testclient import TestClient

from app.main import app


S1 = (
    "Relația părinte copil prezice reglarea emoțională la vârsta preșcolară."
)
S2 = (
    "Reglarea emoțională timpurie prezice adaptarea școlară ulterioară a copilului."
)
ABSENT = (
    "Ipoteza câmpului cuantic determină scorurile de anxietate "
    "la adolescenții din mediul urban."
)


def _study(client, title="Compose study"):
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


def _verified_pair(client):
    """One dossier with claims A1 + A2, both ``verificat`` via exact match."""
    from app.db import get_db

    study = _study(client)
    conn = get_db()
    try:
        _insert_page(conn, study["id"], 1, f"Intro. {S1} {S2} Final.", printed="4")
    finally:
        conn.close()
    payload = _capture(client, study["id"], f"{S1} {S2}")
    assert payload["summary"] == {"verificat": 2, "neverificat": 0, "total": 2}
    return study, payload


def _sections(payload):
    return {s["kind"]: s["body_md"] for s in payload["sections"]}


# ── 1. happy path: generated rows carry the body, owned rows untouched ──


def test_compose_writes_generated_sections_only(monkeypatch):
    body_ce_spune = "Sursa arată că relația părinte-copil contează (A1)."
    body_teza = "Teza centrală: reglarea emoțională se dezvoltă timpuriu."

    async def fake_sections(payload, page_samples, api_note=""):
        return {
            "ce_spune": body_ce_spune,
            "teza": body_teza,
            "fisa_sursei": "NU trebuie scris",
            "corectii": "NU trebuie scris",
        }

    monkeypatch.setattr("app.notes.compose._llm_sections", fake_sections)
    with TestClient(app) as client:
        _, payload = _verified_pair(client)
        dossier_id = payload["dossier"]["id"]
        res = client.post(f"/api/notes/dossiers/{dossier_id}/compose")
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["written"] == 2
        sections = _sections(data)
        assert sections["ce_spune"] == body_ce_spune
        assert sections["teza"] == body_teza
        # The source sheet is owned by the service and stays prefilled.
        assert "Titlu:" in sections["fisa_sursei"]
        assert "NU trebuie scris" not in sections["fisa_sursei"]
        # ``corectii`` is derived from claims, never generated.
        assert sections["corectii"] == ""


# ── 2. a section citing a non-existent code is dropped ──


def test_compose_drops_section_citing_unknown_code(monkeypatch):
    async def fake_sections(payload, page_samples, api_note=""):
        return {"teza": "Această secțiune citează A9, care nu există în dosar."}

    monkeypatch.setattr("app.notes.compose._llm_sections", fake_sections)
    with TestClient(app) as client:
        _, payload = _verified_pair(client)
        dossier_id = payload["dossier"]["id"]
        res = client.post(f"/api/notes/dossiers/{dossier_id}/compose")
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["written"] == 0
        assert data["rejected"] == 1
        assert "teza" not in data["sections"]
        assert _sections(data)["teza"] == ""


# ── 3. glossary: invented terms are dropped, real ones kept ──


def test_compose_glossary_drops_invented_lines(monkeypatch):
    real_line = "Relația părinte copil — legătura de atașament din primii ani."
    fake_line = "Zorblaxul cuantic — ficțiune totală fără acoperire."

    async def fake_sections(payload, page_samples, api_note=""):
        return {"glosar": f"{real_line}\n{fake_line}"}

    monkeypatch.setattr("app.notes.compose._llm_sections", fake_sections)
    with TestClient(app) as client:
        from app.db import get_db

        study = _study(client)
        conn = get_db()
        try:
            _insert_page(conn, study["id"], 1, f"Intro. {S1} Final.", printed="4")
        finally:
            conn.close()
        payload = _capture(client, study["id"], S1)
        dossier_id = payload["dossier"]["id"]
        res = client.post(f"/api/notes/dossiers/{dossier_id}/compose")
        assert res.status_code == 200, res.text
        data = res.json()
        body = _sections(data)["glosar"]
        assert "Relația părinte" in body
        assert "Zorblaxul" not in body
        assert data["rejected"] == 1


# ── 4. zero verified claims: nothing written, Romanian reason ──


def test_compose_without_verified_claims_writes_nothing(monkeypatch):
    async def fake_sections(payload, page_samples, api_note=""):
        raise AssertionError("the LLM must not be called without verified claims")

    monkeypatch.setattr("app.notes.compose._llm_sections", fake_sections)
    with TestClient(app) as client:
        study = _study(client)
        payload = _capture(client, study["id"], ABSENT)
        dossier_id = payload["dossier"]["id"]
        before = _sections(client.get(f"/api/notes/dossiers/{dossier_id}").json())
        res = client.post(f"/api/notes/dossiers/{dossier_id}/compose")
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["written"] == 0
        assert data["rejected"] == 0
        assert data["reason"] == (
            "Nu există afirmații verificate din care să compun secțiunile."
        )
        after = _sections(client.get(f"/api/notes/dossiers/{dossier_id}").json())
        assert after == before


# ── 5. exactly one journal round per compose run ──


def test_compose_appends_one_round(monkeypatch):
    async def fake_sections(payload, page_samples, api_note=""):
        return {"ce_spune": "Rezumat fără cod inventat."}

    monkeypatch.setattr("app.notes.compose._llm_sections", fake_sections)
    with TestClient(app) as client:
        from app.db import get_db

        _, payload = _verified_pair(client)
        dossier_id = payload["dossier"]["id"]
        conn = get_db()
        try:
            before = conn.execute(
                "SELECT COUNT(*) AS n FROM note_rounds WHERE dossier_id = ?",
                [dossier_id],
            ).fetchone()["n"]
        finally:
            conn.close()

        res = client.post(f"/api/notes/dossiers/{dossier_id}/compose")
        assert res.status_code == 200, res.text

        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT summary FROM note_rounds WHERE dossier_id = ? ORDER BY id",
                [dossier_id],
            ).fetchall()
        finally:
            conn.close()
        assert len(rows) == before + 1
        assert "Structură:" in rows[-1]["summary"]


# ── 6. unknown kinds from the model are ignored ──


def test_compose_ignores_unknown_kind(monkeypatch):
    async def fake_sections(payload, page_samples, api_note=""):
        return {
            "ce_spune": "O propoziție fără niciun cod.",
            "concluzii": "Acest tip nu există.",
        }

    monkeypatch.setattr("app.notes.compose._llm_sections", fake_sections)
    with TestClient(app) as client:
        _, payload = _verified_pair(client)
        dossier_id = payload["dossier"]["id"]
        res = client.post(f"/api/notes/dossiers/{dossier_id}/compose")
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["written"] == 1
        sections = _sections(data)
        assert "concluzii" not in sections
        assert sections["ce_spune"] == "O propoziție fără niciun cod."


# ── 7. endpoint is project-scoped and returns the refreshed payload ──


def test_compose_endpoint_is_project_scoped_and_returns_sections(monkeypatch):
    async def fake_sections(payload, page_samples, api_note=""):
        return {"ce_spune": "Rezumat verificat al sursei (A1)."}

    monkeypatch.setattr("app.notes.compose._llm_sections", fake_sections)
    with TestClient(app) as client:
        _, payload = _verified_pair(client)
        dossier_id = payload["dossier"]["id"]
        proj_a = client.get("/api/projects").json()["active_id"]

        created = client.post("/api/projects", json={"title": "Proiect B"})
        assert created.status_code == 201, created.text
        assert client.post(
            f"/api/notes/dossiers/{dossier_id}/compose"
        ).status_code == 404

        assert client.post(f"/api/projects/{proj_a}/activate").status_code == 200
        res = client.post(f"/api/notes/dossiers/{dossier_id}/compose")
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["dossier"]["id"] == dossier_id
        assert _sections(data)["ce_spune"] == "Rezumat verificat al sursei (A1)."


# ── 8. validation is a deterministic pure function ──


def test_validate_section_is_deterministic():
    from app.notes.compose import validate_section

    body = "Prima linie (A1).\nA doua linie citează A9.\nA treia linie."
    first = validate_section("teza", body, {"A1", "A2"}, "text sursă")
    second = validate_section("teza", body, {"A1", "A2"}, "text sursă")
    assert first == second
    assert first[0] == "Prima linie (A1).\nA treia linie."
    assert first[1] == ["A doua linie citează A9."]
