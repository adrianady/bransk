"""Contract and secret-handling tests for OpenCode Console inference."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app import llm, opencode_console_provider as console


API_KEY = "oc_test_secret_key_123"
MODEL_LIST = {
    "data": [
        {
            "id": "big-pickle",
            "name": "Big Pickle",
            "type": "chat",
            "modalities": {"input": ["text"], "output": ["text"]},
            "cost": {"text": {"input": {"price": 0}, "output": {"price": 0}}},
        },
        {
            "id": "beam-free",
            "name": "Beam Free",
            "type": "chat",
            "modalities": {"input": ["text"], "output": ["text"]},
            "cost": {"text": {"input": {"price": 0}, "output": {"price": 0}}},
        },
        {
            "id": "experimental-candidate",
            "name": "Experimental Candidate",
            "type": "chat",
            "modalities": {"input": ["text"], "output": ["text"]},
            "cost": {"text": {"input": {"price": 0}, "output": {"price": 0}}},
        },
        {
            "id": "console/text-small",
            "name": "Text Small",
            "type": "chat",
            "modalities": {"input": ["text"], "output": ["text"]},
            "limit": {"context": 32000, "output": 4096},
            "capabilities": {"reasoning": {"effort": ["low", "high"]}},
            "cost": {"text": {"input": {"price": 0.1}, "output": {"price": 0.2}}},
        },
        {
            "id": "console/vision-only",
            "type": "chat",
            "modalities": {"input": ["image"], "output": ["text"]},
        },
        {"id": "console/embed", "type": "embedding"},
    ],
}


@pytest.fixture()
def clean_settings():
    from app.db import get_db, init_db

    init_db()
    conn = get_db()
    try:
        conn.execute("DELETE FROM app_settings WHERE key LIKE 'llm_%'")
        conn.execute("DELETE FROM app_settings WHERE key = 'deepseek_model'")
        conn.commit()
    finally:
        conn.close()
    yield


def _patch_http(monkeypatch, handler):
    real_client = httpx.AsyncClient

    def mocked_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(console.httpx, "AsyncClient", mocked_client)


def test_console_url_validation_restricts_remote_and_local_targets():
    assert console._normalize_api_url("https://opencode.ai") == console.DEFAULT_API_URL
    assert console._normalize_api_url("https://opencode.ai/inference/") == console.DEFAULT_API_URL
    assert console._normalize_api_url(
        "https://opencode.ai/inference/go/", service="go"
    ) == console.GO_API_URL
    assert console._normalize_api_url("http://127.0.0.1:8787/inference") == "http://127.0.0.1:8787/inference"
    assert console._normalize_api_url("http://[::1]:8787") == "http://[::1]:8787"

    for value in (
        "http://opencode.ai/inference",
        "https://example.com/inference",
        "http://192.168.1.20:8080",
        "http://169.254.169.254/latest/meta-data",
        "https://user:password@opencode.ai/inference",
        "https://opencode.ai/inference?token=secret",
        "https://opencode.ai/inference#models",
        "https://opencode.ai/v1",
        "https://opencode.ai/inference/other",
        "https://opencode.ai:444/inference",
        "http://localhost/a/%2e%2e/admin",
        "http://127.0.0.1:invalid",
    ):
        with pytest.raises(ValueError):
            console._normalize_api_url(value)

    # Console and Go are separate services: a Console setting cannot point at Go.
    with pytest.raises(ValueError, match="Go"):
        console._normalize_api_url("https://opencode.ai/inference/go")
    with pytest.raises(ValueError):
        console._normalize_api_url("https://opencode.ai/inference", service="go")


def test_go_endpoint_uses_a_persistent_console_session_id(clean_settings):
    go_url = console.GO_API_URL
    console.save_config(go_url, API_KEY, service="go")

    url, headers = console.request_details(service="go")
    repeated_url, repeated_headers = console.request_details(service="go")

    assert url == f"{go_url}/openai/v1/chat/completions"
    assert repeated_url == url
    assert headers["Authorization"] == f"Bearer {API_KEY}"
    assert headers["x-opencode-session"].startswith("ses_")
    assert repeated_headers["x-opencode-session"] == headers["x-opencode-session"]
    assert API_KEY not in headers["x-opencode-session"]
    assert console.request_details(models=True, service="go")[0] == f"{go_url}/v1/models"

    # Switching the account/base resets the session without forwarding the old key.
    console.save_config(go_url, "replacement_key", service="go")
    assert console.request_details(service="go")[1]["x-opencode-session"] != headers["x-opencode-session"]


def test_saved_key_is_redacted_and_cleared_when_origin_changes(clean_settings):
    saved = console.save_config(console.DEFAULT_API_URL, API_KEY)
    assert saved == {
        "configured": True,
        "base_url": console.DEFAULT_API_URL,
        "has_key": True,
    }
    assert API_KEY not in json.dumps(console.public_config())
    assert console.api_key() == API_KEY

    # The old Bearer key must not silently move to a different host/port.
    changed = console.save_config("http://127.0.0.1:8787/inference")
    assert changed["has_key"] is False
    assert console.api_key() == ""

    console.save_config("http://127.0.0.1:8787/inference", "local_test_key")
    cleared = console.save_config("http://127.0.0.1:8787/inference", clear_key=True)
    assert cleared["configured"] is False
    assert console.api_key() == ""


def test_configuration_routes_never_echo_the_key(clean_settings):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        response = client.post("/api/ai/opencode-console", json={
            "base_url": console.DEFAULT_API_URL,
            "api_key": API_KEY,
        })
        assert response.status_code == 200
        assert response.json()["has_key"] is True
        assert API_KEY not in response.text

        state = client.get("/api/ai/opencode-console")
        assert state.status_code == 200
        assert state.json()["configured"] is True
        assert state.json()["base_url"] == console.DEFAULT_API_URL
        assert API_KEY not in state.text

        invalid = client.post("/api/ai/opencode-console", json={
            "base_url": "https://example.com/inference",
            "api_key": API_KEY,
        })
        assert invalid.status_code == 400
        assert API_KEY not in invalid.text

        oversized_key = "K" * 501
        too_long = client.post("/api/ai/opencode-console", json={
            "base_url": console.DEFAULT_API_URL,
            "api_key": oversized_key,
        })
        assert too_long.status_code == 400
        assert oversized_key not in too_long.text

        wrong_type = client.post("/api/ai/opencode-console", json={
            "base_url": console.DEFAULT_API_URL,
            "api_key": [API_KEY],
        })
        assert wrong_type.status_code == 400
        assert API_KEY not in wrong_type.text

        oversized_body_secret = "X" * 2500
        oversized_body = client.post(
            "/api/ai/opencode-console",
            content=(
                '{"base_url":"https://opencode.ai/inference","api_key":"'
                + oversized_body_secret
                + '"}'
            ),
            headers={"content-type": "application/json"},
        )
        assert oversized_body.status_code == 413
        assert oversized_body_secret not in oversized_body.text


def test_console_configuration_routes_obey_the_optional_shared_token(clean_settings, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setenv("CHRONOKG_AUTH_TOKEN", "app-token-test")
    with TestClient(app) as client:
        rejected = client.post("/api/ai/opencode-console", json={
            "base_url": console.DEFAULT_API_URL,
            "api_key": API_KEY,
        })
        assert rejected.status_code == 401
        assert API_KEY not in rejected.text

        accepted = client.post(
            "/api/ai/opencode-console",
            json={"base_url": console.DEFAULT_API_URL, "api_key": API_KEY},
            headers={"Authorization": "Bearer app-token-test"},
        )
        assert accepted.status_code == 200
        assert API_KEY not in accepted.text


def test_go_configuration_route_keeps_its_own_key(clean_settings):
    from fastapi.testclient import TestClient

    from app.main import app

    console.save_config(console.DEFAULT_API_URL, "zen_key")

    with TestClient(app) as client:
        saved = client.post("/api/ai/opencode-go", json={
            "base_url": console.GO_API_URL,
            "api_key": "go_key",
        })
        assert saved.status_code == 200
        body = saved.json()
        assert body["configured"] is True
        assert body["base_url"] == console.GO_API_URL
        assert "go_key" not in saved.text

        go_state = client.get("/api/ai/opencode-go").json()
        console_state = client.get("/api/ai/opencode-console").json()
        assert go_state["base_url"] == console.GO_API_URL
        assert console_state["base_url"] == console.DEFAULT_API_URL
        assert console.api_key() == "zen_key"
        assert console.api_key(service="go") == "go_key"

        wrong_path = client.post("/api/ai/opencode-go", json={
            "base_url": console.DEFAULT_API_URL,
            "api_key": "go_key",
        })
        assert wrong_path.status_code == 400
        assert "inference/go" in wrong_path.text


def test_model_catalogue_uses_bearer_and_filters_to_text_chat_models(clean_settings, monkeypatch):
    console.save_config(console.DEFAULT_API_URL, API_KEY)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "GET"
        assert str(request.url) == f"{console.DEFAULT_API_URL}/v1/models"
        assert request.headers.get("authorization") == f"Bearer {API_KEY}"
        return httpx.Response(200, json=MODEL_LIST)

    _patch_http(monkeypatch, handler)
    result = asyncio.run(console.model_catalogue())

    assert [model["id"] for model in result["models"]] == ["console/text-small"]
    assert result["models"][0]["context_window"] == 32000
    assert result["models"][0]["effort_levels"] == ["low", "high"]
    assert result["free_model_ids"] == ["big-pickle", "beam-free", "experimental-candidate"]
    assert len(seen) == 1


def test_go_catalogue_keeps_free_models_and_uses_the_go_endpoint(clean_settings, monkeypatch):
    console.save_config(console.GO_API_URL, API_KEY, service="go")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert str(request.url) == f"{console.GO_API_URL}/v1/models"
        assert request.headers.get("authorization") == f"Bearer {API_KEY}"
        return httpx.Response(200, json=MODEL_LIST)

    _patch_http(monkeypatch, handler)
    result = asyncio.run(console.model_catalogue(service="go"))

    ids = [model["id"] for model in result["models"]]
    assert "beam-free" in ids
    assert "big-pickle" in ids
    assert "console/text-small" in ids
    assert result["blocked_free_model_ids"] == []
    assert set(result["free_model_ids"]) == {"big-pickle", "beam-free", "experimental-candidate"}
    assert [model["id"] for model in result["models"] if model["free"]] == [
        "big-pickle", "beam-free", "experimental-candidate",
    ]
    assert len(seen) == 1


def test_go_and_console_configs_stay_separate(clean_settings):
    console.save_config(console.DEFAULT_API_URL, "zen_key")
    console.save_config(console.GO_API_URL, "go_key", service="go")

    assert console.api_key() == "zen_key"
    assert console.api_key(service="go") == "go_key"
    assert console.public_config()["base_url"] == console.DEFAULT_API_URL
    assert console.public_config(service="go")["base_url"] == console.GO_API_URL
    assert console.public_config()["has_key"] and console.public_config(service="go")["has_key"]


def test_go_chat_allows_free_models_through_the_go_path(clean_settings, monkeypatch):
    console.save_config(console.GO_API_URL, API_KEY, service="go")
    llm.set_active_provider("opencode_go")
    llm.set_active_model("beam-free", provider="opencode_go")
    monkeypatch.setattr(llm, "LLM_PROVIDER", "auto")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.path == "/inference/go/openai/v1/chat/completions"
        assert request.headers.get("authorization") == f"Bearer {API_KEY}"
        assert request.headers.get("x-opencode-session", "").startswith("ses_")
        body = json.loads(request.content)
        assert body["model"] == "beam-free"
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "Răspuns Go"}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3},
        })

    _patch_http(monkeypatch, handler)

    provider, model, text = asyncio.run(llm.complete([{"role": "user", "content": "Salut"}]))
    assert (provider, model, text) == ("opencode_go", "beam-free", "Răspuns Go")
    assert len(seen) == 1


def test_go_activation_accepts_a_free_model(clean_settings, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    console.save_config(console.GO_API_URL, API_KEY, service="go")
    _patch_http(monkeypatch, lambda _request: httpx.Response(200, json=MODEL_LIST))

    with TestClient(app) as client:
        models = client.get("/api/ai/models?provider=opencode_go")
        assert models.status_code == 200
        assert "beam-free" in [model["id"] for model in models.json()["models"]]

        selected = client.post("/api/ai/model", json={
            "provider": "opencode_go",
            "model_id": "beam-free",
        })
        assert selected.status_code == 200
        assert selected.json()["active"] == "beam-free"

        active = client.post("/api/ai/provider", json={"provider": "opencode_go"})
        assert active.status_code == 200
        assert active.json()["active"] == "opencode_go"
        assert next(p for p in client.get("/api/ai/providers").json()["providers"]
                    if p["id"] == "opencode_go")["configured"] is True


def test_go_402_explains_the_subscription(clean_settings):
    with pytest.raises(console.OpenCodeConsoleError, match="abonamentul Go") as error:
        console.validate_response(httpx.Response(402), service="go")

    assert "Extra Usage" in str(error.value)
    assert error.value.no_fallback is True


def test_console_model_route_and_activation_require_a_saved_model(clean_settings, monkeypatch):
    from fastapi.testclient import TestClient

    from app import llm
    from app.main import app

    console.save_config(console.DEFAULT_API_URL, API_KEY)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") == f"Bearer {API_KEY}"
        return httpx.Response(200, json=MODEL_LIST)

    _patch_http(monkeypatch, handler)
    with TestClient(app) as client:
        models = client.get("/api/ai/models?provider=opencode_console")
        assert models.status_code == 200
        assert [model["id"] for model in models.json()["models"]] == ["console/text-small"]

        before_model = client.post("/api/ai/provider", json={"provider": "opencode_console"})
        assert before_model.status_code == 409

        llm.set_active_model("experimental-candidate", provider="opencode_console")
        blocked_dynamic_free_activation = client.post("/api/ai/provider", json={"provider": "opencode_console"})
        assert blocked_dynamic_free_activation.status_code == 409
        assert "experimental-candidate" in blocked_dynamic_free_activation.text

        blocked_dynamic_free_model = client.post("/api/ai/model", json={
            "provider": "opencode_console",
            "model_id": "experimental-candidate",
        })
        assert blocked_dynamic_free_model.status_code == 400
        assert "experimental-candidate" in blocked_dynamic_free_model.text

        llm.set_active_model("big-pickle", provider="opencode_console")
        blocked_free_activation = client.post("/api/ai/provider", json={"provider": "opencode_console"})
        assert blocked_free_activation.status_code == 409
        assert "gratuit" in blocked_free_activation.text.lower()
        assert "OpenCode local" in blocked_free_activation.text

        blocked_free_model = client.post("/api/ai/model", json={
            "provider": "opencode_console",
            "model_id": "big-pickle",
        })
        assert blocked_free_model.status_code == 400

        selected = client.post("/api/ai/model", json={
            "provider": "opencode_console",
            "model_id": "console/text-small",
        })
        assert selected.status_code == 200
        assert selected.json()["active"] == "console/text-small"

        active = client.post("/api/ai/provider", json={"provider": "opencode_console"})
        assert active.status_code == 200
        assert active.json()["active"] == "opencode_console"
        assert next(p for p in client.get("/api/ai/providers").json()["providers"]
                    if p["id"] == "opencode_console")["configured"] is True


def test_nonstreaming_and_streaming_chat_use_console_bearer_without_redirects(clean_settings, monkeypatch):
    console.save_config(console.DEFAULT_API_URL, API_KEY)
    llm.set_active_provider("opencode_console")
    llm.set_active_model("console/text-small", provider="opencode_console")
    monkeypatch.setattr(llm, "LLM_PROVIDER", "auto")

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.path == "/inference/openai/v1/chat/completions"
        assert request.headers.get("authorization") == f"Bearer {API_KEY}"
        assert request.headers.get("x-opencode-session", "").startswith("ses_")
        body = json.loads(request.content)
        assert body["model"] == "console/text-small"
        if body["stream"]:
            return httpx.Response(
                200,
                text=(
                    'data: {"choices":[{"delta":{"content":"Răspuns "}}]}\n\n'
                    'data: {"choices":[{"delta":{"content":"stream"}}]}\n\n'
                    "data: [DONE]\n\n"
                ),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "Răspuns complet"}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3},
        })

    real_client = httpx.AsyncClient

    def mocked_client(*args, **kwargs):
        assert kwargs.get("follow_redirects") is False
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(llm.httpx, "AsyncClient", mocked_client)

    provider, model, text = asyncio.run(llm.complete([{"role": "user", "content": "Salut"}]))
    assert (provider, model, text) == ("opencode_console", "console/text-small", "Răspuns complet")

    async def collect_stream():
        return [chunk async for chunk in llm.stream_complete([{"role": "user", "content": "Salut"}])]

    chunks = asyncio.run(collect_stream())
    assert [chunk[2] for chunk in chunks] == ["Răspuns ", "stream"]
    assert all(chunk[:2] == ("opencode_console", "console/text-small") for chunk in chunks)
    assert seen[0].headers["x-opencode-session"] == seen[1].headers["x-opencode-session"]
    assert len(seen) == 2


def test_model_redirect_is_not_followed_or_exposed(clean_settings, monkeypatch):
    console.save_config(console.DEFAULT_API_URL, API_KEY)
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://attacker.example/collect"})

    _patch_http(monkeypatch, handler)
    with pytest.raises(console.OpenCodeConsoleError, match="redirect"):
        asyncio.run(console.model_catalogue())
    assert len(requests) == 1
    assert API_KEY not in str(requests[0].url)


def _enable_console_chat(model_id: str, monkeypatch):
    from app import llm
    import app.chat as chat_router

    console.save_config(console.DEFAULT_API_URL, API_KEY)
    llm.set_active_model(model_id, provider="opencode_console")
    llm.set_active_provider("opencode_console")
    monkeypatch.setattr(llm, "LLM_PROVIDER", "auto")
    monkeypatch.setattr(llm, "DEEPSEEK_API_KEY", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setenv("LLM_PROVIDERS", "")
    monkeypatch.setattr(chat_router, "LLM_PROVIDER", "auto")
    monkeypatch.setattr(chat_router, "_build_context", lambda _message: [])


def test_chat_stream_routes_through_console_provider(clean_settings, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    _enable_console_chat("console/text-small", monkeypatch)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "POST"
        assert request.url.path == "/inference/openai/v1/chat/completions"
        assert request.headers.get("authorization") == f"Bearer {API_KEY}"
        assert request.headers.get("x-opencode-session", "").startswith("ses_")
        body = json.loads(request.content)
        assert body["model"] == "console/text-small"
        assert body["stream"] is True
        return httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"content":"Răspuns "}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"Console"}}]}\n\n'
                "data: [DONE]\n\n"
            ),
            headers={"content-type": "text/event-stream"},
        )

    _patch_http(monkeypatch, handler)
    with TestClient(app) as client:
        response = client.post("/api/chat/stream", json={"message": "Salut", "history": []})

    assert response.status_code == 200
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: {")
    ]
    assert any(event.get("provider") == "opencode_console" for event in events)
    assert any(event.get("model") == "console/text-small" for event in events)
    assert [event["content"] for event in events if event.get("type") == "chunk"] == [
        "Răspuns ", "Console",
    ]
    assert len(seen) == 1


def test_chat_stream_blocks_console_free_model_before_external_request(clean_settings, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    _enable_console_chat("big-pickle", monkeypatch)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "unexpected request"}}],
        })

    _patch_http(monkeypatch, handler)
    with TestClient(app) as client:
        response = client.post("/api/chat/stream", json={"message": "Salut", "history": []})

    assert response.status_code == 200
    assert "gratuit" in response.text.lower()
    assert "OpenCode local" in response.text
    assert "pay-as-you-go" in response.text.lower()
    assert len(seen) == 0


def test_nonstream_console_free_model_is_blocked_before_external_request(clean_settings, monkeypatch):
    console.save_config(console.DEFAULT_API_URL, API_KEY)
    llm.set_active_model("big-pickle", provider="opencode_console")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "unexpected request"}}],
        })

    _patch_http(monkeypatch, handler)
    with pytest.raises(console.OpenCodeConsoleError, match="Big Pickle"):
        asyncio.run(llm._call_openai_compatible(
            "opencode_console", [{"role": "user", "content": "Salut"}],
            temperature=0, json_mode=False, max_tokens=8,
        ))

    assert seen == []


def test_console_free_model_refusal_does_not_fall_back(clean_settings, monkeypatch):
    _enable_console_chat("big-pickle", monkeypatch)
    monkeypatch.setattr(llm, "provider_order", lambda: ["deepseek"])
    monkeypatch.setattr(llm, "_provider_is_configured", lambda _provider: True)

    calls = []
    original_call = llm._call_provider

    async def tracked_call(provider, messages, temperature, json_mode, max_tokens):
        calls.append(provider)
        return await original_call(provider, messages, temperature, json_mode, max_tokens)

    monkeypatch.setattr(llm, "_call_provider", tracked_call)
    with pytest.raises(llm.LLMProviderError, match="gratuit"):
        asyncio.run(llm.complete([{"role": "user", "content": "Salut"}]))
    assert calls == ["opencode_console"]

    calls.clear()
    original_stream = llm._stream_provider

    async def tracked_stream(provider, messages, temperature, max_tokens):
        calls.append(provider)
        async for chunk in original_stream(provider, messages, temperature, max_tokens):
            yield chunk

    monkeypatch.setattr(llm, "_stream_provider", tracked_stream)

    async def collect_stream():
        return [chunk async for chunk in llm.stream_complete([{"role": "user", "content": "Salut"}])]

    with pytest.raises(llm.LLMProviderError, match="gratuit"):
        asyncio.run(collect_stream())
    assert calls == ["opencode_console"]


def test_console_429_is_actionable_and_does_not_fall_back(clean_settings, monkeypatch):
    _enable_console_chat("console/text-small", monkeypatch)
    monkeypatch.setattr(llm, "provider_order", lambda: ["deepseek"])
    monkeypatch.setattr(llm, "_provider_is_configured", lambda _provider: True)

    calls = []
    original_call = llm._call_provider

    async def tracked_call(provider, messages, temperature, json_mode, max_tokens):
        calls.append(provider)
        return await original_call(provider, messages, temperature, json_mode, max_tokens)

    monkeypatch.setattr(llm, "_call_provider", tracked_call)
    _patch_http(monkeypatch, lambda _request: httpx.Response(429, json={"error": "private upstream detail"}))

    with pytest.raises(llm.LLMProviderError, match="limita de rată") as error:
        asyncio.run(llm.complete([{"role": "user", "content": "Salut"}]))

    assert "private upstream detail" not in str(error.value)
    assert calls == ["opencode_console"]


def test_console_402_is_actionable_and_does_not_fall_back(clean_settings, monkeypatch):
    _enable_console_chat("deepseek-v4.1-flash", monkeypatch)
    monkeypatch.setattr(llm, "provider_order", lambda: ["deepseek"])
    monkeypatch.setattr(llm, "_provider_is_configured", lambda _provider: True)

    calls = []
    original_call = llm._call_provider

    async def tracked_call(provider, messages, temperature, json_mode, max_tokens):
        calls.append(provider)
        return await original_call(provider, messages, temperature, json_mode, max_tokens)

    monkeypatch.setattr(llm, "_call_provider", tracked_call)
    _patch_http(monkeypatch, lambda _request: httpx.Response(402, json={"error": "private billing detail"}))

    with pytest.raises(llm.LLMProviderError, match="HTTP 402") as error:
        asyncio.run(llm.complete([{"role": "user", "content": "Reply OK"}]))

    assert "facturare" in str(error.value).lower()
    assert "private billing detail" not in str(error.value)
    assert calls == ["opencode_console"]


def test_console_429_error_is_actionable(clean_settings):
    with pytest.raises(console.OpenCodeConsoleError, match="HTTP 429") as error:
        console.validate_response(httpx.Response(429), model_id="big-pickle")

    assert "Big Pickle" in str(error.value)
    assert "OpenCode local" in str(error.value)

    with pytest.raises(console.OpenCodeConsoleError, match="limita de rată"):
        console.validate_response(httpx.Response(429))
