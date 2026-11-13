"""Vision input over the provider layer: payload shapes, capability, errors.

Hermetic: every provider answers through httpx.MockTransport, capability reads
through a stubbed model_catalogue. Mirrors tests/test_providers.py stubbing.
"""
from __future__ import annotations

import asyncio
import base64
import json

import httpx
import pytest

from app import llm

PNG_BYTES = b"\x89PNG\r\n\x1a\nfakepngdata"


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
    llm._vision_support_cache.clear()
    yield
    conn = get_db()
    try:
        conn.execute("DELETE FROM app_settings WHERE key LIKE 'llm_%'")
        conn.commit()
    finally:
        conn.close()
    llm._vision_support_cache.clear()


@pytest.fixture(autouse=True)
def live_mode(monkeypatch):
    """The shared conftest pins LLM_PROVIDER=mock; vision payloads need live mode."""
    monkeypatch.setattr(llm, "LLM_PROVIDER", "auto")


def _patch_clients(monkeypatch, handler):
    real = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(llm.httpx, "AsyncClient", patched)


def test_openai_vision_payload_shape(fresh_settings, monkeypatch):
    """OpenAI-compatible: one user content list with text + image_url data-URL."""
    monkeypatch.setenv("LLM_PROVIDERS", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    llm.set_active_provider("openai")
    llm.set_active_model("gpt-4o-mini", provider="openai")

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "parsed page"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 6},
        })

    _patch_clients(monkeypatch, handler)
    provider, model, text = asyncio.run(llm.complete_with_images(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "transcrie"}],
        [{"data": PNG_BYTES, "mime": "image/png"}],
    ))
    assert (provider, model, text) == ("openai", "gpt-4o-mini", "parsed page")
    messages = captured["body"]["messages"]
    # System messages stay text-only.
    system = [m for m in messages if m["role"] == "system"]
    assert system and all(isinstance(m["content"], str) for m in system)
    multimodal = [m for m in messages if m["role"] == "user" and isinstance(m.get("content"), list)]
    assert len(multimodal) == 1
    content = multimodal[0]["content"]
    texts = [p for p in content if p.get("type") == "text"]
    imgs = [p for p in content if p.get("type") == "image_url"]
    assert texts and texts[0]["text"] == "transcrie"
    assert len(imgs) == 1
    url = imgs[0]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == PNG_BYTES


def test_ollama_vision_payload_shape(fresh_settings, monkeypatch):
    """Ollama native /api/chat: images is a base64 list, no data: prefix."""
    monkeypatch.setenv("LLM_PROVIDERS", "ollama")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    llm.set_active_provider("ollama")
    llm.set_active_model("qwen3-vl:4b", provider="ollama")

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "message": {"role": "assistant", "content": "pagina 1"},
            "prompt_eval_count": 7, "eval_count": 8,
        })

    _patch_clients(monkeypatch, handler)
    provider, model, text = asyncio.run(llm.complete_with_images(
        [{"role": "user", "content": "transcrie"}],
        [{"data": PNG_BYTES, "mime": "image/png"}],
    ))
    assert (provider, model, text) == ("ollama", "qwen3-vl:4b", "pagina 1")
    assert "/api/chat" in captured["url"]
    user_msgs = [m for m in captured["body"]["messages"] if m["role"] == "user"]
    assert user_msgs
    images = user_msgs[-1].get("images")
    assert isinstance(images, list) and len(images) == 1
    assert all(":" not in entry and not entry.startswith("data:") for entry in images)
    assert base64.b64decode(images[0]) == PNG_BYTES
    assert user_msgs[-1]["content"] == "transcrie"


def test_model_supports_images_from_catalogue(fresh_settings, monkeypatch):
    """True/False from stubbed input_modalities; None when the call fails."""
    async def fake_catalogue(for_provider=None):
        return {"models": [
            {"id": "vision-model", "input_modalities": ["TEXT", "Image"]},
            {"id": "text-model", "input_modalities": ["text"]},
            {"id": "mystery-model"},
        ]}

    monkeypatch.setattr(llm, "model_catalogue", fake_catalogue)
    assert asyncio.run(llm.model_supports_images("openai", "vision-model")) is True
    assert asyncio.run(llm.model_supports_images("openai", "text-model")) is False
    # No modality field at all: unknown, not False.
    assert asyncio.run(llm.model_supports_images("openai", "mystery-model")) is None
    # Unlisted model: unknown.
    assert asyncio.run(llm.model_supports_images("openai", "nope")) is None

    async def failing_catalogue(for_provider=None):
        raise RuntimeError("offline")

    monkeypatch.setattr(llm, "model_catalogue", failing_catalogue)
    llm._vision_support_cache.clear()
    assert asyncio.run(llm.model_supports_images("openai", "vision-model")) is None


def test_vision_400_becomes_actionable_error(fresh_settings, monkeypatch):
    """DeepSeek-style 400 surfaces as VisionUnsupportedError naming both."""
    monkeypatch.setenv("LLM_PROVIDERS", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    llm.set_active_provider("deepseek")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "This model does not support image input."}})

    _patch_clients(monkeypatch, handler)
    with pytest.raises(llm.VisionUnsupportedError) as excinfo:
        asyncio.run(llm.complete_with_images(
            [{"role": "user", "content": "transcrie"}],
            [{"data": PNG_BYTES, "mime": "image/png"}],
            provider="deepseek",
            model="deepseek-v4-pro",
        ))
    message = str(excinfo.value)
    assert "deepseek" in message.lower()
    assert "deepseek-v4-pro" in message


def test_complete_still_works(fresh_settings, monkeypatch):
    """Non-regression smoke: the text-only path is untouched by the vision work."""
    monkeypatch.setattr(llm, "LLM_PROVIDER", "mock")
    assert asyncio.run(llm.complete([{"role": "user", "content": "hi"}])) == ("mock", "mock-model", "[mock]")
