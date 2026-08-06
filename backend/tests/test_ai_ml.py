"""AI/ML integration tests: observability, embeddings fallback, JSON repair."""
import json

from fastapi.testclient import TestClient

from app.main import app
from app import embeddings
from app.routers import research as research_mod


def test_ai_events_logged_after_chat_stream():
    with TestClient(app) as client:
        resp = client.post("/api/chat/stream", json={"message": "Hello", "history": []})
        assert resp.status_code == 200
        events = client.get("/api/ai/events").json()
        assert isinstance(events, list) and len(events) >= 1
        assert events[0]["ok"] == 1
        assert events[0]["provider"] == "mock"


def test_ai_status_endpoint_reports_config():
    with TestClient(app) as client:
        status = client.get("/api/ai/status").json()
        assert status["provider_mode"] == "mock"
        assert "configured_embed_model" in status
        assert "active_embed_model" in status
        assert isinstance(status["semantic_retrieval"], bool)


def test_rerank_falls_back_to_original_order_when_embeddings_disabled(monkeypatch):
    # Force the "unavailable" cooldown window (the modern equivalent of the old
    # permanent _disabled latch) so rerank falls back to the original order.
    import time as _time

    monkeypatch.setattr(embeddings, "_cooldown_until", _time.monotonic() + 1000)
    candidates = [
        {"id": 1, "title": "Physics", "content": "about atoms"},
        {"id": 2, "title": "Cooking", "content": "recipes"},
    ]
    assert embeddings.rerank("physics", candidates) == candidates


def test_cosine_similarity():
    assert embeddings.cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert embeddings.cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert embeddings.cosine([], []) == 0.0


def test_llm_json_repairs_malformed_first_reply(monkeypatch):
    """A prose-wrapped first reply must trigger the repair pass and still parse."""
    monkeypatch.setattr(research_mod, "LLM_PROVIDER", "ollama")
    calls = []

    async def fake_complete(messages, **kwargs):
        calls.append(kwargs.get("temperature"))
        if len(calls) == 1:
            return ("ollama", "qwen", "Here you go: {broken json")
        # Repair pass returns valid JSON
        payload = {"research_question": "Q", "sub_questions": ["a", "b"]}
        return ("ollama", "qwen", json.dumps(payload))

    monkeypatch.setattr(research_mod, "llm_complete", fake_complete)

    result = __import__("asyncio").run(
        research_mod._llm_json("PLAN system", "Topic: sleep")
    )
    assert result["research_question"] == "Q"
    assert len(calls) == 2
    assert calls[1] == 0.0  # repair pass runs at temperature 0


def test_context_recall_offline():
    """FTS5 path must surface the matching entry (recall@10 baseline)."""
    with TestClient(app) as client:
        client.post(
            "/api/entries",
            json={"title": "Quantum entanglement experiment", "content": "Bell inequalities violation measurements", "tags": "physics"},
        )
        client.post(
            "/api/entries",
            json={"title": "Cooking pasta", "content": "Boil water add salt", "tags": "food"},
        )
    from app.routers.chat import _build_context

    ctx = _build_context("Bell inequalities violation")
    assert ctx
    assert ctx[0]["id"] == 1


def test_pdf_chunking_respects_size_and_overlap():
    from app.pdf_rag import chunk_text

    text = " ".join(["word"] * 120)
    chunks = chunk_text(text, size=50, overlap=10)
    assert len(chunks) > 1
    assert all(len(c) <= 50 for c in chunks)
    assert all(c for c in chunks)  # no empty chunks


def test_search_documents_returns_empty_without_embeddings(monkeypatch):
    import asyncio

    from app.pdf_rag import search_documents

    monkeypatch.setattr("app.pdf_rag.embedding_available", lambda: False)
    assert asyncio.run(search_documents("anything")) == []
