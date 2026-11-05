"""Contract tests for ChronoKG's native OpenCode HTTP v2.1.2 adapter."""
from __future__ import annotations

import asyncio
import base64
import json
import logging

import httpx
import pytest

from app import llm, opencode_provider as opencode


@pytest.fixture()
def configured(monkeypatch):
    from app.db import get_db, init_db

    init_db()
    conn = get_db()
    try:
        conn.execute("DELETE FROM app_settings WHERE key LIKE 'llm_%'")
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", "secret-test-password")
    monkeypatch.setenv("OPENCODE_SERVER_URL", "http://127.0.0.1:4096")
    opencode.save_config("http://127.0.0.1:4096", "/tmp/chronokg-project")
    monkeypatch.setattr(llm, "LLM_PROVIDER", "auto")
    yield


def _patch_http(monkeypatch, handler):
    real_client = httpx.AsyncClient

    def mocked_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(opencode.httpx, "AsyncClient", mocked_client)


def _sse(*events: dict) -> str:
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events)


def _bus_event(event_type: str, session_id: str, **properties) -> dict:
    return {
        "directory": "/tmp/chronokg-project",
        "payload": {
            "type": event_type,
            "properties": {"sessionID": session_id, **properties},
        },
    }


def test_local_only_config_and_public_state_never_expose_password(configured):
    with pytest.raises(ValueError, match="doar localhost"):
        opencode.save_config("http://example.com:4096", "/tmp/project")

    state = opencode.public_config()
    serialized = json.dumps(state)
    assert state["configured"] is True
    assert state["has_password"] is True
    assert state["api_protocol"] == "v2.1.2"
    assert "secret-test-password" not in serialized

    from app.db import get_db

    conn = get_db()
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", [opencode.SETTING_OPENCODE]).fetchone()
        assert "secret-test-password" not in row[0]
        # Older in-development builds persisted this field; the next read strips it.
        legacy = {"base_url": state["base_url"], "directory": state["directory"], "password": "legacy-secret"}
        conn.execute("UPDATE app_settings SET value = ? WHERE key = ?", [json.dumps(legacy), opencode.SETTING_OPENCODE])
        conn.commit()
    finally:
        conn.close()

    opencode.public_config()
    conn = get_db()
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", [opencode.SETTING_OPENCODE]).fetchone()
    finally:
        conn.close()
    assert "legacy-secret" not in row[0]


def test_config_api_rejects_password_input_and_returns_only_environment_presence(configured):
    from fastapi.testclient import TestClient

    from app.db import get_db, init_db
    from app.main import app

    init_db()
    client = TestClient(app)
    rejected = client.post("/api/ai/opencode", json={
        "base_url": "http://127.0.0.1:4096",
        "directory": "/tmp/chronokg-project",
        "password": "must-not-be-sent",
    })
    assert rejected.status_code == 400
    assert "must-not-be-sent" not in rejected.text

    response = client.get("/api/ai/opencode")
    assert response.status_code == 200
    assert response.json()["has_password"] is True
    assert "secret-test-password" not in response.text

    conn = get_db()
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", [opencode.SETTING_OPENCODE]).fetchone()
    finally:
        conn.close()
    assert "secret-test-password" not in row[0]


def test_model_catalogue_reads_native_models_and_server_default(configured, monkeypatch):
    opencode.set_selected_model("openai::gpt-test")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["location[directory]"] == "/tmp/chronokg-project"
        assert request.headers.get("authorization") == "Basic " + base64.b64encode(b"opencode:secret-test-password").decode()
        if request.url.path == "/api/model":
            return httpx.Response(200, json=[
                {"providerID": "openai", "modelID": "gpt-test", "name": "GPT test", "limit": {"context": 32000, "output": 4096}},
                {"providerID": "local", "modelID": "small", "name": "Small", "limit": "invalid-but-ignorable"},
            ])
        if request.url.path == "/api/model/default":
            return httpx.Response(200, json={"providerID": "local", "modelID": "small"})
        return httpx.Response(404)

    _patch_http(monkeypatch, handler)
    catalogue = asyncio.run(opencode.model_catalogue())

    assert catalogue["from_provider"] is True
    assert catalogue["active"] == "openai::gpt-test"
    assert next(model for model in catalogue["models"] if model["id"] == catalogue["active"])["is_active"] is True
    assert {model["id"] for model in catalogue["models"]} == {"openai::gpt-test", "local::small"}


def test_persisted_endpoint_is_revalidated_before_authentication(configured):
    from app.db import get_db

    conn = get_db()
    try:
        conn.execute(
            "UPDATE app_settings SET value = ? WHERE key = ?",
            [json.dumps({"base_url": "https://attacker.example", "directory": "/tmp/other"}), opencode.SETTING_OPENCODE],
        )
        conn.commit()
    finally:
        conn.close()

    assert opencode.is_configured() is False
    assert opencode.public_config()["configured"] is False
    with pytest.raises(opencode.OpenCodeError, match="Configurează adresa"):
        opencode._request_config()


def test_password_is_not_sent_to_an_unpinned_loopback_port(configured):
    from app.db import get_db

    conn = get_db()
    try:
        conn.execute(
            "UPDATE app_settings SET value = ? WHERE key = ?",
            [json.dumps({"base_url": "http://127.0.0.1:4097", "directory": "/tmp/chronokg-project"}), opencode.SETTING_OPENCODE],
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(opencode.OpenCodeError, match="nu corespunde cu OPENCODE_SERVER_URL"):
        opencode._request_config()


def test_basic_auth_requires_pinned_server_url(configured, monkeypatch):
    monkeypatch.delenv("OPENCODE_SERVER_URL")
    with pytest.raises(opencode.OpenCodeError, match="setează și OPENCODE_SERVER_URL"):
        opencode._request_config()


def test_prompt_uses_async_native_session_sse_and_denies_tools(configured, monkeypatch):
    opencode.set_selected_model("openai::gpt-test")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers.get("authorization") == "Basic " + base64.b64encode(b"opencode:secret-test-password").decode()
        path = request.url.path
        if path == "/api/session" and request.method == "POST":
            assert request.url.params["location[directory]"] == "/tmp/chronokg-project"
            assert json.loads(request.content) == {"title": "Bransk response"}
            return httpx.Response(200, json={"id": "session-1"})
        if path == "/api/session/session-1" and request.method == "PATCH":
            assert json.loads(request.content) == {
                "permissions": [{"action": "*", "resource": "*", "effect": "deny"}],
            }
            return httpx.Response(200, json={"id": "session-1"})
        if path == "/api/event" and request.method == "GET":
            body = _sse(
                {"type": "session.text.delta", "properties": {"sessionID": "another-session", "delta": "ignore"}},
                _bus_event("session.text.delta", "session-1", delta="Răspuns "),
                _bus_event("session.text.delta", "session-1", delta="test."),
                _bus_event("session.idle", "session-1"),
            )
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
        if path == "/api/session/session-1/prompt_async" and request.method == "POST":
            body = json.loads(request.content)
            assert body["model"] == {"providerID": "openai", "modelID": "gpt-test"}
            assert body["parts"][0]["type"] == "text"
            assert "--- USER ---\nÎntrebare" in body["parts"][0]["text"]
            assert "Nu executa comenzi și nu modifica fișiere" in body["parts"][0]["text"]
            return httpx.Response(204)
        if path == "/api/session/session-1" and request.method == "DELETE":
            return httpx.Response(200, json=True)
        return httpx.Response(404, json={"path": path, "method": request.method})

    _patch_http(monkeypatch, handler)

    async def collect():
        return [chunk async for chunk in opencode.stream_complete([{"role": "user", "content": "Întrebare"}])]

    chunks = asyncio.run(collect())
    assert chunks == ["Răspuns ", "test."]
    assert [request.method for request in seen] == ["POST", "PATCH", "GET", "POST", "DELETE"]
    assert seen[-1].method == "DELETE"


def test_auth_failure_is_redacted_and_session_is_cleaned_up(configured, monkeypatch, caplog):
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path == "/api/session" and request.method == "POST":
            return httpx.Response(200, json={"id": "session-auth"})
        if request.method == "PATCH":
            return httpx.Response(200, json={"id": "session-auth"})
        if request.url.path == "/api/event":
            return httpx.Response(200, text="", headers={"content-type": "text/event-stream"})
        if request.url.path.endswith("/prompt_async"):
            return httpx.Response(401, json={"error": "secret-test-password"})
        if request.url.path.endswith("/abort"):
            return httpx.Response(200, json=True)
        if request.method == "DELETE":
            return httpx.Response(200, json=True)
        return httpx.Response(404)

    _patch_http(monkeypatch, handler)
    with caplog.at_level(logging.INFO, logger="chronokg.opencode"):
        with pytest.raises(opencode.OpenCodeError, match="HTTP 401") as exc:
            asyncio.run(opencode.complete([{"role": "user", "content": "hi"}]))

    assert "secret-test-password" not in str(exc.value)
    assert "secret-test-password" not in caplog.text
    assert ("DELETE", "/api/session/session-auth") in seen


@pytest.mark.parametrize("event_type, message", [
    ("session.execution.failed", "a eșuat"),
    ("session.execution.interrupted", "a fost întreruptă"),
])
def test_terminal_execution_errors_cleanup_session(configured, monkeypatch, event_type, message):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/session" and request.method == "POST":
            return httpx.Response(200, json={"id": "session-terminal"})
        if request.method == "PATCH":
            return httpx.Response(200, json={"id": "session-terminal"})
        if request.url.path == "/api/event":
            return httpx.Response(200, text=_sse(_bus_event(event_type, "session-terminal")), headers={"content-type": "text/event-stream"})
        if request.url.path.endswith("/prompt_async"):
            return httpx.Response(204)
        if request.method == "DELETE":
            return httpx.Response(200, json=True)
        return httpx.Response(404)

    _patch_http(monkeypatch, handler)
    with pytest.raises(opencode.OpenCodeError, match=message):
        asyncio.run(opencode.complete([{"role": "user", "content": "hi"}]))


def test_timeout_cancels_event_wait_and_cleans_session(configured, monkeypatch):
    deleted = False
    aborted = False

    class HangingEvents(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b": keepalive\n\n"
            await asyncio.sleep(2)

        async def aclose(self):
            return None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal deleted, aborted
        if request.url.path == "/api/session" and request.method == "POST":
            return httpx.Response(200, json={"id": "session-timeout"})
        if request.method == "PATCH":
            return httpx.Response(200, json={"id": "session-timeout"})
        if request.url.path == "/api/event":
            return httpx.Response(200, stream=HangingEvents(), headers={"content-type": "text/event-stream"})
        if request.url.path.endswith("/prompt_async"):
            return httpx.Response(204)
        if request.url.path.endswith("/abort"):
            aborted = True
            return httpx.Response(200, json=True)
        if request.method == "DELETE":
            deleted = True
            return httpx.Response(200, json=True)
        return httpx.Response(404)

    _patch_http(monkeypatch, handler)
    with pytest.raises(opencode.OpenCodeError, match="depășit limita de timp"):
        asyncio.run(opencode.complete([{"role": "user", "content": "hi"}], timeout=0.03))
    assert aborted is True
    assert deleted is True


def test_cancelled_consumer_still_cleans_session(configured, monkeypatch):
    reading = asyncio.Event()
    deleted = False
    aborted = False

    class HangingEvents(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b": keepalive\n\n"
            reading.set()
            await asyncio.Event().wait()

        async def aclose(self):
            return None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal deleted, aborted
        if request.url.path == "/api/session" and request.method == "POST":
            return httpx.Response(200, json={"id": "session-cancel"})
        if request.method == "PATCH":
            return httpx.Response(200, json={"id": "session-cancel"})
        if request.url.path == "/api/event":
            return httpx.Response(200, stream=HangingEvents(), headers={"content-type": "text/event-stream"})
        if request.url.path.endswith("/prompt_async"):
            return httpx.Response(204)
        if request.url.path.endswith("/abort"):
            aborted = True
            return httpx.Response(200, json=True)
        if request.method == "DELETE":
            deleted = True
            return httpx.Response(200, json=True)
        return httpx.Response(404)

    _patch_http(monkeypatch, handler)

    async def cancel_consumer():
        async def consume():
            async for _chunk in opencode.stream_complete([{"role": "user", "content": "hi"}]):
                pass

        task = asyncio.create_task(consume())
        await reading.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_consumer())
    assert aborted is True
    assert deleted is True


def test_opencode_failure_falls_through_to_another_provider(configured, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setenv("LLM_PROVIDERS", "openai")
    llm.set_active_provider("opencode")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        if request.url.path == "/api/session":
            return httpx.Response(503)
        assert request.url.host == "api.openai.com"
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "fallback worked"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2},
        })

    _patch_http(monkeypatch, handler)
    provider, _model, text = asyncio.run(llm.complete([{"role": "user", "content": "hi"}]))

    assert provider == "openai"
    assert text == "fallback worked"
    assert "127.0.0.1" in seen and "api.openai.com" in seen
