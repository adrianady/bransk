"""The deployed Bransk AI boundary is CPU-only and fails closed."""
from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app import embeddings, llm
from app.cpu_only import CPU_ONLY_MODE, is_loopback_url
from app.main import app


@pytest.fixture()
def local_provider(monkeypatch):
    from app.db import get_db, init_db

    assert CPU_ONLY_MODE is True
    monkeypatch.setattr(llm, "CPU_ONLY_MODE", True)
    monkeypatch.setattr(llm, "LLM_PROVIDER", "auto")
    init_db()
    conn = get_db()
    try:
        conn.execute("DELETE FROM app_settings WHERE key LIKE 'llm_%'")
        conn.commit()
    finally:
        conn.close()
    llm.set_custom_provider(
        "http://127.0.0.1:8198/v1",
        "local-test-key",
        "qwen2.5-coder-7b-instruct-q4_k_m",
    )
    llm.set_active_provider("custom")
    yield
    conn = get_db()
    try:
        conn.execute("DELETE FROM app_settings WHERE key LIKE 'llm_%'")
        conn.commit()
    finally:
        conn.close()


def test_only_explicit_loopback_and_mock_are_allowed(local_provider):
    assert llm.active_provider() == "custom"
    assert llm.provider_order() == ["custom"]
    assert llm.provider_allowed("custom")
    assert llm.provider_allowed("mock")
    for provider in ("deepseek", "nvidia", "openai", "ollama", "opencode_console", "opencode"):
        assert not llm.provider_allowed(provider)
        assert not next(p for p in llm.provider_catalogue() if p["id"] == provider)["allowed"]

    with pytest.raises(ValueError, match="CPU-only"):
        llm.set_active_provider("nvidia")
    assert llm.active_provider() == "custom"


def test_loopback_validator_rejects_remote_and_credential_urls():
    assert is_loopback_url("http://127.0.0.1:8198/v1")
    assert is_loopback_url("http://localhost:8198/v1")
    assert is_loopback_url("http://[::1]:8198/v1")
    assert not is_loopback_url("https://api.example.com/v1")
    assert not is_loopback_url("http://user:password@127.0.0.1:8198/v1")
    assert not is_loopback_url("http://127.0.0.1:8198/v1?next=https://example.com")


def test_local_failure_never_falls_through_to_online_provider(local_provider, monkeypatch):
    calls: list[str] = []

    async def local_failure(provider, *_args, **_kwargs):
        calls.append(provider)
        raise httpx.ConnectError("local model unavailable")

    monkeypatch.setattr(llm, "_call_provider", local_failure)
    with pytest.raises(RuntimeError, match="No LLM provider answered"):
        asyncio.run(llm.complete([{"role": "user", "content": "synthetic offline test"}]))
    assert calls
    assert set(calls) == {"custom"}


def test_remote_provider_routes_and_external_custom_urls_are_rejected(local_provider):
    with TestClient(app) as client:
        assert client.get("/api/ai/status").json()["cpu_only"] is True
        assert client.get("/api/ai/providers").json()["cpu_only"] is True
        assert client.post("/api/ai/provider", json={"provider": "nvidia"}).status_code == 409
        assert client.post(
            "/api/ai/test-connection", json={"provider": "nvidia"}
        ).status_code == 409
        assert client.post(
            "/api/ai/custom/models",
            json={"base_url": "https://api.example.com/v1", "api_key": "not-sent"},
        ).status_code == 409
        assert client.post(
            "/api/ai/custom",
            json={"base_url": "https://api.example.com/v1", "api_key": "not-sent"},
        ).status_code == 400


def test_embeddings_do_not_fall_back_to_ollama(local_provider, monkeypatch):
    monkeypatch.setattr(embeddings, "CPU_ONLY_MODE", True)
    monkeypatch.setattr(embeddings, "_llama_available", lambda: False)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Ollama must not be contacted in CPU-only mode")

    monkeypatch.setattr(embeddings.httpx.Client, "get", forbidden)
    monkeypatch.setattr(embeddings.httpx.Client, "post", forbidden)
    assert embeddings._ollama_tags() is None
    assert embeddings._resolve_model() is None
    assert embeddings._batch_embed(["synthetic text"]) is None
    assert embeddings.embedding_backend().startswith("none")


def test_scanned_page_ocr_is_preserved_but_never_claimed_verified(local_provider, monkeypatch):
    from app import document_parse

    transcript = "Synthetic OCR text, retained only as unverified evidence. " * 8
    monkeypatch.setattr(document_parse, "CPU_ONLY_MODE", True)
    monkeypatch.setattr(
        document_parse,
        "run_tesseract_ocr",
        lambda _png, lang=None: {
            "text": transcript,
            "lang": lang or "eng",
            "elapsed_ms": 2,
        },
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Scanned-page image must not be sent to a vision model")

    monkeypatch.setattr(document_parse, "complete_with_images", forbidden)
    parsed = document_parse.parse_scanned_page(b"synthetic image")
    assert parsed["text"] == transcript
    assert parsed["source"] == "ocr"
    assert parsed["status"] == "vision-uncertain"
    assert parsed["verdict"] == "unavailable"
    assert parsed["vision_text"] == ""
    assert "cpu_only_vision_unavailable" in parsed["reasons"]


def test_local_context_is_trimmed_before_dispatch(local_provider):
    """A long chat history must not produce the local server's HTTP 400."""
    messages = [{"role": "system", "content": " Instrucțiuni. " * 400}]
    messages += [
        {"role": i % 2 and "user" or "assistant", "content": f"Tur {i}: " + "text vechi " * 300}
        for i in range(10)
    ]
    messages.append({"role": "user", "content": "Răspunde scurt: ce este 2+2?"})

    fitted, reply_budget = llm._fit_local_context(messages, 4096)
    assert reply_budget <= llm.CPU_ONLY_LOCAL_CONTEXT // 4
    assert fitted[-1]["content"] == "Răspunde scurt: ce este 2+2?"
    approx_tokens = sum(4 + len(str(m.get("content", ""))) // 3 for m in fitted)
    assert approx_tokens <= llm.CPU_ONLY_LOCAL_CONTEXT - reply_budget


def test_direct_vision_completion_is_blocked(local_provider, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Vision dispatch must be blocked before transport")

    monkeypatch.setattr(llm, "_call_vision_provider", forbidden)
    with pytest.raises(llm.VisionUnsupportedError, match="CPU-only"):
        asyncio.run(
            llm.complete_with_images(
                [{"role": "user", "content": "synthetic test"}],
                [{"data": b"image", "mime": "image/png"}],
                provider="nvidia",
                model="remote-model",
            )
        )
