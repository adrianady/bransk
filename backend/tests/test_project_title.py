"""The title is analysed, not just counted.

Before this there was one check — "under four words is too short" — which said
nothing about whether the title announces the study. The analysis has two
layers: measurements that run with no model at all, and an optional written
reading. The tests pin both, and pin that a missing title never crashes.
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


def _levels(body):
    return [c["level"] for c in body["checks"]]


def test_no_title_is_reported_not_crashed(client):
    body = client.get("/api/project/title").json()
    assert body["title"] == ""
    assert "critical" in _levels(body)
    assert body["score"] == 0


def test_a_strong_title_scores_well(client):
    client.put("/api/project", json={
        "title": "Reziliența după adversitate la adolescenți: un studiu longitudinal",
        "research_q": "Cum prezice adversitatea reziliența la adolescenți?",
    })
    body = client.get("/api/project/title").json()
    assert body["stats"]["words"] >= 5
    assert body["stats"]["population"], "adolescenți must be recognised as a population"
    assert body["score"] >= 70
    assert not [c for c in body["checks"] if c["level"] == "critical"]


def test_a_vague_title_is_named_as_vague(client):
    client.put("/api/project", json={
        "title": "Aspecte și considerații privind problematica rezilienței",
    })
    body = client.get("/api/project/title").json()
    vague = set(body["stats"]["vague"])
    assert {"aspecte", "considerații", "problematica"} <= vague
    messages = " ".join(c["message"] for c in body["checks"])
    assert "nu spun nimic concret" in messages


def test_a_title_without_population_says_so(client):
    client.put("/api/project", json={"title": "Modificări structurale cerebrale produse de meditație"})
    body = client.get("/api/project/title").json()
    assert body["stats"]["population"] == []
    messages = " ".join(c["message"] for c in body["checks"])
    assert "pe cine studiezi" in messages


def test_title_and_question_far_apart_is_flagged(client):
    """The most common advisor remark: the title promises a different study."""
    client.put("/api/project", json={
        "title": "Reziliența după adversitate la adolescenți",
        "research_q": "Ce modificări structurale cerebrale produce meditația?",
    })
    body = client.get("/api/project/title").json()
    messages = " ".join(c["message"] for c in body["checks"])
    assert "lucruri diferite" in messages


def test_title_and_question_close_together_passes(client):
    client.put("/api/project", json={
        "title": "Reziliența adolescenților după experiențe adverse",
        "research_q": "Cum se leagă experiențele adverse de reziliența adolescenților?",
    })
    body = client.get("/api/project/title").json()
    messages = " ".join(c["message"] for c in body["checks"])
    assert "se leagă de întrebarea de cercetare" in messages


def test_missing_research_question_is_flagged(client):
    client.put("/api/project", json={"title": "Reziliența la adolescenți după adversitate"})
    body = client.get("/api/project/title").json()
    messages = " ".join(c["message"] for c in body["checks"])
    assert "nu pot verifica dacă titlul o acoperă" in messages


def test_measurements_need_no_model(client, monkeypatch):
    """GET must never call the LLM: the panel is useful offline."""
    import app.routers.project as project

    calls = []

    async def fake_llm(messages, **kwargs):
        calls.append(messages)
        return "text"

    monkeypatch.setattr(project, "_llm", fake_llm)
    monkeypatch.setattr(project, "LLM_PROVIDER", "deepseek")
    client.put("/api/project", json={"title": "Reziliența la adolescenți după adversitate"})

    body = client.get("/api/project/title").json()
    assert calls == []
    assert body["review"] is None
    assert body["checks"], "the deterministic part must stand on its own"


def test_the_written_reading_is_returned_when_available(client, monkeypatch):
    import app.routers.project as project

    async def fake_llm(messages, **kwargs):
        return "**Ce promite titlul** — ceva.\n**Trei variante** — 1. a 2. b 3. c"

    monkeypatch.setattr(project, "_llm", fake_llm)
    monkeypatch.setattr(project, "LLM_PROVIDER", "deepseek")
    client.put("/api/project", json={"title": "Reziliența la adolescenți după adversitate"})

    body = client.post("/api/project/title").json()
    assert body["from_llm"] is True
    assert "Ce promite titlul" in body["review"]
    assert body["checks"], "the measurements come with the reading, not instead of it"


def test_a_reasoning_model_out_of_tokens_does_not_crash(client, monkeypatch):
    """An empty reply must degrade to the measurements, not to an error."""
    import app.routers.project as project

    async def empty_llm(messages, **kwargs):
        return None

    monkeypatch.setattr(project, "_llm", empty_llm)
    monkeypatch.setattr(project, "LLM_PROVIDER", "deepseek")
    client.put("/api/project", json={"title": "Reziliența la adolescenți după adversitate"})

    res = client.post("/api/project/title")
    assert res.status_code == 200
    body = res.json()
    assert body["from_llm"] is False
    assert body["checks"]
