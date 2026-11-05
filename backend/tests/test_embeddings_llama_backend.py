"""Semantic retrieval can run on llama.cpp, without Ollama.

Ollama is not installed on this machine, but llama.cpp is. Its server speaks
the OpenAI shape at /v1/embeddings, so one small GGUF model from Hugging Face
is enough. These tests pin that the llama.cpp path is tried first, that a
missing server degrades to FTS5 instead of failing, and that the reported
backend tells the truth about which one answered.
"""
import pytest

from app import embeddings


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    """Each test starts from a clean module state."""
    monkeypatch.setattr(embeddings, "_llama_checked", False)
    monkeypatch.setattr(embeddings, "_llama_ok", False)
    monkeypatch.setattr(embeddings, "_dimension", None)
    monkeypatch.setattr(embeddings, "_cooldown_until", 0.0)
    yield


def test_llama_is_used_before_ollama(monkeypatch):
    """When llama.cpp answers, no Ollama request is made."""
    calls = []

    def fake_post(self, url, json=None, **kw):
        calls.append(url)

        class R:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"data": [{"embedding": [0.1, 0.2, 0.3]}]}

        return R()

    monkeypatch.setattr(embeddings.httpx.Client, "post", fake_post)
    monkeypatch.setattr(embeddings, "_llama_available", lambda: True)

    vectors = embeddings._batch_embed(["un text"])
    assert vectors == [[0.1, 0.2, 0.3]]
    assert calls and "/v1/embeddings" in calls[0]
    assert not any("/api/embed" in c for c in calls), "Ollama must not be called"


def test_missing_llama_server_reports_unavailable(monkeypatch):
    """No server: the module says so instead of raising."""
    monkeypatch.setattr(embeddings, "_llama_available", lambda: False)
    monkeypatch.setattr(embeddings, "_resolve_model", lambda: None)
    assert embeddings.embedding_available() is False
    assert embeddings.embedding_backend() == "none"


def test_backend_name_is_honest(monkeypatch):
    monkeypatch.setattr(embeddings, "_llama_available", lambda: True)
    assert embeddings.embedding_backend().startswith("llama.cpp")
    assert embeddings.embedding_model() == embeddings.LLAMA_EMBED_MODEL

    monkeypatch.setattr(embeddings, "_llama_available", lambda: False)
    monkeypatch.setattr(embeddings, "_resolve_model", lambda: "nomic-embed-text")
    monkeypatch.setattr(embeddings, "_active_model", "nomic-embed-text")
    assert embeddings.embedding_backend() == "ollama"


def test_a_broken_llama_call_falls_through_to_fts5(monkeypatch):
    """If llama.cpp errors, retrieval degrades — it never explodes."""

    def boom(self, *a, **kw):
        raise RuntimeError("server died")

    monkeypatch.setattr(embeddings.httpx.Client, "post", boom)
    monkeypatch.setattr(embeddings, "_llama_available", lambda: True)
    monkeypatch.setattr(embeddings, "_resolve_model", lambda: None)

    assert embeddings._batch_embed(["x"]) is None


def test_default_embedding_model_is_multilingual():
    """The topic is Romanian over English papers: a monolingual model is wrong."""
    assert "bge-m3" in embeddings.LLAMA_EMBED_MODEL
