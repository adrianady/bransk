"""The model is chosen from the UI and remembered, not fixed in .env.

DeepSeek's own list is the only honest source of what exists (deepseek-chat is
already an alias the API resolves to deepseek-flash), so the app reads it
instead of hard-coding a menu. These tests pin the contract: the list comes
from the provider, a choice persists across restarts, a typo is refused with
the real options named, and an unreachable provider never breaks generation.
"""
import pytest
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


FAKE_LIST = {
    "models": [
        {"id": "deepseek-flash", "name": "DeepSeek-V4.1-Flash",
         "context_window": 1048576, "input_modalities": ["text", "image"]},
        {"id": "deepseek-v4-pro", "name": "DeepSeek-V4-Pro",
         "context_window": 1048576, "input_modalities": ["text"]},
    ],
    "active": "deepseek-chat",
    "from_provider": True,
    "active_listed": False,
}


def _fake_catalogue(result):
    async def fake():
        return dict(result)

    return fake


def test_model_list_comes_from_the_provider(client, monkeypatch):
    import app.routers.ai as ai

    monkeypatch.setattr(ai, "model_catalogue", _fake_catalogue(FAKE_LIST))
    body = client.get("/api/ai/models").json()

    assert body["from_provider"] is True
    assert {m["id"] for m in body["models"]} == {"deepseek-flash", "deepseek-v4-pro"}
    assert body["env_default"], "the screen must show what .env says as the fallback"


def test_choosing_a_model_persists(client, monkeypatch):
    import app.routers.ai as ai
    from app.llm import active_model

    monkeypatch.setattr(ai, "model_catalogue", _fake_catalogue(FAKE_LIST))
    res = client.post("/api/ai/model", json={"model_id": "deepseek-v4-pro"})
    assert res.status_code == 200
    assert res.json()["active"] == "deepseek-v4-pro"
    # Read back through the same function generation uses.
    assert active_model() == "deepseek-v4-pro"
    assert client.get("/api/ai/status").json()["deepseek_model"] == "deepseek-v4-pro"


def test_a_typo_is_refused_with_the_real_options(client, monkeypatch):
    import app.routers.ai as ai

    monkeypatch.setattr(ai, "model_catalogue", _fake_catalogue(FAKE_LIST))
    res = client.post("/api/ai/model", json={"model_id": "gpt-9-turbo"})
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert "gpt-9-turbo" in detail
    assert "deepseek-flash" in detail and "deepseek-v4-pro" in detail


def test_an_empty_choice_returns_to_the_env_default(client, monkeypatch):
    import app.routers.ai as ai
    from app.llm import DEEPSEEK_MODEL, active_model

    monkeypatch.setattr(ai, "model_catalogue", _fake_catalogue(FAKE_LIST))
    client.post("/api/ai/model", json={"model_id": "deepseek-v4-pro"})
    assert active_model() == "deepseek-v4-pro"

    body = client.post("/api/ai/model", json={"model_id": ""}).json()
    assert body["cleared"] is True
    assert active_model() == DEEPSEEK_MODEL


def test_offline_catalogue_still_lets_the_app_run(client, monkeypatch):
    """No provider list: the app must keep the current model and say why."""
    import app.routers.ai as ai

    monkeypatch.setattr(ai, "model_catalogue", _fake_catalogue({
        "models": [], "active": "deepseek-chat", "from_provider": False,
        "note": "Nu am putut citi lista de modele de la provider.",
    }))
    body = client.get("/api/ai/models").json()
    assert body["from_provider"] is False
    assert body["note"]
    # Without a readable list a manual id is accepted: nothing to validate it against.
    assert client.post("/api/ai/model", json={"model_id": "deepseek-v4-pro"}).status_code == 200


def test_a_broken_settings_table_never_breaks_generation(monkeypatch):
    """active_model() falls back to .env if the DB is unusable."""
    from app.llm import DEEPSEEK_MODEL, active_model

    def boom():
        raise RuntimeError("no db")

    monkeypatch.setattr("app.db.get_db", boom)
    assert active_model() == DEEPSEEK_MODEL
