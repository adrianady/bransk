"""Audit-driven chat AI tests: structured-output safety, tool execution wiring,
and provider retry behavior."""
import asyncio

import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.routers import chat as chat_mod
from app import llm as llm_mod


def _parse(reply: str) -> list[dict]:
    return chat_mod._parse_actions(reply)


# ── Structured-output safety ───────────────────────────────────────────────

def test_tags_inside_code_fences_and_backticks_never_execute():
    """Regression: a model showing usage examples in markdown code used to
    trigger real DB writes (created entries, archived entries)."""
    with TestClient(app) as client:
        entry = client.post("/api/entries", json={"title": "Keep me", "content": "x"}).json()
        reply = (
            "Example of saving an entry:\n"
            "```\n[CREATE_ENTRY: Example Note | content | 2026-08-02 | demo | 3]\n```\n"
            "and inline: `[ARCHIVE_ENTRY: %d]`\n"
            "[SEARCH_KB: keep]"
        ) % entry["id"]
        acts = _parse(reply)
        types = {a["type"] for a in acts}
        assert "entry_created" not in types
        assert "entry_archived" not in types
        assert "search_results" in types  # real tag outside code still runs

        rows = client.get("/api/entries").json()
        assert all(e["title"] != "Example Note" for e in rows)
        assert client.get(f"/api/entries/{entry['id']}").json()["archived"] is False


def test_near_miss_create_entry_returns_corrective_error():
    """A CREATE_ENTRY with a non-numeric importance must not fail silently."""
    acts = _parse("[CREATE_ENTRY: My note | content | 2026-08-02 | demo | 5 stars]")
    assert any(a["type"] == "error" and "CREATE_ENTRY" in a.get("detail", "") for a in acts)


def test_strict_and_near_miss_tags_are_both_handled():
    with TestClient(app) as client:
        acts = _parse(
            "[CREATE_ENTRY: Good one | content | 2026-08-02 | demo | 3]\n"
            "[CREATE_ENTRY: Broken one | content | 2026-08-02 | demo | high]"
        )
        types = {a["type"] for a in acts}
        assert "entry_created" in types
        assert any(a["type"] == "error" and "CREATE_ENTRY" in a.get("detail", "") for a in acts)


# ── Endpoint wiring with a structured provider reply ───────────────────────

def test_chat_endpoint_executes_structured_actions(monkeypatch):
    async def fake_complete(messages, **kwargs):
        return ("fake", "fake-model", "[CREATE_ENTRY: From AI | ai content | 2026-08-02 | audit | 4]")

    monkeypatch.setattr(chat_mod, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(chat_mod, "llm_complete", fake_complete)

    with TestClient(app) as client:
        resp = client.post("/api/chat", json={"message": "save this", "history": []})
        assert resp.status_code == 200
        body = resp.json()
        assert body["provider"] == "fake"
        assert any(a["type"] == "entry_created" and a["title"] == "From AI" for a in body["actions"])
        created = [e for e in client.get("/api/entries").json() if e["title"] == "From AI"]
        assert len(created) == 1


def test_chat_endpoint_surfaces_error_for_bad_action(monkeypatch):
    async def fake_complete(messages, **kwargs):
        return ("fake", "fake-model", "[ADD_TODO: 1 | very-important | 2026-08-15]")

    monkeypatch.setattr(chat_mod, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(chat_mod, "llm_complete", fake_complete)

    with TestClient(app) as client:
        resp = client.post("/api/chat", json={"message": "add a task", "history": []})
        assert resp.status_code == 200
        errors = [a for a in resp.json()["actions"] if a["type"] == "error"]
        assert errors and "ADD_TODO" in errors[0]["detail"]


# ── Provider retry behavior ────────────────────────────────────────────────

def test_with_retry_retries_transient_failures(monkeypatch):
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("boom")
        return "ok"

    result = asyncio.run(llm_mod._with_retry(flaky, attempts=3, base_delay=0.0))
    assert result == "ok"
    assert calls["n"] == 3


def test_with_retry_does_not_retry_client_errors(monkeypatch):
    calls = {"n": 0}

    async def bad_request():
        calls["n"] += 1
        raise httpx.HTTPStatusError("400", request=httpx.Request("POST", "http://x"), response=httpx.Response(400))

    try:
        asyncio.run(llm_mod._with_retry(bad_request, attempts=3, base_delay=0.0))
        raise AssertionError("expected HTTPStatusError")
    except httpx.HTTPStatusError:
        pass
    assert calls["n"] == 1


# ── Remaining-findings fixes ───────────────────────────────────────────────

def test_archive_entry_requires_confirmation(monkeypatch):
    """Regression: [ARCHIVE_ENTRY: id] must not execute until confirmed."""
    with TestClient(app) as client:
        entry = client.post("/api/entries", json={"title": "Keep", "content": "x"}).json()

        async def fake_complete(messages, **kwargs):
            return ("fake", "fake-model", f"[ARCHIVE_ENTRY: {entry['id']}]")

        monkeypatch.setattr(chat_mod, "LLM_PROVIDER", "ollama")
        monkeypatch.setattr(chat_mod, "llm_complete", fake_complete)

        resp = client.post("/api/chat", json={"message": "archive it", "history": []})
        assert resp.status_code == 200
        pending = [a for a in resp.json()["actions"] if a["type"] == "pending_confirm"]
        assert len(pending) == 1
        assert client.get(f"/api/entries/{entry['id']}").json()["archived"] is False

        confirm_id = pending[0]["confirm_id"]
        approved = client.post("/api/chat/confirm", json={"confirm_id": confirm_id, "approve": True})
        assert approved.status_code == 200
        assert approved.json()["status"] == "executed"
        assert client.get(f"/api/entries/{entry['id']}").json()["archived"] is True


def test_archive_confirmation_can_be_rejected():
    from app.routers.chat import _parse_actions, _PENDING_ACTIONS

    with TestClient(app) as client:
        entry = client.post("/api/entries", json={"title": "Keep 2", "content": "x"}).json()
        acts = _parse(f"[ARCHIVE_ENTRY: {entry['id']}]")
        pending = next(a for a in acts if a["type"] == "pending_confirm")
        resp = client.post("/api/chat/confirm", json={"confirm_id": pending["confirm_id"], "approve": False})
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"
        assert client.get(f"/api/entries/{entry['id']}").json()["archived"] is False


def test_expired_confirmation_returns_404():
    resp = TestClient(app).post("/api/chat/confirm", json={"confirm_id": "deadbeef00000000", "approve": True})
    assert resp.status_code == 404


def test_duplicate_identical_tags_execute_once():
    with TestClient(app) as client:
        acts = _parse(
            "[CREATE_ENTRY: Only Once | x | 2026-08-02 | dedupe | 3]\n"
            "[CREATE_ENTRY: Only Once | x | 2026-08-02 | dedupe | 3]"
        )
        created = [a for a in acts if a["type"] == "entry_created"]
        assert len(created) == 1
        rows = client.get("/api/entries").json()
        assert sum(1 for e in rows if e["title"] == "Only Once") == 1


def test_search_query_with_brackets_parses_fully():
    with TestClient(app) as client:
        client.post(
            "/api/entries",
            json={"title": "Brain regions", "content": "brain [regions] and memory connections", "tags": "neuro"},
        )
        acts = _parse("[SEARCH_KB: brain [regions] and memory]")
        result = next(a for a in acts if a["type"] == "search_results")
        assert result["query"] == "brain [regions] and memory"
        assert result["results"]


def test_echoed_tag_in_prose_does_not_execute():
    with TestClient(app) as client:
        entry = client.post("/api/entries", json={"title": "Safe", "content": "x"}).json()
        acts = _parse(f"You asked: [ARCHIVE_ENTRY: {entry['id']}] — done.")
        assert not any(a["type"] in ("pending_confirm", "entry_archived") for a in acts)
        assert client.get(f"/api/entries/{entry['id']}").json()["archived"] is False


def test_reminder_with_invalid_trigger_returns_error():
    with TestClient(app) as client:
        entry = client.post("/api/entries", json={"title": "E", "content": "c"}).json()
        acts = _parse(f"[ADD_REMINDER: {entry['id']} | Call | not-a-date | once]")
        errors = [a for a in acts if a["type"] == "error"]
        assert errors and "ADD_REMINDER" in errors[0]["detail"]
        assert client.get(f"/api/entries/{entry['id']}/reminders").json() == []


def test_stream_failover_does_not_duplicate_partial_output(monkeypatch):
    """If DeepSeek fails after yielding tokens, the stream must raise instead
    of restarting from Ollama (which duplicated the prefix)."""
    async def fake_deepseek(messages, temperature, max_tokens):
        yield "partial "
        raise RuntimeError("mid-stream failure")

    async def fake_ollama(messages, temperature, max_tokens):
        yield "ollama"

    monkeypatch.setattr(llm_mod, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(llm_mod, "DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(llm_mod, "_stream_deepseek", fake_deepseek)
    monkeypatch.setattr(llm_mod, "_stream_ollama", fake_ollama)

    async def run():
        chunks = []
        try:
            async for piece in llm_mod.stream_complete([{"role": "user", "content": "hi"}]):
                chunks.append(piece[2])
        except RuntimeError:
            return chunks, True
        return chunks, False

    chunks, raised = asyncio.run(run())
    assert raised is True
    assert chunks == ["partial "]
