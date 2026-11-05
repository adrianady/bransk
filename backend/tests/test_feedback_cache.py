"""Feedback must never call the LLM on a read.

Before this, GET /api/project/feedback awaited the model every time the tab was
opened (measured 11.7 s and 11.4 s, one extra ai_events row per visit), and the
client opens that tab by itself. The contract pinned here:

  * GET answers from the deterministic checks alone — zero model calls;
  * POST /api/project/feedback/review generates once and stores the text;
  * a second identical POST is served from the database in milliseconds;
  * editing the project invalidates the stored review.
"""
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "feedback.db"
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
        "methodology": "Longitudinal cohort",
    })


def test_get_feedback_never_calls_the_model(client, monkeypatch):
    """A GET must answer even when the model is available — and not touch it."""

    def explode(*_args, **_kwargs):
        raise AssertionError("GET /project/feedback must not call the LLM")

    import app.routers.project as project
    monkeypatch.setattr(project, "_llm", explode)
    monkeypatch.setattr(project, "LLM_PROVIDER", "deepseek")
    _seed_project(client)

    res = client.get("/api/project/feedback")
    assert res.status_code == 200
    body = res.json()
    assert body["items"], "the rule-based checks must still be there"
    assert body["reviewer"] is None
    assert body["from_llm"] is False
    assert body["generated_at"] is None
    assert body["cached"] is False
    assert body["needs_generation"] is True


def test_post_review_generates_once_then_serves_the_cache(client, monkeypatch):
    calls = []

    async def fake_llm(messages, **kwargs):
        calls.append(messages)
        return "**Puncte tari** — titlul e clar."   # noqa: RUF001 (Romanian text)

    import app.routers.project as project
    monkeypatch.setattr(project, "_llm", fake_llm)
    monkeypatch.setattr(project, "LLM_PROVIDER", "deepseek")
    _seed_project(client)

    first = client.post("/api/project/feedback/review").json()
    assert len(calls) == 1, "the first POST is the one that pays"
    assert first["reviewer"].startswith("**Puncte tari**")
    assert first["from_llm"] is True
    assert first["cached"] is False
    assert first["needs_generation"] is False
    assert first["generated_at"]

    started = time.perf_counter()
    second = client.post("/api/project/feedback/review").json()
    elapsed = time.perf_counter() - started

    assert len(calls) == 1, "a second identical POST must come from the cache"
    assert second["cached"] is True
    assert second["reviewer"] == first["reviewer"]
    assert second["generated_at"] == first["generated_at"]
    assert second["items"] == first["items"]
    assert elapsed < 0.2, f"cached review took {elapsed * 1000:.0f} ms"


def test_get_after_a_review_still_makes_no_call(client, monkeypatch):
    """GET keeps its own contract: structural items only, never the model."""
    calls = []

    async def fake_llm(messages, **kwargs):
        calls.append(messages)
        return "text"

    import app.routers.project as project
    monkeypatch.setattr(project, "_llm", fake_llm)
    monkeypatch.setattr(project, "LLM_PROVIDER", "deepseek")
    _seed_project(client)

    client.post("/api/project/feedback/review")
    body = client.get("/api/project/feedback").json()
    assert len(calls) == 1
    assert body["reviewer"] is None
    assert body["needs_generation"] is True


def test_editing_the_project_invalidates_the_cached_review(client, monkeypatch):
    calls = []

    async def fake_llm(messages, **kwargs):
        calls.append(messages)
        return f"review {len(calls)}"

    import app.routers.project as project
    monkeypatch.setattr(project, "_llm", fake_llm)
    monkeypatch.setattr(project, "LLM_PROVIDER", "deepseek")
    _seed_project(client)

    assert client.post("/api/project/feedback/review").json()["reviewer"] == "review 1"

    client.put("/api/project", json={"title": "A completely different topic about memory"})
    refreshed = client.post("/api/project/feedback/review").json()
    assert len(calls) == 2, "a changed project must regenerate, not reuse the old text"
    assert refreshed["reviewer"] == "review 2"
    assert refreshed["cached"] is False


def test_review_without_a_model_says_so_instead_of_pretending(client, monkeypatch):
    import app.routers.project as project
    monkeypatch.setattr(project, "LLM_PROVIDER", "mock")
    _seed_project(client)

    body = client.post("/api/project/feedback/review").json()
    assert body["reviewer"] is None
    assert body["from_llm"] is False
    assert body["needs_generation"] is True
    assert body["items"], "the deterministic checks are still returned"
