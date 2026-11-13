"""The chat must use the context window the model actually has.

Before this the context was capped twice: `_build_context` kept 10 entries at
500 characters each, then `_build_system_prompt` cut every entry again to 300.
With a 1M-token model that is a fraction of a percent of the window, and the
model answered from a summary of a summary.

These tests pin the new contract: the budget comes from the active model, it is
respected, an unknown model falls back conservatively, and nothing is ever cut
to less than it was before when the window is large.
"""
import pytest

from app import llm
import app.chat as chat
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setenv("CHRONOKG_DB_PATH", str(db))
    import app.db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", db)
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def clear_catalogue_cache(monkeypatch):
    monkeypatch.setattr(llm, "_catalogue_cache", {"at": 0.0, "models": {}})
    # The resolved window is cached per model name; tests swap catalogues, so
    # every test starts with an empty window cache too.
    llm._context_window_cache.clear()


def test_window_is_read_from_the_provider(monkeypatch):
    async def fake_catalogue():
        return {"models": [
            {"id": "deepseek-flash", "context_window": 1_048_576},
            {"id": "deepseek-v4-pro", "context_window": 1_048_576},
        ], "from_provider": True}

    monkeypatch.setattr(llm, "model_catalogue", fake_catalogue)
    monkeypatch.setattr(llm, "active_model", lambda: "deepseek-flash")
    assert llm.active_context_window() == 1_048_576


def test_an_unknown_model_falls_back_conservatively(monkeypatch):
    async def empty_catalogue():
        return {"models": [], "from_provider": False}

    monkeypatch.setattr(llm, "model_catalogue", empty_catalogue)
    monkeypatch.setattr(llm, "active_model", lambda: "some-model")
    assert llm.active_context_window() == llm.FALLBACK_CONTEXT_WINDOW
    # Small enough that a prompt built on it is never rejected for length.
    assert llm.FALLBACK_CONTEXT_WINDOW <= 8192


def test_max_output_leaves_room_inside_the_window(monkeypatch):
    async def small_catalogue():
        return {"models": [{"id": "tiny", "context_window": 2000}], "from_provider": True}

    monkeypatch.setattr(llm, "model_catalogue", small_catalogue)
    monkeypatch.setattr(llm, "active_model", lambda: "tiny")
    out = llm.active_max_output()
    # A quarter of a 2k window is 500; the prompt keeps the rest.
    assert 0 < out <= 2000 // 4
    assert out >= 256, "the reply must not be squeezed to nothing"


def test_context_budget_grows_with_the_window(monkeypatch):
    """A big window must yield a much larger prompt than a small one."""
    async def big():
        return {"models": [{"id": "big", "context_window": 1_000_000}], "from_provider": True}

    async def small():
        return {"models": [{"id": "small", "context_window": 8000}], "from_provider": True}

    monkeypatch.setattr(llm, "active_model", lambda: "big")
    monkeypatch.setattr(llm, "model_catalogue", big)
    big_budget = chat._context_char_budget()

    llm._catalogue_cache = {"at": 0.0, "models": {}}
    monkeypatch.setattr(llm, "active_model", lambda: "small")
    monkeypatch.setattr(llm, "model_catalogue", small)
    small_budget = chat._context_char_budget()

    assert big_budget > small_budget * 10
    # Never so small that the old 300-char summary would look generous.
    assert small_budget > 3000


def test_entries_keep_their_full_text_in_a_big_window(client, monkeypatch):
    """A long entry must not be cut to 500 characters when the window is 1M."""
    body = "A" * 4000
    client.post("/api/entries", json={"title": "Long study", "content": body,
                                      "tags": "study"})

    async def big():
        return {"models": [{"id": "big", "context_window": 1_000_000}], "from_provider": True}

    monkeypatch.setattr(llm, "model_catalogue", big)
    monkeypatch.setattr(llm, "active_model", lambda: "big")

    ctx = chat._build_context("Long study")
    assert ctx, "the entry must be found"
    longest = max(len(c["content"]) for c in ctx)
    assert longest > 500, "content was still cut to the old 500-char cap"


def test_the_budget_is_respected(client, monkeypatch):
    """Whatever the window, the built context stays under the character budget."""
    for i in range(30):
        client.post("/api/entries", json={
            "title": f"Study {i}", "content": "x" * 3000, "tags": "study",
        })

    async def modest():
        return {"models": [{"id": "m", "context_window": 16_000}], "from_provider": True}

    monkeypatch.setattr(llm, "model_catalogue", modest)
    monkeypatch.setattr(llm, "active_model", lambda: "m")

    ctx = chat._build_context("Study")
    total = sum(len(c["content"]) + len(c["title"]) for c in ctx)
    assert total <= chat._context_char_budget()


def test_the_number_of_entries_is_capped(client, monkeypatch):
    """The character budget alone did not bound the cost.

    Once the whole library fit inside the budget, every message carried all 137
    entries — ~19 800 tokens for "say ok". The entry cap is what actually limits
    the bill, so it is pinned here.
    """
    for i in range(40):
        client.post("/api/entries", json={
            "title": f"Study {i}", "content": "text " * 200, "tags": "study",
        })

    async def big():
        return {"models": [{"id": "big", "context_window": 1_000_000}], "from_provider": True}

    monkeypatch.setattr(llm, "model_catalogue", big)
    monkeypatch.setattr(llm, "active_model", lambda: "big")

    ctx = chat._build_context("Study")
    assert len(ctx) <= chat.MAX_CONTEXT_ENTRIES, \
        f"{len(ctx)} entries sent; the cap is {chat.MAX_CONTEXT_ENTRIES}"


def test_the_cap_still_carries_a_real_prompt(client, monkeypatch):
    """Cheap must not mean empty: a capped context still holds many entries."""
    for i in range(12):
        client.post("/api/entries", json={
            "title": f"Meditation study {i}", "content": "mindfulness " * 40, "tags": "study",
        })

    async def big():
        return {"models": [{"id": "big", "context_window": 1_000_000}], "from_provider": True}

    monkeypatch.setattr(llm, "model_catalogue", big)
    monkeypatch.setattr(llm, "active_model", lambda: "big")

    ctx = chat._build_context("meditation")
    assert 5 <= len(ctx) <= chat.MAX_CONTEXT_ENTRIES
    assert sum(len(c["content"]) for c in ctx) > 1000


def test_prompt_does_not_cut_content_again(client, monkeypatch):
    """The system prompt used to truncate every entry to 300 chars."""
    client.post("/api/entries", json={"title": "Deep entry", "content": "B" * 3000})

    async def big():
        return {"models": [{"id": "big", "context_window": 1_000_000}], "from_provider": True}

    monkeypatch.setattr(llm, "model_catalogue", big)
    monkeypatch.setattr(llm, "active_model", lambda: "big")

    ctx = chat._build_context("Deep entry")
    prompt = chat._build_system_prompt(ctx, "deepseek", "big")
    assert "B" * 1500 in prompt, "content is still truncated inside the prompt"
