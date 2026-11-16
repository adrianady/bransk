"""Cloud-only LLM guard: a local model endpoint can never be saved or used as a fallback.

Embeddings and OCR stay local (CPU); only the LLM/AI-task path is constrained.
No network calls here — provider discovery is inspected directly.
"""
from __future__ import annotations

import pytest

from app import llm


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8198/v1",
    "http://localhost:11434/v1",
    "http://[::1]:8080/v1",
    "https://192.168.1.10/v1",
])
def test_loopback_endpoint_detected(url):
    assert llm.is_local_endpoint(url) is True


@pytest.mark.parametrize("url", [
    "https://api.deepseek.com/v1",
    "https://integrate.api.nvidia.com/v1",
    "https://openrouter.ai/api/v1",
])
def test_cloud_endpoint_allowed(url):
    assert llm.is_local_endpoint(url) is False


def test_saving_local_llm_endpoint_is_refused(monkeypatch):
    saved = {}
    monkeypatch.setattr(llm, "_set_setting", lambda key, value: saved.update({key: value}))
    with pytest.raises(ValueError, match="local"):
        llm.set_custom_provider("http://127.0.0.1:8198/v1", "k", "qwen-local")
    assert saved == {}


def test_local_custom_never_enters_failover_order(monkeypatch):
    monkeypatch.setattr(llm, "custom_provider", lambda: {
        "base_url": "http://127.0.0.1:8198/v1", "api_key": "k", "model": "qwen",
    })
    monkeypatch.delenv("LLM_PROVIDERS", raising=False)
    assert "custom" not in llm.provider_order()


def test_local_custom_is_not_reported_as_configured(monkeypatch):
    from app.db import init_db

    init_db()
    monkeypatch.setattr(llm, "custom_provider", lambda: {
        "base_url": "http://127.0.0.1:8198/v1", "api_key": "k", "model": "qwen",
    })
    monkeypatch.setattr(llm, "active_provider", lambda: "nvidia")
    custom = next(p for p in llm.provider_catalogue() if p["id"] == "custom")
    assert custom["configured"] is False


def test_local_custom_has_no_chat_url(monkeypatch):
    monkeypatch.setattr(llm, "custom_provider", lambda: {
        "base_url": "http://127.0.0.1:8198/v1", "api_key": "k", "model": "qwen",
    })
    assert llm._provider_url("custom") == ""
