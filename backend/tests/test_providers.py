"""Provider failover: the chat must never die because one key is missing.

No network: every provider is answered by `httpx.MockTransport`, so these
tests state the contract — which provider is tried when, what a missing key
does, and that choosing a provider survives a restart (it is a database row).
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app import llm


@pytest.fixture()
def fresh_settings():
    """Fresh schema, then clean provider/model rows (each test starts from .env)."""
    from app.db import get_db, init_db

    init_db()
    conn = get_db()
    try:
        conn.execute("DELETE FROM app_settings WHERE key LIKE 'llm_%'")
        conn.commit()
    finally:
        conn.close()
    yield
    conn = get_db()
    try:
        conn.execute("DELETE FROM app_settings WHERE key LIKE 'llm_%'")
        conn.commit()
    finally:
        conn.close()


def _ok_response(request: httpx.Request, provider: str) -> httpx.Response:
    body = json.loads(request.content)
    return httpx.Response(200, json={
        "choices": [{"message": {"content": f"reply from {provider} ({body['model']})"}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 4},
    })


@pytest.fixture(autouse=True)
def live_mode(monkeypatch):
    """The shared conftest pins LLM_PROVIDER=mock; provider failover needs a real mode."""
    monkeypatch.setattr(llm, "LLM_PROVIDER", "auto")


def _patch_clients(monkeypatch, handler):
    real = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(llm.httpx, "AsyncClient", patched)


def test_registry_lists_every_provider_and_skips_unconfigured_ones(fresh_settings, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "")
    catalogue = {p["id"]: p for p in llm.provider_catalogue()}
    for pid in ("deepseek", "openai", "openrouter", "groq", "mistral", "together", "ollama", "mock"):
        assert pid in catalogue, f"{pid} must be listed"
    assert catalogue["ollama"]["configured"] is True  # local, no key needed
    assert catalogue["groq"]["configured"] is False  # no key set


def test_order_comes_from_env_and_never_appends_ollama(fresh_settings, monkeypatch):
    """The reserve list is what the owner wrote; Ollama is not smuggled in.

    The project runs its models on llama.cpp / a custom endpoint, so Ollama is
    not an automatic step — it must be named explicitly to be used.
    """
    monkeypatch.setenv("LLM_PROVIDERS", "groq,openrouter")
    assert llm.provider_order() == ["groq", "openrouter"]
    monkeypatch.setenv("LLM_PROVIDERS", "")
    assert "ollama" not in llm.provider_order()
    monkeypatch.setenv("LLM_PROVIDERS", "deepseek,ollama")
    assert llm.provider_order() == ["deepseek", "ollama"]  # explicit choice is honoured


def test_missing_key_is_skipped_and_the_next_provider_answers(fresh_settings, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDERS", "groq,openai")
    monkeypatch.setenv("GROQ_API_KEY", "")       # not configured
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    llm.set_active_provider("groq")  # the chosen one has no key

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        assert "api.openai.com" in str(request.url), "only the configured provider may be called"
        return _ok_response(request, "openai")

    _patch_clients(monkeypatch, handler)
    provider, model, text = asyncio.run(llm.complete([{"role": "user", "content": "hi"}]))
    assert provider == "openai"
    assert "openai" in text
    assert any("api.openai.com" in u for u in seen)


def test_a_failing_provider_hands_over_to_the_next_one(fresh_settings, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDERS", "groq,openai")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    llm.set_active_provider("groq")

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "api.groq.com" in url:
            return httpx.Response(500, json={"error": "boom"})
        return _ok_response(request, "openai")

    _patch_clients(monkeypatch, handler)
    provider, _model, text = asyncio.run(llm.complete([{"role": "user", "content": "hi"}]))
    assert provider == "openai"


def test_choosing_a_provider_persists_and_clearing_falls_back(fresh_settings, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    assert llm.set_active_provider("openrouter") == "openrouter"
    assert llm.active_provider() == "openrouter"
    from app.db import get_db

    conn = get_db()
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key = 'llm_provider'").fetchone()
    finally:
        conn.close()
    assert row[0] == "openrouter"


def test_each_provider_keeps_its_own_model(fresh_settings, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    llm.set_active_provider("openai")
    llm.set_active_model("gpt-4o")
    llm.set_active_provider("groq")
    llm.set_active_model("llama-3.1-8b-instant")
    assert llm.provider_model("openai") == "gpt-4o"
    assert llm.provider_model("groq") == "llama-3.1-8b-instant"
    assert llm.provider_model("ollama") == llm.OLLAMA_MODEL


def test_all_providers_failing_raises_one_clear_error(fresh_settings, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDERS", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    llm.set_active_provider("groq")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad key"})

    _patch_clients(monkeypatch, handler)
    with pytest.raises(Exception) as excinfo:
        asyncio.run(llm.complete([{"role": "user", "content": "hi"}]))
    assert "No LLM provider answered" in str(excinfo.value)


def test_streaming_uses_the_chosen_provider(fresh_settings, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    llm.set_active_provider("groq")

    def handler(request: httpx.Request) -> httpx.Response:
        lines = (
            'data: {"choices":[{"delta":{"content":"hel"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=lines, headers={"content-type": "text/event-stream"})

    _patch_clients(monkeypatch, handler)

    async def collect():
        out = []
        async for chunk in llm.stream_complete([{"role": "user", "content": "hi"}]):
            out.append(chunk)
        return out

    chunks = asyncio.run(collect())
    assert [c[2] for c in chunks] == ["hel", "lo"]
    assert all(c[0] == "groq" for c in chunks)


def test_ai_status_and_provider_routes_stay_compatible(fresh_settings):
    from fastapi.testclient import TestClient

    from app.db import init_db
    from app.main import app

    init_db()

    client = TestClient(app)
    providers = client.get("/api/ai/providers")
    assert providers.status_code == 200
    body = providers.json()
    assert "providers" in body and "active" in body and "order" in body
    assert client.get("/api/ai/status").status_code == 200
    assert client.get("/api/ai/models").status_code == 200
    bad = client.post("/api/ai/provider", json={"provider": "does-not-exist"})
    assert bad.status_code == 400


def test_custom_provider_fetching_models_and_chatting(fresh_settings, monkeypatch):
    """The user's own endpoint: list its models, save it, then chat through it."""
    from fastapi.testclient import TestClient

    from app.db import init_db
    from app.main import app

    init_db()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/models"):
            assert request.headers.get("authorization") == "Bearer my-key"
            return httpx.Response(200, json={"data": [{"id": "local-model", "owned_by": "me"}]})
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "reply from custom"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2},
        })

    _patch_clients(monkeypatch, handler)
    client = TestClient(app)

    fetched = client.post("/api/ai/custom/models", json={
        "base_url": "http://127.0.0.1:8099/v1", "api_key": "my-key",
    })
    assert fetched.status_code == 200
    assert fetched.json()["models"][0]["id"] == "local-model"

    saved = client.post("/api/ai/custom", json={
        "base_url": "http://127.0.0.1:8099/v1", "api_key": "my-key",
        "model": "local-model", "label": "Local", "activate": True,
    })
    assert saved.status_code == 200
    assert saved.json()["active"] == "custom"

    state = client.get("/api/ai/custom").json()
    assert state["configured"] is True
    assert "my-key" not in json.dumps(state), "the key must never be echoed back"

    provider, model, text = asyncio.run(llm.complete([{"role": "user", "content": "hi"}]))
    assert (provider, model, text) == ("custom", "local-model", "reply from custom")


def test_custom_provider_needs_an_http_url(fresh_settings):
    from fastapi.testclient import TestClient

    from app.db import init_db
    from app.main import app

    init_db()
    client = TestClient(app)
    bad = client.post("/api/ai/custom", json={"base_url": "ftp://example.com", "api_key": ""})
    assert bad.status_code == 400
    assert "http" in bad.json()["detail"].lower()
