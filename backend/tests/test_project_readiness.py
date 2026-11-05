"""Readiness and ideas: the app must say what the thesis is missing.

The readiness panel is deterministic on purpose — it answers from the real
library, so it is useful with no LLM and no network. The tests pin that it
names the missing pieces and that its score moves when they are filled.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setenv("CHRONOKG_DB_PATH", str(db))
    import app.db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", db)
    with TestClient(app) as c:
        yield c


def _areas(body):
    return {i["area"]: i for i in body["items"]}


def test_readiness_names_the_missing_pieces(client):
    client.put("/api/project", json={"title": "Adverse experiences and resilience in youth"})
    body = client.get("/api/project/readiness").json()

    areas = _areas(body)
    # Everything the research plan needs is named, and empty on a new project.
    for area in ("Participanți", "Instrumente", "Ipoteze", "Variabile",
                 "Analiză", "Etică", "Calendar", "Riscuri"):
        assert area in areas, f"{area} must be checked"
        assert areas[area]["state"] == "missing"
        assert areas[area]["missing"], "a missing piece must say what it lacks"

    summary = body["summary"]
    assert summary["missing"] >= 8
    assert summary["score"] < 60
    assert summary["next"] is not None


def test_filling_the_plan_raises_the_score(client):
    before = client.get("/api/project/readiness").json()["summary"]["score"]

    client.put("/api/project", json={
        "title": "Adverse experiences and resilience in youth",
        "plan": {
            "participants": "120 de adolescenți, 14-18 ani, două grupe",
            "instruments": "Chestionar ACE, scala de reziliență CD-RISC",
            "hypotheses": "Adversitatea prezice reziliență mai mică",
            "variables": "IV: numărul de experiențe adverse; DV: scorul de reziliență",
            "analysis": "Regresie liniară multiplă, alfa 0.05",
            "ethics": "Consimțământ informat, aprobare de la comisia de etică",
            "schedule": "Noiembrie: colectare; decembrie: analiză; ianuarie: redactare",
            "risks": "Eșantion mic; completare online pentru a reduce costul",
        },
    })

    after = client.get("/api/project/readiness").json()
    assert after["summary"]["score"] > before
    areas = _areas(after)
    for area in ("Participanți", "Instrumente", "Ipoteze", "Variabile",
                 "Analiză", "Etică", "Calendar", "Riscuri"):
        assert areas[area]["state"] == "ready", f"{area} should be ready now"


def test_readiness_counts_verifiable_sources_honestly(client):
    """A study with neither DOI nor PDF is not a citable source."""
    client.put("/api/project", json={"title": "Memory consolidation during sleep"})
    client.post("/api/entries", json={
        "title": "Sleep and memory", "content": "x", "study_type": "study", "tags": "study",
    })
    body = client.get("/api/project/readiness").json()
    sources = _areas(body)["Surse verificabile"]
    assert sources["state"] == "missing"
    assert "niciun DOI" in sources["have"]

    client.post("/api/entries", json={
        "title": "Sleep and memory II", "content": "y", "study_type": "study",
        "tags": "study", "doi": "10.1038/nrn.2016.44",
    })
    body = client.get("/api/project/readiness").json()
    assert _areas(body)["Surse verificabile"]["state"] in ("partial", "ready")


def test_schema_shows_the_real_question_and_pillars(client):
    client.put("/api/project", json={
        "title": "Mindfulness and brain structure",
        "research_q": "What structural changes does meditation produce?",
    })
    client.post("/api/entries", json={
        "title": "Meditation and cortical thickness", "content": "text " * 60,
        "study_type": "study", "tags": "study,neuroscience,meditation",
    })
    body = client.get("/api/project/schema").json()
    assert body["question"].startswith("What structural")
    assert any(p["tag"] == "neuroscience" for p in body["pillars"])
    assert body["top_studies"], "the schema must carry the ranked studies"
    assert "independent" in body["variables"]


def test_ideas_round_trip(client):
    created = client.post("/api/project/ideas", json={
        "text": "Verifică dacă hipocampul mediază relația", "category": "intrebare",
    })
    assert created.status_code == 201
    idea = created.json()
    assert idea["category"] == "intrebare"

    listed = client.get("/api/project/ideas").json()
    assert any(i["id"] == idea["id"] for i in listed)

    assert client.delete(f"/api/project/ideas/{idea['id']}").status_code == 204
    assert not any(i["id"] == idea["id"] for i in client.get("/api/project/ideas").json())


def test_unknown_idea_category_falls_back_to_general(client):
    body = client.post("/api/project/ideas",
                       json={"text": "o idee", "category": "inexistent"}).json()
    assert body["category"] == "general"


def test_deleting_a_missing_idea_is_a_404(client):
    assert client.delete("/api/project/ideas/999999").status_code == 404


def test_empty_idea_is_refused(client):
    assert client.post("/api/project/ideas", json={"text": ""}).status_code == 422


def test_ideas_can_be_filtered_by_category(client):
    client.post("/api/project/ideas", json={"text": "una metodică", "category": "metoda"})
    client.post("/api/project/ideas", json={"text": "una teoretică", "category": "teorie"})
    only = client.get("/api/project/ideas?category=metoda").json()
    assert len(only) == 1
    assert only[0]["text"] == "una metodică"
