"""Vector-dimension honesty: mismatched embeddings must not fail or lie.

The live store still holds 768-dim vectors from the Ollama era while llama.cpp
serves bge-m3 (1024 dims). `search_documents` compared lengths and dropped
every stored vector in silence, returning [] while /api/ai/status kept saying
semantic retrieval worked. These tests pin the honest behaviour: `vector_stats`
counts what is really stored, the mismatch is logged once per process, and the
status endpoint says when zero vectors can be compared.
"""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app import embeddings
from app import pdf_rag
from app.db import get_db, init_db
from app.main import app


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """Use a private DB file: concurrent pytest runs share one temp path.

    `conftest.py` pins every run to the same `/tmp/chronokg_pytest.db`, so a
    run that wipes the file mid-test turns into "no such table".
    """
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "rag_dimensions.db")
    yield


@pytest.fixture(autouse=True)
def offline_probes(monkeypatch):
    """No test may dial llama.cpp or Ollama on the developer's machine."""
    monkeypatch.setattr(embeddings, "_llama_checked", True)
    monkeypatch.setattr(embeddings, "_llama_ok", False)
    monkeypatch.setattr(embeddings, "_dimension", None)
    monkeypatch.setattr(embeddings, "_tags", [])
    monkeypatch.setattr(embeddings, "_active_model", None)
    monkeypatch.setattr(embeddings, "_cooldown_until", 0.0)
    monkeypatch.setattr(embeddings, "_ollama_tags", lambda: [])
    monkeypatch.setattr(pdf_rag, "_dimension_warning_logged", False)
    yield


def _seed_vectors(vectors: list[list[float] | str]) -> int:
    """Store one entry with one chunk per given embedding payload."""
    init_db()
    conn = get_db()
    try:
        entry_id = conn.execute(
            "INSERT INTO entries (title) VALUES ('A paper')"
        ).lastrowid
        for index, vec in enumerate(vectors):
            raw = vec if isinstance(vec, str) else json.dumps(vec)
            conn.execute(
                "INSERT INTO documents (entry_id, chunk_index, content, embedding) "
                "VALUES (?, ?, ?, ?)",
                [entry_id, index, f"chunk {index} about cognition", raw],
            )
        conn.commit()
        return entry_id
    finally:
        conn.close()


def test_search_documents_survives_mismatched_dimensions(monkeypatch, caplog):
    """768-dim chunks + 1024-dim query: no crash, no results, but a warning."""
    _seed_vectors([[0.1] * 768])
    monkeypatch.setattr(embeddings, "_llama_ok", True)
    monkeypatch.setattr(embeddings, "_dimension", 1024)
    monkeypatch.setattr(pdf_rag, "embedding_available", lambda: True)
    monkeypatch.setattr(pdf_rag, "embed", lambda texts: [[0.2] * 1024 for _ in texts])

    with caplog.at_level("WARNING", logger="chronokg.pdf_rag"):
        assert asyncio.run(pdf_rag.search_documents("anything")) == []
        assert asyncio.run(pdf_rag.search_documents("anything again")) == []

    warnings = [r for r in caplog.records if "reembed_documents.py" in r.getMessage()]
    assert len(warnings) == 1, "the mismatch must be reported once per process, not per search"
    message = warnings[0].getMessage()
    assert "0 of 1 document vectors" in message
    assert "1024d" in message
    assert "768d: 1" in message


def test_vector_stats_counts_stored_dimensions(monkeypatch):
    _seed_vectors([[0.1] * 1024, [0.2] * 1024, [0.3] * 768, "", "not json"])
    monkeypatch.setattr(pdf_rag, "embedding_dimension", lambda: 1024)

    stats = pdf_rag.vector_stats()

    assert stats["chunks"] == 5
    assert stats["dims"] == {0: 2, 768: 1, 1024: 2}
    assert stats["active_dim"] == 1024
    assert stats["matchable"] == 2


def test_vector_stats_reports_zero_matchable_without_active_backend(monkeypatch):
    _seed_vectors([[0.3] * 768])
    monkeypatch.setattr(pdf_rag, "embedding_dimension", lambda: None)

    stats = pdf_rag.vector_stats()

    assert stats["active_dim"] is None
    assert stats["matchable"] == 0


def test_ai_status_admits_semantic_search_is_broken(monkeypatch):
    """A live backend plus 768-dim vectors must not read as 'all good'."""
    _seed_vectors([[0.1] * 768])
    monkeypatch.setattr(embeddings, "_llama_ok", True)
    monkeypatch.setattr(embeddings, "_dimension", 1024)

    with TestClient(app) as client:
        status = client.get("/api/ai/status").json()

    assert status["semantic_retrieval"] is True, "the backend does answer"
    assert status["embedding_dimension"] == 1024
    assert status["pdf_chunks_indexed"] == 1
    assert status["pdf_vector_dims"] == {"768": 1}
    assert status["pdf_chunks_matchable"] == 0
    assert status["semantic_search_effective"] is False


def test_ai_status_says_effective_when_vectors_match(monkeypatch):
    """The new flag follows the data instead of being hardcoded."""
    _seed_vectors([[0.1] * 1024])
    monkeypatch.setattr(embeddings, "_llama_ok", True)
    monkeypatch.setattr(embeddings, "_dimension", 1024)

    with TestClient(app) as client:
        status = client.get("/api/ai/status").json()

    assert status["pdf_chunks_matchable"] == 1
    assert status["semantic_search_effective"] is True
