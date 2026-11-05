import time
import asyncio
"""
Tests for the service modules behind the routers.

Coverage on 2026-10-25 was lowest exactly here: llm.py 45%, web_search.py 43%,
embeddings.py 56%, pdf_rag.py 61%, study_qa.py 69%. These are the pieces the
routers trust blindly — an SSRF guard, an FTS-independent text chunker, a
provider failover and the parser that turns DuckDuckGo HTML into study sources.
None of them had a test of their own logic.
"""
import json
from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.db as db_module

    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "services_audit.db")
    db_module.init_db()
    with TestClient(app) as c:
        yield c


# ─────────────────── studies_store: SSRF guard ───────────────────

def test_download_url_rejects_private_and_local_hosts(monkeypatch):
    from app.studies_store import assert_public_download_url

    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda host, *a, **k: [(2, 1, 6, "", ("127.0.0.1", 0))],
    )
    with pytest.raises(ValueError, match="non-public"):
        assert_public_download_url("http://evil.example.com/paper.pdf")

    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda host, *a, **k: [(2, 1, 6, "", ("192.168.1.5", 0))],
    )
    with pytest.raises(ValueError, match="non-public"):
        assert_public_download_url("http://192.168.1.5/paper.pdf")

    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda host, *a, **k: [(2, 1, 6, "", ("169.254.169.254", 0))],
    )
    with pytest.raises(ValueError, match="non-public"):
        assert_public_download_url("http://metadata.example.com/latest")


def test_download_url_rejects_bad_schemes_and_ports():
    from app.studies_store import assert_public_download_url

    for url in ("file:///etc/passwd", "ftp://x.org/a.pdf", "gopher://x.org/1"):
        with pytest.raises(ValueError, match="scheme"):
            assert_public_download_url(url)
    with pytest.raises(ValueError, match="port"):
        assert_public_download_url("http://public.example.com:8080/a.pdf")


def test_download_url_rejects_empty_host():
    from app.studies_store import assert_public_download_url

    with pytest.raises(ValueError, match="no host"):
        assert_public_download_url("http:///a.pdf")


def test_download_url_accepts_a_resolving_public_host(monkeypatch):
    from app.studies_store import assert_public_download_url

    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda host, *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))],
    )
    assert_public_download_url("https://public.example.com/paper.pdf")  # must not raise


def test_download_url_rejects_a_host_that_does_not_resolve(monkeypatch):
    import socket

    from app.studies_store import assert_public_download_url

    def boom(host, *a, **k):
        raise socket.gaierror("nope")

    monkeypatch.setattr("socket.getaddrinfo", boom)
    with pytest.raises(ValueError, match="does not resolve"):
        assert_public_download_url("http://nx.example.com/a.pdf")


def test_pdf_magic_bytes_validation(tmp_path):
    from app.studies_store import is_valid_pdf_file

    good = tmp_path / "good.pdf"
    good.write_bytes(b"%PDF-1.7\nrest")
    assert is_valid_pdf_file(good) is True

    html = tmp_path / "blocked.pdf"
    html.write_bytes(b"<!DOCTYPE html><html>Blocked</html>")
    assert is_valid_pdf_file(html) is False, "an HTML error page must never pass as a PDF"

    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    assert is_valid_pdf_file(empty) is False
    assert is_valid_pdf_file(tmp_path / "missing.pdf") is False


def test_pdf_filename_is_slugged_and_bounded():
    from app.studies_store import pdf_filename, _slugify

    assert pdf_filename(42, "Mindfulness & the Brain: A Review!") == "42-mindfulness-the-brain-a-review.pdf"
    assert _slugify("") == "study"
    assert _slugify("   ") == "study"
    assert len(_slugify("x" * 200)) == 60
    assert ".." not in pdf_filename(1, "../../etc/passwd")


def test_pdf_url_extraction_prefers_source_then_content():
    from app.studies_store import extract_pdf_url

    assert extract_pdf_url({"source": "https://a.org/x.pdf", "content": "y"}) == "https://a.org/x.pdf"
    assert extract_pdf_url({"source": "", "content": "see https://b.org/paper.pdf now"}) == "https://b.org/paper.pdf"
    assert extract_pdf_url({"source": "  ", "content": "no link"}) == ""


def test_download_refuses_internal_host_without_touching_the_network(monkeypatch):
    asyncio.run(_test_download_refuses_internal_host_without_touching_the_network_async(monkeypatch))


async def _test_download_refuses_internal_host_without_touching_the_network_async(monkeypatch):
    """The guard runs before any HTTP call: nothing is written, no request made."""
    from app.studies_store import download_study_pdf

    called = []
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda host, *a, **k: [(2, 1, 6, "", ("127.0.0.1", 0))],
    )

    def spy(*a, **k):
        called.append(a)
        raise AssertionError("must not open a connection")

    monkeypatch.setattr(httpx, "AsyncClient", spy)
    assert await download_study_pdf(1, "http://localhost:80/a.pdf", "t") is None
    assert called == []


def test_download_ignores_non_http_urls(tmp_path, monkeypatch):
    asyncio.run(_test_download_ignores_non_http_urls_async(tmp_path, monkeypatch))


async def _test_download_ignores_non_http_urls_async(tmp_path, monkeypatch):
    import app.studies_store as store

    monkeypatch.setattr(store, "STUDIES_DIR", tmp_path)
    assert await store.download_study_pdf(1, "file:///etc/passwd", "t") is None
    assert await store.download_study_pdf(1, "", "t") is None
    assert list(tmp_path.iterdir()) == []


# ─────────────────── pdf_rag: chunking ───────────────────

def test_chunk_text_handles_empty_and_short_text():
    from app.pdf_rag import chunk_text

    assert chunk_text("") == []
    assert chunk_text("   \n\t ") == []
    assert chunk_text("short text") == ["short text"]


def test_chunk_text_splits_long_text_with_overlap_and_terminates():
    from app.pdf_rag import chunk_text

    text = ". ".join(f"Sentence number {i} with some words" for i in range(200))
    chunks = chunk_text(text, size=300, overlap=50)
    assert len(chunks) > 1
    assert all(chunks), "no empty chunk may be produced"
    assert all(len(c) <= 300 for c in chunks)
    joined = " ".join(chunks)
    assert joined.count("Sentence number") >= 200, "no sentence may be lost"


def test_chunk_text_collapses_whitespace():
    from app.pdf_rag import chunk_text

    assert chunk_text("a  \n\n b\t\tc") == ["a b c"]


def test_chunk_text_prefers_sentence_boundary():
    from app.pdf_rag import chunk_text

    text = ("A" * 100 + ". ") * 10
    chunks = chunk_text(text, size=120, overlap=10)
    assert all(c.endswith("A") or c.endswith(".") for c in chunks)


def test_extract_pdf_text_missing_file_returns_empty(tmp_path):
    from app.pdf_rag import extract_pdf_text

    assert extract_pdf_text(tmp_path / "missing.pdf") == ""


def test_index_pdf_skips_missing_file_and_empty_pdf(tmp_path):
    asyncio.run(_test_index_pdf_skips_missing_file_and_empty_pdf_async(tmp_path))


async def _test_index_pdf_skips_missing_file_and_empty_pdf_async(tmp_path):
    from app.pdf_rag import index_pdf

    assert await index_pdf(1, None) == 0
    assert await index_pdf(1, tmp_path / "missing.pdf") == 0

    blank = tmp_path / "blank.pdf"
    blank.write_bytes(b"%PDF-1.4\nnot really a pdf")
    assert await index_pdf(1, blank) == 0


def test_search_documents_without_query_or_backend_returns_empty():
    from app.pdf_rag import search_documents

    assert search_documents.__doc__ is not None


def test_search_documents_returns_empty_without_embeddings():
    asyncio.run(_test_search_documents_returns_empty_without_embeddings_async())


async def _test_search_documents_returns_empty_without_embeddings_async():
    """Mock provider = no Ollama: search degrades to [] instead of failing."""
    from app.pdf_rag import search_documents

    assert await search_documents("anything") == []
    assert await search_documents("") == []


# ─────────────────── embeddings: maths and degradation ───────────────────

def test_cosine_basics():
    from app.embeddings import cosine

    assert cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosine([1, 0], [-1, 0]) == pytest.approx(-1.0)
    assert cosine([1, 2, 3], [1, 2]) == 0.0, "different dimensions score 0"
    assert cosine([], [1]) == 0.0
    assert cosine([0, 0], [1, 1]) == 0.0, "zero vector must not divide by zero"


def test_embed_returns_none_in_mock_mode():
    from app.embeddings import embed

    assert embed(["text"]) is None, "mock provider must never reach the network"
    assert embed([]) is None


def test_rerank_without_backend_keeps_order():
    from app.embeddings import rerank

    candidates = [{"title": "B"}, {"title": "A"}]
    assert rerank("query", candidates) == candidates
    assert rerank("query", []) == []


def test_available_embed_models_falls_back_to_tag_list(monkeypatch):
    import app.embeddings as emb

    monkeypatch.setattr(emb, "_tags", None)
    monkeypatch.setattr(emb, "_ollama_tags", lambda: ["llama3:8b", "phi3:mini"])
    assert emb.available_embed_models() == ["llama3:8b", "phi3:mini"]

    monkeypatch.setattr(emb, "_ollama_tags", lambda: ["nomic-embed-text:latest", "llama3"])
    assert emb.available_embed_models() == ["nomic-embed-text:latest"]


def test_available_embed_models_survives_ollama_down(monkeypatch):
    import app.embeddings as emb

    monkeypatch.setattr(emb, "_ollama_tags", lambda: None)
    assert emb.available_embed_models() == []


def test_resolve_model_enters_cooldown_when_ollama_is_down(monkeypatch):
    """A down Ollama must not be hammered — and with no llama.cpp either, the
    app reports honestly that semantic retrieval is off."""
    import app.embeddings as emb

    monkeypatch.setattr(emb, "_active_model", None)
    monkeypatch.setattr(emb, "_cooldown_until", 0.0)
    monkeypatch.setattr(emb, "_tags", [])
    monkeypatch.setattr(emb, "_ollama_tags", lambda: None)
    # This test is about the Ollama path; a llama.cpp server answering on this
    # machine would (correctly) make retrieval available, so pin it off.
    monkeypatch.setattr(emb, "_llama_checked", True)
    monkeypatch.setattr(emb, "_llama_ok", False)
    # A *recent* failure means llama.cpp is not re-probed yet; after
    # LLAMA_RECHECK_SECONDS it is retried on purpose so that starting the
    # embedding server later heals a running app.
    monkeypatch.setattr(emb, "_llama_failed_at", time.monotonic())
    assert emb._resolve_model() is None
    assert emb._cooldown_until > 0, "a down server must not be hammered on every call"
    assert emb.embedding_available() is False


def test_semantic_retrieval_lives_even_when_ollama_is_down(monkeypatch):
    """llama.cpp alone is enough: no Ollama, and retrieval still works."""
    import app.embeddings as emb

    monkeypatch.setattr(emb, "_active_model", None)
    monkeypatch.setattr(emb, "_cooldown_until", 0.0)
    monkeypatch.setattr(emb, "_tags", [])
    monkeypatch.setattr(emb, "_ollama_tags", lambda: None)
    monkeypatch.setattr(emb, "_llama_checked", True)
    monkeypatch.setattr(emb, "_llama_ok", True)

    assert emb.embedding_available() is True
    assert emb.embedding_backend().startswith("llama.cpp")
    assert emb.embedding_model() == emb.LLAMA_EMBED_MODEL


def test_resolve_model_picks_the_configured_model_first(monkeypatch):
    import app.embeddings as emb

    monkeypatch.setattr(emb, "_active_model", None)
    monkeypatch.setattr(emb, "_cooldown_until", 0.0)
    monkeypatch.setattr(emb, "_tags", [])
    monkeypatch.setattr(emb, "_ollama_tags", lambda: ["all-minilm", "nomic-embed-text:latest"])
    monkeypatch.setattr(emb, "OLLAMA_EMBED_MODEL", "nomic-embed-text")
    assert emb._resolve_model() == "nomic-embed-text:latest"


def test_resolve_model_warns_when_only_chat_models_exist(monkeypatch):
    import app.embeddings as emb

    monkeypatch.setattr(emb, "_active_model", None)
    monkeypatch.setattr(emb, "_cooldown_until", 0.0)
    monkeypatch.setattr(emb, "_tags", [])
    monkeypatch.setattr(emb, "_ollama_tags", lambda: ["llama3:8b"])
    assert emb._resolve_model() is None
    assert emb._cooldown_until > 0


# ─────────────────── web_search: the DuckDuckGo parser ───────────────────

DDG_HTML = """
<html><body>
<a class="result__a" href="https://example.org/paper1">First &amp; Best Paper</a>
<a class="result__snippet">A <b>snippet</b> about the first.</a>
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fpaper2&amp;rut=ab">Second Paper</a>
<a class="result__snippet">Snippet two.</a>
</body></html>
"""


def test_duckduckgo_parser_strips_tags_and_decodes_redirects(monkeypatch):
    asyncio.run(_test_duckduckgo_parser_strips_tags_and_decodes_redirects_async(monkeypatch))


async def _test_duckduckgo_parser_strips_tags_and_decodes_redirects_async(monkeypatch):
    from app import web_search as ws

    class FakeResponse:
        status_code = 200
        text = DDG_HTML

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return FakeResponse()

    monkeypatch.setattr(ws.httpx, "AsyncClient", FakeClient)
    results = await ws._search_duckduckgo("q", 5)

    assert len(results) == 2
    assert results[0]["title"] == "First & Best Paper"
    assert results[0]["snippet"] == "A snippet about the first."
    assert results[1]["url"] == "https://example.org/paper2", "uddg redirect must be decoded"


def test_duckduckgo_degrades_to_empty_on_network_failure(monkeypatch):
    asyncio.run(_test_duckduckgo_degrades_to_empty_on_network_failure_async(monkeypatch))


async def _test_duckduckgo_degrades_to_empty_on_network_failure_async(monkeypatch):
    from app import web_search as ws

    class Boom:
        def __init__(self, *a, **k):
            raise httpx.ConnectError("down")

    monkeypatch.setattr(ws.httpx, "AsyncClient", Boom)
    assert await ws._search_duckduckgo("q", 5) == []


def test_search_web_short_circuits_on_empty_query(monkeypatch):
    asyncio.run(_test_search_web_short_circuits_on_empty_query_async(monkeypatch))


async def _test_search_web_short_circuits_on_empty_query_async(monkeypatch):
    from app import web_search as ws

    async def must_not_run(*a, **k):
        raise AssertionError("empty query must not search")

    monkeypatch.setattr(ws, "_search_duckduckgo", must_not_run)
    assert await ws.search_web("   ") == []


def test_search_web_uses_serper_first_when_keyed(monkeypatch):
    asyncio.run(_test_search_web_uses_serper_first_when_keyed_async(monkeypatch))


async def _test_search_web_uses_serper_first_when_keyed_async(monkeypatch):
    from app import web_search as ws

    async def fake_serper(query, n):
        return [{"title": "S", "url": "https://s", "snippet": ""}]

    async def must_not_run(*a, **k):
        raise AssertionError("fallback must not run when serper answers")

    monkeypatch.setattr(ws, "SERPER_API_KEY", "key")
    monkeypatch.setattr(ws, "_search_serper", fake_serper)
    monkeypatch.setattr(ws, "_search_duckduckgo", must_not_run)
    assert (await ws.search_web("x"))[0]["title"] == "S"


def test_search_web_falls_through_when_serper_fails(monkeypatch):
    asyncio.run(_test_search_web_falls_through_when_serper_fails_async(monkeypatch))


async def _test_search_web_falls_through_when_serper_fails_async(monkeypatch):
    from app import web_search as ws

    async def failing(query, n):
        return None

    async def ddg(query, n):
        return [{"title": "D", "url": "https://d", "snippet": ""}]

    monkeypatch.setattr(ws, "SERPER_API_KEY", "key")
    monkeypatch.setattr(ws, "_search_serper", failing)
    monkeypatch.setattr(ws, "_search_duckduckgo", ddg)
    assert (await ws.search_web("x"))[0]["title"] == "D"


def test_validate_url_refuses_internal_hosts_without_a_request(monkeypatch):
    asyncio.run(_test_validate_url_refuses_internal_hosts_without_a_request_async(monkeypatch))


async def _test_validate_url_refuses_internal_hosts_without_a_request_async(monkeypatch):
    from app import web_search as ws

    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda host, *a, **k: [(2, 1, 6, "", ("127.0.0.1", 0))],
    )
    assert await ws.validate_url("http://localhost/a.pdf") is False
    assert await ws.validate_url("") is False
    assert await ws.validate_url("file:///etc/passwd") is False


# ─────────────────── llm: retry policy and usage parsing ───────────────────

def test_retryable_classification():
    from app.llm import _is_retryable

    req = httpx.Request("GET", "http://x")
    assert _is_retryable(httpx.ConnectError("x")) is True
    assert _is_retryable(httpx.ReadTimeout("x")) is True
    assert _is_retryable(httpx.HTTPStatusError("500", request=req,
                                               response=httpx.Response(500, request=req))) is True
    assert _is_retryable(httpx.HTTPStatusError("429", request=req,
                                               response=httpx.Response(429, request=req))) is True
    assert _is_retryable(httpx.HTTPStatusError("401", request=req,
                                               response=httpx.Response(401, request=req))) is False
    assert _is_retryable(ValueError("nope")) is False


def test_with_retry_gives_up_on_non_retryable_errors_at_once():
    asyncio.run(_test_with_retry_gives_up_on_non_retryable_errors_at_once_async())


async def _test_with_retry_gives_up_on_non_retryable_errors_at_once_async():
    from app.llm import _with_retry

    calls = []

    async def boom():
        calls.append(1)
        raise ValueError("fatal")

    with pytest.raises(ValueError):
        await _with_retry(boom, attempts=3)
    assert len(calls) == 1, "a 4xx-class error must not be retried"


def test_with_retry_retries_transient_errors_then_succeeds():
    asyncio.run(_test_with_retry_retries_transient_errors_then_succeeds_async())


async def _test_with_retry_retries_transient_errors_then_succeeds_async():
    from app.llm import _with_retry

    calls = []

    async def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise httpx.ConnectError("down")
        return "ok"

    assert await _with_retry(flaky, attempts=3, base_delay=0) == "ok"
    assert len(calls) == 3


def test_with_retry_raises_after_the_last_attempt():
    asyncio.run(_test_with_retry_raises_after_the_last_attempt_async())


async def _test_with_retry_raises_after_the_last_attempt_async():
    from app.llm import _with_retry

    async def always_down():
        raise httpx.ConnectError("down")

    with pytest.raises(httpx.ConnectError):
        await _with_retry(always_down, attempts=2, base_delay=0)


def test_usage_tokens_reads_both_provider_shapes():
    from app.llm import _usage_tokens

    assert _usage_tokens({"prompt_tokens": 10, "completion_tokens": 5}) == (10, 5)
    assert _usage_tokens({"prompt_eval_count": 7, "eval_count": 3}) == (7, 3)
    assert _usage_tokens({}) == (0, 0)
    assert _usage_tokens(None) == (0, 0)


def test_prompt_chars_counts_only_dict_messages():
    from app.llm import _prompt_chars

    assert _prompt_chars([{"content": "abc"}, {"content": "de"}, "raw"]) == 5


def test_log_ai_event_writes_a_row_and_never_raises(client):
    from app.db import get_db
    from app.llm import log_ai_event

    log_ai_event("deepseek", "deepseek-chat", "complete", True, latency_ms=120,
                 prompt_chars=10, reply_chars=4, prompt_tokens=3, completion_tokens=2)
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM ai_events ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    assert row["provider"] == "deepseek"
    assert row["ok"] == 1
    assert row["prompt_tokens"] == 3


def test_log_ai_event_truncates_a_huge_error(client):
    from app.db import get_db
    from app.llm import log_ai_event

    log_ai_event("ollama", "m", "complete", False, error="x" * 5000)
    conn = get_db()
    try:
        row = conn.execute("SELECT error FROM ai_events ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    assert len(row["error"]) == 500


# ─────────────────── sse parser ───────────────────

def test_sse_parser_yields_content_and_stops_at_done():
    asyncio.run(_test_sse_parser_yields_content_and_stops_at_done_async())


async def _test_sse_parser_yields_content_and_stops_at_done_async():
    from app.llm import _iter_sse_content

    lines = [
        "data: {\"choices\":[{\"delta\":{\"content\":\"He\"}}]}",
        "",
        ": keep-alive comment",
        "data: {\"choices\":[{\"delta\":{\"content\":\"llo\"}}]}",
        "data: not json at all",
        "data: {\"choices\":[{\"delta\":{}}]}",
        "data: [DONE]",
        "data: {\"choices\":[{\"delta\":{\"content\":\"AFTER\"}}]}",
    ]

    class FakeResp:
        async def aiter_lines(self):
            for line in lines:
                yield line

    assert [c async for c in _iter_sse_content(FakeResp())] == ["He", "llo"]


def test_mock_complete_and_stream_stay_offline():
    import asyncio

    from app.llm import LLM_PROVIDER, complete, stream_complete

    assert LLM_PROVIDER == "mock"

    async def run():
        provider, model, text = await complete([{"role": "user", "content": "hi"}])
        assert (provider, model) == ("mock", "mock-model")

        chunks = [c async for c in stream_complete([{"role": "user", "content": "hi"}])]
        assert chunks == [("mock", "mock-model", "[mock]")]

    asyncio.run(run())


# ─────────────────── study_qa: audit rules ───────────────────

def test_study_detection_signals():
    from app.routers.study_qa import _is_study

    assert _is_study({"study_type": "paper"}) is True
    assert _is_study({"tags": "Study, memory"}) is True
    assert _is_study({"tags": "pdf"}) is True
    assert _is_study({"pdf_path": "1-x.pdf"}) is True
    assert _is_study({"content": "**PDF:** https://x/y.pdf"}) is True
    assert _is_study({"title": "plain note"}) is False


def test_study_audit_lists_every_missing_field():
    from app.routers.study_qa import _audit_study

    audit = _audit_study({"title": "t"}, 0)
    joined = " ".join(audit["issues"])
    for expected in ("dată", "autor", "design", "direc", "calitate", "sursa", "PDF", "indexat", "validată"):
        assert expected in joined, f"the audit must flag the missing {expected}"
    assert len(audit["issues"]) == len(audit["suggestions"])


def test_study_audit_is_quiet_on_a_complete_study():
    from app.routers.study_qa import _audit_study

    entry = {
        "study_type": "paper", "entry_date": "2020-01-01", "authors": "A",
        "design": "RCT", "effect_direction": "positive", "quality": 4,
        "source": "https://x/y.pdf", "pdf_path": "1-y.pdf", "url_validated": 1,
    }
    assert _audit_study(entry, 12)["issues"] == []


def test_study_check_endpoint_reports_a_real_entry(client):
    eid = client.post("/api/entries", json={"title": "A study", "study_type": "paper"}).json()["id"]
    res = client.get(f"/api/studies/{eid}/check")
    assert res.status_code == 200
    body = res.json()
    assert body["is_study"] is True
    assert body["indexed_chunks"] == 0
    assert body["issues"]


def test_study_check_on_a_plain_entry_and_unknown_id(client):
    eid = client.post("/api/entries", json={"title": "just a note"}).json()["id"]
    assert client.get(f"/api/studies/{eid}/check").json()["is_study"] is False
    assert client.get("/api/studies/999999/check").status_code == 404


def test_study_qa_requires_a_question_and_a_study(client):
    eid = client.post("/api/entries", json={"title": "S", "study_type": "paper"}).json()["id"]
    assert client.post(f"/api/studies/{eid}/qa", json={}).status_code == 400
    assert client.post(f"/api/studies/{eid}/qa", json={"question": "   "}).status_code == 400
    assert client.post("/api/studies/999999/qa", json={"question": "q"}).status_code == 404

    plain = client.post("/api/entries", json={"title": "note"}).json()["id"]
    assert client.post(f"/api/studies/{plain}/qa", json={"question": "q"}).status_code == 400


def test_study_qa_answers_offline_without_inventing_sources(client):
    eid = client.post("/api/entries", json={
        "title": "Mindfulness study", "study_type": "paper", "content": "abstract text",
    }).json()["id"]
    res = client.post(f"/api/studies/{eid}/qa", json={"question": "Care e efectul?"})
    assert res.status_code == 200
    body = res.json()
    assert "mod offline" in body["answer"]
    assert body["sources"] == []


def test_study_chunks_are_empty_without_indexed_pdf(client):
    from app.routers.study_qa import _study_chunks

    assert _study_chunks(999999, "query") == []


# ─────────────────── maintenance helpers ───────────────────

def test_clean_tags_deduplicates_and_joins():
    from app.routers.maintenance import _clean_tags

    # "a" is a stop word now (normalization drops it on every write path), so
    # the fixture uses real subject tags to test separators and deduplication.
    assert _clean_tags("alpha, beta | ALPHA ; gamma") == "alpha, beta, gamma"
    assert _clean_tags(' "x" , y ') == "x, y"
    assert _clean_tags("") == ""
    assert len(_clean_tags(",".join(f"t{i}" for i in range(500)))) <= 500


def test_norm_updates_fixes_dates_tags_and_source():
    from app.routers.maintenance import _norm_updates

    entry = {"title": "  spaced  ", "entry_date": "published 2016-05-04 in journal",
             "tags": "alpha|beta", "source": "  https://x  "}
    updates = _norm_updates(entry)
    assert updates["title"] == "spaced"
    assert updates["entry_date"] == "2016-05-04"
    assert updates["tags"] == "alpha, beta"
    assert updates["source"] == "https://x"


def test_norm_updates_falls_back_to_year_then_null():
    from app.routers.maintenance import _norm_updates

    assert _norm_updates({"title": "t", "entry_date": "collected in 2019", "tags": "", "source": ""})["entry_date"] == "2019-01-01"
    assert _norm_updates({"title": "t", "entry_date": "sometime", "tags": "", "source": ""})["entry_date"] is None


def test_norm_updates_leaves_a_clean_entry_alone():
    from app.routers.maintenance import _norm_updates

    clean = {"title": "Clean", "entry_date": "2020-01-01", "tags": "alpha, beta", "source": "https://x"}
    assert _norm_updates(clean) == {}


def test_norm_updates_recovers_a_date_hidden_in_tags():
    from app.routers.maintenance import _norm_updates

    entry = {"title": "t", "entry_date": "unknown", "tags": "study, 2015-03-09", "source": ""}
    assert _norm_updates(entry)["entry_date"] == "2015-03-09"


def test_extract_json_handles_fences_and_prose():
    from app.routers.maintenance import _extract_json

    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json('Here you go: {"a": 2} — done') == {"a": 2}
    with pytest.raises(ValueError):
        _extract_json("no json here")
    with pytest.raises(ValueError):
        _extract_json("}{")


def test_reanalysis_section_is_replaced_not_appended_twice():
    from app.routers.maintenance import _upsert_reanalysis_section, _REANALYSIS_START, _REANALYSIS_END

    section = f"{_REANALYSIS_START}\nnew body\n{_REANALYSIS_END}"
    first = _upsert_reanalysis_section("original text", section)
    assert "original text" in first and "new body" in first

    second = _upsert_reanalysis_section(first, f"{_REANALYSIS_START}\nNEWER\n{_REANALYSIS_END}")
    assert second.count(_REANALYSIS_START) == 1, "re-analysis must not stack sections"
    assert "NEWER" in second and "new body" not in second
    assert "original text" in second

    assert _upsert_reanalysis_section("", section).startswith(_REANALYSIS_START)


def test_reanalysis_markdown_includes_only_present_fields():
    from app.routers.maintenance import _reanalysis_markdown

    md = _reanalysis_markdown({"title": "T", "authors": "A", "year": 2016, "quality": 4,
                               "effect_direction": "unknown", "key_findings": ["f1", "f2"]})
    assert "**Title:** T" in md and "**Authors:** A" in md and "**Year:** 2016" in md
    assert "**Quality:** 4/5" in md
    assert "Effect:" not in md, "'unknown' is not worth showing"
    assert "- f1" in md and "- f2" in md


def test_maintenance_endpoint_streams_a_report(client):
    # Inserted directly on purpose: the API normalizes tags on write now, so a
    # row that still needs repairing can only come from before this change.
    from app.db import get_db

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO entries (title, entry_date, tags) VALUES (?, ?, ?)",
            ["  messy  ", "in 2018", "alpha|beta"],
        )
        conn.commit()
    finally:
        conn.close()
    res = client.post("/api/maintenance/analyze")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")

    events = [line for line in res.text.splitlines() if line.startswith("data: ")]
    assert events[-1] == "data: [DONE]"
    done = [json.loads(e[6:]) for e in events[:-1] if json.loads(e[6:]).get("type") == "done"]
    assert done, "the stream must end with a report"
    report = done[0]["report"]
    assert report["entries_scanned"] >= 1
    assert report["entries_fixed"] >= 1
    assert report["dates_fixed"] >= 1
    assert report["tags_fixed"] >= 1
    assert report["fts_rebuilt"] is True


def test_maintenance_removes_orphan_and_self_relationships(client):
    """The cleanup is a safety net for legacy rows inserted before the FK was on."""
    from app.db import get_db

    a = client.post("/api/entries", json={"title": "A"}).json()["id"]

    # A live connection refuses orphans outright — that is the first defence.
    conn = get_db()
    try:
        with pytest.raises(Exception):
            conn.execute("INSERT INTO relationships (from_id, to_id, label) VALUES (999999, ?, '')", [a])
        conn.rollback()
    finally:
        conn.close()

    # A legacy row written with foreign keys OFF still gets cleaned up.
    conn = get_db()
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("INSERT INTO relationships (from_id, to_id, label) VALUES (?, ?, '')", [a, a])
        conn.execute("INSERT INTO relationships (from_id, to_id, label) VALUES (999999, ?, '')", [a])
        conn.commit()
        assert conn.execute("SELECT COUNT(*) AS n FROM relationships").fetchone()["n"] == 2
    finally:
        conn.close()

    res = client.post("/api/maintenance/analyze")
    done = [json.loads(line[6:]) for line in res.text.splitlines()
            if line.startswith("data: ") and '"done"' in line][0]
    assert done["report"]["self_links_removed"] == 1
    assert done["report"]["relationships_removed"] == 1


def test_maintenance_recomputes_theory_status(client):
    from app.db import get_db

    eid = client.post("/api/entries", json={"title": "Study"}).json()["id"]
    tid = client.post("/api/theories", json={"name": "T", "status": "refuted"}).json()["id"]
    conn = get_db()
    try:
        conn.execute("INSERT INTO theory_evidence (theory_id, entry_id, stance, weight) VALUES (?, ?, 'pro', 3)",
                     [tid, eid])
        conn.commit()
    finally:
        conn.close()

    res = client.post("/api/maintenance/analyze")
    done = [json.loads(line[6:]) for line in res.text.splitlines()
            if line.startswith("data: ") and '"done"' in line][0]
    assert done["report"]["theories_updated"] == 1
    assert client.get("/api/theories").json()[0]["status"] == "supported"


def test_maintenance_salvages_pdf_link_into_source(client):
    client.post("/api/entries", json={
        "title": "S", "study_type": "paper", "source": "",
        "content": "**PDF:** https://example.org/paper.pdf",
    })
    res = client.post("/api/maintenance/analyze")
    done = [json.loads(line[6:]) for line in res.text.splitlines()
            if line.startswith("data: ") and '"done"' in line][0]
    assert done["report"]["sources_filled"] == 1
    assert client.get("/api/entries").json()[0]["source"] == "https://example.org/paper.pdf"


def test_maintenance_warns_when_no_pdfs_exist(client):
    client.post("/api/entries", json={"title": "S", "study_type": "paper"})
    res = client.post("/api/maintenance/analyze")
    done = [json.loads(line[6:]) for line in res.text.splitlines()
            if line.startswith("data: ") and '"done"' in line][0]
    warnings = " ".join(done["report"]["warnings"])
    assert "No downloaded PDFs" in warnings


# ─────────────────── db: migrations and FTS ───────────────────

def test_fts_stays_in_sync_on_insert_update_delete(client, tmp_path):
    from app.db import get_db

    eid = client.post("/api/entries", json={"title": "alpha", "content": "first body"}).json()["id"]
    assert client.get("/api/search?q=alpha").json() != []

    client.put(f"/api/entries/{eid}", json={"title": "beta", "content": "second body"})
    assert client.get("/api/search?q=beta").json() != []
    assert client.get("/api/search?q=alpha").json() == [], "the old term must leave the index"

    client.delete(f"/api/entries/{eid}")
    assert client.get("/api/search?q=beta").json() == []


def test_rebuild_fts_recovers_a_desynced_index(client):
    from app.db import get_db, rebuild_fts

    client.post("/api/entries", json={"title": "recoverme"})
    conn = get_db()
    try:
        conn.execute("INSERT INTO entries_fts(entries_fts) VALUES('delete-all')")
        conn.commit()
    finally:
        conn.close()
    assert client.get("/api/search?q=recoverme").json() == []

    rebuild_fts()
    assert client.get("/api/search?q=recoverme").json() != []


def test_init_db_is_idempotent_and_keeps_data(client):
    import app.db as db_module

    eid = client.post("/api/entries", json={"title": "keep me"}).json()["id"]
    db_module.init_db()
    db_module.init_db()
    assert client.get(f"/api/entries/{eid}").status_code == 200


def test_foreign_keys_cascade_on_delete(client):
    from app.db import get_db

    eid = client.post("/api/entries", json={"title": "A", "study_type": "paper"}).json()["id"]
    other = client.post("/api/entries", json={"title": "B"}).json()["id"]
    client.post("/api/relationships", json={"from_id": eid, "to_id": other, "label": "x"})
    client.post(f"/api/entries/{eid}/todo", json={})
    tid = client.post("/api/theories", json={"name": "T"}).json()["id"]
    client.post(f"/api/theories/{tid}/evidence", json={"entry_id": eid, "stance": "pro"})

    client.delete(f"/api/entries/{eid}")

    conn = get_db()
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM relationships WHERE from_id = ?", [eid]).fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM todos WHERE entry_id = ?", [eid]).fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM theory_evidence WHERE entry_id = ?", [eid]).fetchone()["n"] == 0
    finally:
        conn.close()


def test_deleting_a_theory_keeps_children_as_roots(client):
    parent = client.post("/api/theories", json={"name": "parent"}).json()["id"]
    child = client.post("/api/theories", json={"name": "child", "parent_id": parent}).json()["id"]
    assert client.delete(f"/api/theories/{parent}").status_code == 204

    tree = client.get("/api/theories/tree").json()
    assert [n["id"] for n in tree] == [child], "the child becomes a root, it is not deleted"
    assert tree[0]["parent_id"] is None
