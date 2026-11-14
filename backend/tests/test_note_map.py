"""MAP layer for verified note dossiers: edges + project concepts."""
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
MOTIVE = "Ambele afirmații descriu același mecanism familial."


def _study(client, title="Map study"):
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
    """One dossier with claims A1 + A2, both `verificat` via exact match."""
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


def _mixed_pair(client):
    """One dossier with A1 `verificat` and A2 `neverificat`."""
    from app.db import get_db

    study = _study(client)
    conn = get_db()
    try:
        _insert_page(conn, study["id"], 1, f"Intro. {S1} Continuare.", printed="4")
    finally:
        conn.close()
    payload = _capture(client, study["id"], f"{S1} {ABSENT}")
    by_code = {c["code"]: c["status"] for c in payload["claims"]}
    assert by_code["A1"] == "verificat"
    assert by_code["A2"] == "neverificat"
    return study, payload


# ── 1. validate_edge rejects structural violations ──


def test_validate_edge_rejects_bad_proposals():
    from app.db import get_db
    from app.notes.map import validate_edge

    with TestClient(app) as client:
        _, payload = _verified_pair(client)
        dossier_id = payload["dossier"]["id"]
        conn = get_db()
        try:
            base = {"from_code": "A1", "to_code": "A2",
                    "motive": MOTIVE, "page": "4"}
            assert validate_edge(conn, dossier_id, {**base, "to_code": "AX"}) is None
            assert validate_edge(conn, dossier_id, {**base, "to_code": "A1"}) is None
            assert validate_edge(conn, dossier_id, {**base, "motive": "scurt"}) is None
            assert validate_edge(conn, dossier_id, {**base, "page": ""}) is None
            assert validate_edge(conn, dossier_id, {**base, "page": "  "}) is None
            ok = validate_edge(conn, dossier_id, base)
            assert ok is not None
            assert ok["claim_code"] == "A2"
            assert ok["page"] == "4"
        finally:
            conn.close()


# ── 2. generator keeps the valid edge, counts the invalid one ──


def test_generate_edges_keeps_only_valid(monkeypatch):
    async def fake_edges(payload, api_note=""):
        assert len(payload["claims"]) == 2
        return [
            {"from_code": "A1", "to_code": "A2", "motive": MOTIVE, "page": "4"},
            {"from_code": "A1", "to_code": "AX",
             "motive": "O legătură inventată fără niciun suport real.", "page": "9"},
        ]

    monkeypatch.setattr("app.notes.map._llm_edges", fake_edges)
    with TestClient(app) as client:
        _, payload = _verified_pair(client)
        dossier_id = payload["dossier"]["id"]
        res = client.post(f"/api/notes/dossiers/{dossier_id}/map", json={})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["accepted"] == 1
        assert body["rejected"] == 1
        assert len(body["edges"]) == 1
        edge = body["edges"][0]
        assert (edge["from_code"], edge["to_code"]) == ("A1", "A2")
        assert "muchii validate" in body["rounds"][0]["summary"]


# ── 3. edges citing a `neverificat` claim are dropped ──


def test_edges_citing_neverificat_claim_are_dropped(monkeypatch):
    async def fake_edges(payload, api_note=""):
        return [{"from_code": "A1", "to_code": "A2",
                 "motive": MOTIVE, "page": "4"}]

    monkeypatch.setattr("app.notes.map._llm_edges", fake_edges)
    with TestClient(app) as client:
        _, payload = _mixed_pair(client)
        dossier_id = payload["dossier"]["id"]
        res = client.post(f"/api/notes/dossiers/{dossier_id}/map", json={})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["accepted"] == 0
        assert body["rejected"] == 1
        assert body["edges"] == []


# ── 4. project_map bridges two documents; ghost concept is rejected ──


def test_project_map_bridges_two_documents(monkeypatch):
    from app.db import get_db

    async def fake_concepts(payload, api_note=""):
        dossiers = {d["dossier_id"]: d for d in payload["dossiers"]}
        first, second = sorted(dossiers)
        return [
            {"label": "Reglarea emoțională", "description": "Mecanism comun.",
             "links": [
                 {"dossier_id": first, "claim_code": "A1",
                  "note": "Arată legătura cu reglarea la preșcolari.", "page": "4"},
                 {"dossier_id": second, "claim_code": "A1",
                  "note": "Confirmă efectul asupra adaptării școlare.", "page": "4"},
             ]},
            {"label": "Concept fantomă", "description": "Fără sprijin.",
             "links": [{"dossier_id": 999999, "claim_code": "AX",
                        "note": "O notă suficient de lungă pentru test.", "page": "1"}]},
        ]

    monkeypatch.setattr("app.notes.map._llm_concepts", fake_concepts)
    with TestClient(app) as client:
        conn = get_db()
        try:
            s_a = _study(client, "Map doc A")["id"]
            s_b = _study(client, "Map doc B")["id"]
            _insert_page(conn, s_a, 1, f"Intro. {S1} Final.", printed="4")
            _insert_page(conn, s_b, 1, f"Intro. {S2} Final.", printed="4")
        finally:
            conn.close()
        dossier_a = _capture(client, s_a, S1)["dossier"]["id"]
        dossier_b = _capture(client, s_b, S2)["dossier"]["id"]

        res = client.post("/api/notes/project-map", json={})
        assert res.status_code == 200, res.text
        assert res.json()["rejected"] == 1

        pmap = client.get("/api/notes/project-map").json()
        assert pmap["stats"]["documents"] == 2
        assert pmap["stats"]["concepts"] == 1
        assert pmap["stats"]["bridged_concepts"] == 1
        assert pmap["stats"]["links"] == 2
        concept = pmap["concepts"][0]
        assert concept["code"] == "C1"
        assert concept["label"] == "Reglarea emoțională"
        assert sorted(pmap["bridges"][0]["dossier_ids"]) == sorted(
            [dossier_a, dossier_b]
        )
        assert pmap["bridges"][0]["concept_code"] == "C1"


# ── 5. concept codes are stable and never reused ──


def test_concept_codes_are_stable_and_never_reused(monkeypatch):
    async def fake_concepts(payload, api_note=""):
        dossier_id = payload["dossiers"][0]["dossier_id"]
        return [{"label": "Concept stabil", "description": "",
                 "links": [{"dossier_id": dossier_id, "claim_code": "A1",
                            "note": "Notă de sprijin suficient de lungă.", "page": "4"}]}]

    monkeypatch.setattr("app.notes.map._llm_concepts", fake_concepts)
    with TestClient(app) as client:
        _, payload = _verified_pair(client)
        first = client.post("/api/notes/project-map", json={})
        assert first.status_code == 200, first.text
        concept = first.json()["concepts"][0]
        assert concept["code"] == "C1"

        deleted = client.delete(f"/api/notes/concepts/{concept['id']}")
        assert deleted.status_code == 200, deleted.text

        second = client.post("/api/notes/project-map", json={})
        assert second.status_code == 200, second.text
        assert second.json()["concepts"][0]["code"] == "C2"


# ── 6. project isolation ──


def test_project_map_isolation(monkeypatch):
    from app.db import get_db

    async def fake_concepts(payload, api_note=""):
        dossier_id = payload["dossiers"][0]["dossier_id"]
        return [{"label": "Concept din B", "description": "",
                 "links": [{"dossier_id": dossier_id, "claim_code": "A1",
                            "note": "Notă de sprijin suficient de lungă.", "page": "4"}]}]

    monkeypatch.setattr("app.notes.map._llm_concepts", fake_concepts)
    with TestClient(app) as client:
        created_b = client.post("/api/projects", json={"title": "Harta B"})
        assert created_b.status_code == 201, created_b.text
        proj_b = created_b.json()["id"]

        conn = get_db()
        try:
            entry_b = _study(client, "Map isolation B")["id"]
            _insert_page(conn, entry_b, 1, f"Intro. {S1} Final.", printed="4")
        finally:
            conn.close()
        payload_b = _capture(client, entry_b, S1)
        generated = client.post("/api/notes/project-map", json={})
        assert generated.status_code == 200, generated.text
        assert len(generated.json()["concepts"]) == 1

        created_c = client.post("/api/projects", json={"title": "Harta C"})
        assert created_c.status_code == 201, created_c.text
        conn = get_db()
        try:
            entry_c = _study(client, "Map isolation C")["id"]
            _insert_page(conn, entry_c, 1, f"Intro. {S2} Final.", printed="4")
        finally:
            conn.close()
        payload_c = _capture(client, entry_c, S2)

        pmap_c = client.get("/api/notes/project-map").json()
        assert [d["dossier_id"] for d in pmap_c["documents"]] == [
            payload_c["dossier"]["id"]
        ]
        assert pmap_c["concepts"] == []
        assert pmap_c["bridges"] == []
        assert pmap_c["stats"]["bridged_concepts"] == 0

        assert client.post(f"/api/projects/{proj_b}/activate").status_code == 200
        pmap_b = client.get("/api/notes/project-map").json()
        assert [d["dossier_id"] for d in pmap_b["documents"]] == [
            payload_b["dossier"]["id"]
        ]
        assert len(pmap_b["concepts"]) == 1


# ── 7. manual edge endpoint validates motive and page ──


def test_manual_edge_endpoint_validates_motive():
    with TestClient(app) as client:
        _, payload = _verified_pair(client)
        dossier_id = payload["dossier"]["id"]

        bad = client.post(
            f"/api/notes/dossiers/{dossier_id}/edges",
            json={"from_code": "A1", "to_code": "A2",
                  "motive": "scurt", "page": "4"},
        )
        assert bad.status_code == 400, bad.text
        assert "motiv prea scurt" in bad.json()["detail"]

        no_page = client.post(
            f"/api/notes/dossiers/{dossier_id}/edges",
            json={"from_code": "A1", "to_code": "A2", "motive": MOTIVE, "page": ""},
        )
        assert no_page.status_code == 400, no_page.text
        assert "pagina lipsă" in no_page.json()["detail"]

        loop = client.post(
            f"/api/notes/dossiers/{dossier_id}/edges",
            json={"from_code": "A1", "to_code": "A1", "motive": MOTIVE, "page": "4"},
        )
        assert loop.status_code == 400, loop.text
        assert "muchie către sine" in loop.json()["detail"]

        ghost = client.post(
            f"/api/notes/dossiers/{dossier_id}/edges",
            json={"from_code": "A1", "to_code": "AX", "motive": MOTIVE, "page": "4"},
        )
        assert ghost.status_code == 400, ghost.text
        assert "cod inexistent" in ghost.json()["detail"]

        good = client.post(
            f"/api/notes/dossiers/{dossier_id}/edges",
            json={"from_code": "A1", "to_code": "A2",
                  "motive": MOTIVE, "page": "4"},
        )
        assert good.status_code == 200, good.text

        dossier = client.get(f"/api/notes/dossiers/{dossier_id}").json()
        assert any(
            e["from_code"] == "A1" and e["to_code"] == "A2"
            for e in dossier["edges"]
        )
