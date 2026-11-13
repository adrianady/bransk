"""NVIDIA as a first-class OpenAI-compatible provider (catalogue, vision, IPv4).

Hermetic: every HTTP call is answered by httpx.MockTransport, exactly like
tests/test_providers.py and tests/test_llm_vision.py. The measured facts these
tests protect:
- the catalogue must come from NVIDIA, not from a wrong "no DEEPSEEK key" note;
- vision defaults to max_tokens 4096 because 1200 truncates a reasoning model;
- content=null + reasoning_content is an answer, not a crash;
- finish_reason=length is surfaced, not silently accepted as a complete page;
- a 410 names the retired model in Romanian;
- a connection error is retried exactly once over IPv4.
"""
from __future__ import annotations

import asyncio
import base64
import json

import httpx
import pytest

from app import llm

PNG_BYTES = b"\x89PNG\r\n\x1a\nfakepngdata"
VISION_MODEL = "nvidia/nemotron-3.5-super-vl-120b-a12b"


@pytest.fixture()
def fresh_settings():
    """Clean llm_* settings rows so each test starts from .env values."""
    from app.db import get_db, init_db

    init_db()

    def _clear():
        conn = get_db()
        try:
            conn.execute("DELETE FROM app_settings WHERE key LIKE 'llm_%'")
            conn.commit()
        finally:
            conn.close()

    _clear()
    llm._vision_support_cache.clear()
    yield
    _clear()
    llm._vision_support_cache.clear()


@pytest.fixture(autouse=True)
def live_mode(monkeypatch):
    """The shared conftest pins LLM_PROVIDER=mock; these tests need real paths."""
    monkeypatch.setattr(llm, "LLM_PROVIDER", "auto")


def _patch_clients(monkeypatch, handler):
    """Route every httpx.AsyncClient through a MockTransport (no sockets)."""
    real = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(llm.httpx, "AsyncClient", patched)


def _use_nvidia(monkeypatch, model: str = VISION_MODEL):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
    monkeypatch.setenv("LLM_PROVIDERS", "nvidia")
    llm.set_active_provider("nvidia")
    llm.set_active_model(model, provider="nvidia")


def _completion(content=None, *, reasoning=None, finish_reason="stop"):
    message: dict = {"role": "assistant", "content": content}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return {
        "choices": [{"message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 12},
    }


def test_model_catalogue_returns_nvidia_ids(fresh_settings, monkeypatch):
    """Regression: NVIDIA used to answer with a misleading "no DEEPSEEK key" note."""
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"data": [
            {"id": VISION_MODEL, "owned_by": "nvidia"},
            {"id": "nvidia/llama-3.3-nemotron-super-49b-v1", "owned_by": "nvidia"},
        ]})

    _patch_clients(monkeypatch, handler)
    result = asyncio.run(llm.model_catalogue("nvidia"))

    assert seen["url"].endswith("/models")
    assert "integrate.api.nvidia.com" in seen["url"]
    assert seen["auth"] == "Bearer nvapi-test"
    assert [m["id"] for m in result["models"]] == [
        VISION_MODEL, "nvidia/llama-3.3-nemotron-super-49b-v1",
    ]
    assert result["from_provider"] is True
    assert result["provider"] == "nvidia"
    assert "DEEPSEEK" not in result["note"]


def test_nvidia_base_url_comes_from_env(fresh_settings, monkeypatch):
    """NVIDIA_BASE_URL lets a proxied/private NIM endpoint replace the public one."""
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
    monkeypatch.setenv("NVIDIA_BASE_URL", "https://nim.internal.example/v1")
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"data": [{"id": VISION_MODEL}]})

    _patch_clients(monkeypatch, handler)
    asyncio.run(llm.model_catalogue("nvidia"))
    assert seen["url"] == "https://nim.internal.example/v1/models"


def test_vision_payload_defaults_max_tokens_to_4096(fresh_settings, monkeypatch):
    """OpenAI image_url data-URL shape, with the measured 4096-token budget."""
    _use_nvidia(monkeypatch)
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_completion("pagina transcrisă"))

    _patch_clients(monkeypatch, handler)
    provider, model, text = asyncio.run(llm.complete_with_images(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "transcrie"}],
        [{"data": PNG_BYTES, "mime": "image/png"}],
    ))

    assert (provider, model, text) == ("nvidia", VISION_MODEL, "pagina transcrisă")
    assert captured["url"].endswith("/chat/completions")
    assert captured["body"]["max_tokens"] == 4096
    multimodal = [
        m for m in captured["body"]["messages"]
        if m["role"] == "user" and isinstance(m.get("content"), list)
    ]
    assert len(multimodal) == 1
    images = [p for p in multimodal[0]["content"] if p.get("type") == "image_url"]
    assert len(images) == 1
    url = images[0]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == PNG_BYTES


def test_null_content_falls_back_to_reasoning_content(fresh_settings, monkeypatch, caplog):
    """A reasoning-only reply must yield text instead of crashing on None."""
    _use_nvidia(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion(None, reasoning="Textul paginii, doar în raționament."))

    _patch_clients(monkeypatch, handler)
    with caplog.at_level("WARNING", logger="chronokg.llm"):
        provider, _model, text = asyncio.run(llm.complete(
            [{"role": "user", "content": "transcrie"}]))
    assert provider == "nvidia"
    assert text == "Textul paginii, doar în raționament."
    assert any("reasoning_content" in rec.message for rec in caplog.records)


def test_finish_reason_length_is_surfaced(fresh_settings, monkeypatch):
    """A truncated page must be marked, never passed off as complete."""
    _use_nvidia(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion("jumătate de pagină",
                                                    finish_reason="length"))

    _patch_clients(monkeypatch, handler)
    _provider, _model, text = asyncio.run(llm.complete(
        [{"role": "user", "content": "transcrie"}]))
    assert text.startswith("jumătate de pagină")
    assert "trunchiat" in text
    assert "finish_reason=length" in text


def test_retired_model_410_names_the_model_in_romanian(fresh_settings, monkeypatch):
    """410 is not a generic failure: it says which model was retired."""
    _use_nvidia(monkeypatch, model="meta/llama-3.1-8b-instruct")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(410, json={"error": "model retired"})

    _patch_clients(monkeypatch, handler)
    with pytest.raises(llm.LLMProviderError) as excinfo:
        asyncio.run(llm.complete([{"role": "user", "content": "salut"}]))

    message = str(excinfo.value)
    assert "meta/llama-3.1-8b-instruct" in message
    assert "retras" in message
    assert "410" in message


def test_connection_error_is_retried_once_over_ipv4(fresh_settings, monkeypatch):
    """One lost connection retries exactly once; the second answer is returned."""
    _use_nvidia(monkeypatch)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if len(calls) == 1:
            raise httpx.ConnectError("simulated IPv6-first connection failure", request=request)
        return httpx.Response(200, json=_completion("a doua încercare"))

    _patch_clients(monkeypatch, handler)
    _provider, _model, text = asyncio.run(llm.complete(
        [{"role": "user", "content": "salut"}]))

    assert text == "a doua încercare"
    assert len(calls) == 2, "exactly one retry, not a retry loop"


def test_transport_is_pinned_to_ipv4():
    """The shared transport binds its source socket to IPv4 (0.0.0.0)."""
    transport = llm._openai_transport()
    assert transport._pool._local_address == "0.0.0.0"
