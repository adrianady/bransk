"""The analysis must be cached, not re-billed on every open.

Before this, GET /api/project/analysis awaited the LLM each time the tab was
opened: 4–7 seconds and real money per visit. The tests pin the new contract:
GET reads storage and never calls a model, POST generates once and stores it,
and a changed title invalidates what was stored.
"""
import sqlite3

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


def _seed_project(client, title="Adverse childhood experiences and resilience"):
    client.put("/api/project", json={
        "title": title,
        "research_q": "How does perceived context shape resilience after adversity?",
        "work_type": "disertatie",
    })
    client.post("/api/entries", json={
        "title": "ACEs and resilience in adolescents",
        "content": "A longitudinal study of adversity and later adaptation. " * 8,
        "study_type": "study",
        "tags": "study,resilience",
        "entry_date": "2020-01-01",
    })


def test_get_analysis_does_not_call_the_model(client, monkeypatch):
    """A GET must answer from storage even when the model is available."""
    calls = []

    async def fake_llm(messages, **kwargs):
        calls.append(messages)
        return "generated"

    import app.routers.project as project
    monkeypatch.setattr(project, "_llm", fake_llm)
    monkeypatch.setattr(project, "LLM_PROVIDER", "deepseek")
    _seed_project(client)

    res = client.get("/api/project/analysis")
    assert res.status_code == 200
    assert calls == [], "GET must not call the LLM"
    assert res.json()["needs_generation"] is True


def test_post_analysis_generates_once_and_get_reuses_it(client, monkeypatch):
    calls = []

    async def fake_llm(messages, **kwargs):
        calls.append(messages)
        return "**Unde ești acum** — text generat."   # noqa: RUF001 (Romanian text)

    import app.routers.project as project
    monkeypatch.setattr(project, "_llm", fake_llm)
    monkeypatch.setattr(project, "LLM_PROVIDER", "deepseek")
    _seed_project(client)

    first = client.post("/api/project/analysis").json()
    assert first["from_llm"] is True
    assert first["generated_at"]
    assert len(calls) == 1

    # Three opens later the model was still called only once.
    for _ in range(3):
        body = client.get("/api/project/analysis").json()
        assert body["cached"] is True
        assert body["stale"] is False
        assert body["analysis"] == first["analysis"]
        assert body["generated_at"] == first["generated_at"]
    assert len(calls) == 1, "opening the tab must not call the LLM again"


def test_changing_the_title_marks_the_analysis_stale(client, monkeypatch):
    async def fake_llm(messages, **kwargs):
        return "analiză veche"

    import app.routers.project as project
    monkeypatch.setattr(project, "_llm", fake_llm)
    monkeypatch.setattr(project, "LLM_PROVIDER", "deepseek")
    _seed_project(client)
    client.post("/api/project/analysis")
    assert client.get("/api/project/analysis").json()["stale"] is False

    client.put("/api/project", json={"title": "A completely different topic about memory"})

    body = client.get("/api/project/analysis").json()
    # The stored text still answers the old question: it stays readable, and
    # the fingerprint says it no longer matches the project.
    assert body["stale"] is True
    assert body["needs_generation"] is True
    assert body["analysis"] == "analiză veche", "paid-for text must not vanish"


def test_analysis_survives_a_restart(tmp_path, monkeypatch):
    """The cache lives in the database, not in memory."""
    db = tmp_path / "persist.db"
    monkeypatch.setenv("CHRONOKG_DB_PATH", str(db))
    import app.db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", db)

    async def fake_llm(messages, **kwargs):
        return "text păstrat"

    import app.routers.project as project
    monkeypatch.setattr(project, "_llm", fake_llm)
    monkeypatch.setattr(project, "LLM_PROVIDER", "deepseek")

    with TestClient(app) as c:
        _seed_project(c)
        c.post("/api/project/analysis")

    # A fresh client, as after restarting the app.
    with TestClient(app) as c:
        body = c.get("/api/project/analysis").json()
        assert body["analysis"] == "text păstrat"
        assert body["cached"] is True


def test_no_llm_configured_says_so_instead_of_guessing(client, monkeypatch):
    import app.routers.project as project
    monkeypatch.setattr(project, "LLM_PROVIDER", "mock")
    _seed_project(client)

    body = client.get("/api/project/analysis").json()
    assert body["from_llm"] is False
    # The deterministic numbers are still there — the tab is never empty.
    assert body["stats"]["library_studies"] >= 1
